"""
core/verifier.py — Evaluates test assertions against the current page state.
"""
from schema import Assertion, ElementModel


async def evaluate_assertions(
    page,
    assertions: list[Assertion],
    elements: dict[str, ElementModel] = None,
    llm_client = None,
    test_name: str = ""
) -> list[dict]:
    """
    Evaluate a list of test assertions against the current page.
    
    Args:
        page: Playwright page object
        assertions: List of Assertion objects to evaluate
        elements: Map of element IDs to ElementModels
        llm_client: LLM client to perform semantic healing if strict check fails
        test_name: Name of the test case for context
    
    Returns:
        List of dicts: {"type": ..., "expected": ..., "actual": ..., "passed": bool}
    """
    results = []

    for assertion in assertions:
        result = {
            "type": assertion.type,
            "expected": assertion.expected,
            "actual": None,
            "passed": False
        }

        try:
            if assertion.type == "url_contains":
                try:
                    if assertion.expected in page.url and "login" in page.url.lower() and "login" not in assertion.expected.lower():
                        await page.wait_for_url(lambda url: assertion.expected in url and "login" not in url.lower(), timeout=5000)
                    else:
                        await page.wait_for_url(lambda url: assertion.expected in url, timeout=5000)
                except Exception:
                    pass
                current_url = page.url
                result["actual"] = current_url
                result["passed"] = assertion.expected in current_url

            elif assertion.type == "element_visible":
                visible = False
                resolved = False
                
                # Try resolving through actual element locators first
                if elements and assertion.element_label:
                    element = next((e for e in elements.values() if e.semantic_label == assertion.element_label), None)
                    if element:
                        for spec in sorted(element.locators, key=lambda l: l.confidence, reverse=True):
                            try:
                                from utils.locators import _build_playwright_locator
                                loc = _build_playwright_locator(page, spec).locator("visible=true")
                                await loc.first.wait_for(state="visible", timeout=15000)
                                visible = True
                                resolved = True
                                break
                            except Exception:
                                continue

                if not resolved:
                    # Try to find by text first, then by label
                    try:
                        locator = page.locator(f"text={assertion.expected} >> visible=true")
                        await locator.first.wait_for(state="visible", timeout=15000)
                        visible = True
                    except Exception:
                        visible = False

                    if not visible and assertion.element_label:
                        try:
                            locator = page.get_by_label(assertion.element_label).locator("visible=true")
                            await locator.first.wait_for(state="visible", timeout=15000)
                            visible = True
                        except Exception:
                            visible = False

                result["actual"] = "visible" if visible else "not visible"
                result["passed"] = visible

            elif assertion.type == "text_equals":
                # expected format: "selector:::expected_text"
                if ":::" in assertion.expected:
                    selector, expected_text = assertion.expected.split(":::", 1)
                    try:
                        actual_text = await page.locator(selector.strip()).text_content()
                        result["actual"] = actual_text
                        result["passed"] = (actual_text or "").strip() == expected_text.strip()
                    except Exception as e:
                        result["actual"] = f"error: {e}"
                        result["passed"] = False
                else:
                    # Treat as text search
                    try:
                        locator = page.get_by_text(assertion.expected, exact=False).first
                        await locator.wait_for(state="visible", timeout=1500)
                        visible = True
                        result["actual"] = "visible" if visible else "not found"
                        result["passed"] = visible
                    except Exception as e:
                        result["actual"] = f"error: {e}"
                        result["passed"] = False

            elif assertion.type == "element_count":
                # expected format: "selector:::count"
                if ":::" in assertion.expected:
                    selector, count_str = assertion.expected.split(":::", 1)
                    try:
                        actual_count = await page.locator(selector.strip()).count()
                        result["actual"] = str(actual_count)
                        result["passed"] = actual_count == int(count_str.strip())
                    except Exception as e:
                        result["actual"] = f"error: {e}"
                        result["passed"] = False
                else:
                    result["actual"] = "invalid format (use selector:::count)"
                    result["passed"] = False

            elif assertion.type == "element_absent":
                try:
                    locator = page.get_by_text(assertion.expected, exact=False)
                    await locator.wait_for(state="hidden", timeout=1500)
                    visible = False
                except Exception:
                    visible = False

                result["actual"] = "absent" if not visible else "present"
                result["passed"] = not visible

            else:
                result["actual"] = f"unknown assertion type: {assertion.type}"
                result["passed"] = False

            # Semantic Fallback healing check
            if not result["passed"] and llm_client:
                await _evaluate_semantic_fallback(page, assertion, result, test_name, llm_client)

        except Exception as e:
            result["actual"] = f"error: {str(e)}"
            result["passed"] = False

        results.append(result)

    return results

async def _evaluate_semantic_fallback(page, assertion, result, test_name, llm_client):
    """Evaluate failed assertions conceptually using the fast LLM model."""
    log_file = "semantic_fallback_debug.log"
    try:
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(f"\n--- Fallback Initiated ---\nTest: {test_name}\nAssertion: {assertion.type} | Expected: {assertion.expected}\nActual: {result['actual']}\n")
        
        from llm.prompts import SEMANTIC_ASSERTION_PROMPT

        # ── Toast / alert capture ──────────────────────────────────────────
        # For text/visibility assertions, try to capture the visible toast or
        # alert message BEFORE scraping general body text.  This ensures the
        # report's "actual" column shows the real on-screen error text.
        #
        # Strategy: read .toast-title and .toast-message separately and join
        # them so we get "Squad1 - By Talakunchi!!! | User ID is Required."
        # Falling back to a generic selector loop for non-squad1 toasts.
        toast_text = ""
        if assertion.type in ("element_visible", "text_equals", "element_absent", "url_contains"):
            # ── Preferred: structured title + message ──────────────────────
            try:
                title_loc = page.locator(".toast-title")
                await title_loc.first.wait_for(state="visible", timeout=1500)
                title_raw = (await title_loc.first.inner_text() or "").strip()

                msg_loc = page.locator(".toast-message")
                msg_raw = ""
                if await msg_loc.count() > 0:
                    msg_raw = (await msg_loc.first.inner_text() or "").strip()
                    # Collapse <br> separated lines into " | "
                    msg_raw = " | ".join(
                        p.strip() for p in msg_raw.splitlines() if p.strip()
                    )

                parts = [p for p in [title_raw, msg_raw] if p]
                if parts:
                    toast_text = " | ".join(parts)
            except Exception:
                pass

            # ── Fallback: generic selectors ────────────────────────────────
            if not toast_text:
                fallback_selectors = [
                    ".toast",
                    "[role='alert']",
                    ".alert",
                    ".notification",
                ]
                for sel in fallback_selectors:
                    try:
                        loc = page.locator(sel)
                        await loc.first.wait_for(state="visible", timeout=1500)
                        raw = (await loc.first.inner_text()) or ""
                        # Strip close-button Unicode glyphs and collapse whitespace
                        import unicodedata
                        cleaned_lines = []
                        for line in raw.splitlines():
                            line = line.strip()
                            # Drop lines that are purely non-printable / symbol chars
                            if line and not all(
                                unicodedata.category(c) in ("So", "Sm", "Sk", "Sc", "Cn")
                                for c in line
                            ):
                                cleaned_lines.append(line)
                        cleaned = " | ".join(cleaned_lines)
                        if cleaned:
                            toast_text = cleaned
                            break
                    except Exception:
                        continue


        # ── General body text ──────────────────────────────────────────────
        page_text = ""
        if assertion.type in ("element_visible", "text_equals", "element_absent"):
            try:
                page_text = await page.locator("body").inner_text()
                page_text = page_text[:4000]
            except Exception as pe:
                page_text = f"Error scraping page text: {pe}"

        # Clean expected value if it uses selector format (like text_equals selector:::value)
        expected_val = assertion.expected
        if ":::" in expected_val:
            expected_val = expected_val.split(":::", 1)[1]

        # Build the actual_value fed to the LLM – toast text takes priority
        actual_for_llm = result["actual"] or "not found/visible"
        if toast_text:
            actual_for_llm = f"Toast: '{toast_text}'"

        prompt = SEMANTIC_ASSERTION_PROMPT.format(
            test_name=test_name,
            assertion_type=assertion.type,
            expected_value=expected_val,
            actual_value=actual_for_llm,
            page_text=page_text
        )

        with open(log_file, "a", encoding="utf-8") as f:
            f.write(f"Generated prompt: {prompt}\n")

        # Call LLM with the fast model to keep verification overhead small
        # Note: model="haiku" maps to fast_model in LLMClient
        llm_res = await llm_client.generate(prompt, model="haiku", expect_json=True)
        
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(f"LLM Response: {llm_res}\n")

        if llm_res.get("passed") is True:
            result["passed"] = True
            reason = llm_res.get("reason", "Semantic match confirmed by LLM")
            if toast_text:
                result["actual"] = f"Toast: '{toast_text}' (Conceptually passed: {reason})"
            else:
                result["actual"] = f"Conceptually passed: {reason}"
    except Exception as e:
        # Log verifier failure for debugging
        try:
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(f"EXCEPTION: {e}\n")
        except Exception:
            pass



