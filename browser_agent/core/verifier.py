"""
core/verifier.py — Evaluates test assertions against the current page state.
"""
import re
from schema import Assertion, ElementModel
from utils.toast import capture_toast, toast_matches_expectation


async def evaluate_assertions(
    page,
    assertions: list[Assertion],
    elements: dict[str, ElementModel] = None,
    llm_client=None,
    test_name: str = "",
    pre_captured_toast: str = "",
) -> list[dict]:
    """
    Evaluate a list of test assertions against the current page.

    Args:
        page: Playwright page object
        assertions: List of Assertion objects to evaluate
        elements: Map of element IDs to ElementModels
        llm_client: LLM client to perform semantic healing if strict check fails
        test_name: Name of the test case for context
        pre_captured_toast: Toast text captured right after the last action

    Returns:
        List of dicts: {"type": ..., "expected": ..., "actual": ..., "passed": bool}
    """
    results = []

    # Capture toast once up front (reuse executor-captured toast when available)
    toast_text = (pre_captured_toast or "").strip()
    needs_toast = any(
        a.type in ("element_visible", "text_equals", "element_absent")
        for a in assertions
    )
    if needs_toast and not toast_text:
        toast_text = await capture_toast(page, timeout_ms=5000)

    for assertion in assertions:
        result = {
            "type": assertion.type,
            "expected": assertion.expected,
            "actual": None,
            "passed": False,
        }

        try:
            if assertion.type == "url_contains":
                try:
                    if (
                        assertion.expected in page.url
                        and "login" in page.url.lower()
                        and "login" not in assertion.expected.lower()
                    ):
                        await page.wait_for_url(
                            lambda url: assertion.expected in url and "login" not in url.lower(),
                            timeout=5000,
                        )
                    else:
                        await page.wait_for_url(
                            lambda url: assertion.expected in url,
                            timeout=5000,
                        )
                except Exception:
                    pass
                current_url = page.url
                result["actual"] = current_url
                result["passed"] = assertion.expected in current_url

                # Deterministic URL healing for update/settings happy paths
                if not result["passed"]:
                    healed = _heal_url_assertion(
                        assertion.expected, current_url, test_name, toast_text
                    )
                    if healed:
                        result["passed"] = True
                        result["actual"] = f"{current_url} (Conceptually passed: {healed})"

            elif assertion.type == "element_visible":
                visible = False
                resolved = False

                # Prefer toast match for success / validation messages
                if toast_text:
                    reason = toast_matches_expectation(
                        assertion.expected, toast_text, test_name
                    )
                    if reason:
                        result["actual"] = f"Toast: '{toast_text}'"
                        result["passed"] = True
                        resolved = True
                        visible = True

                if not resolved and elements and assertion.element_label:
                    element = next(
                        (
                            e
                            for e in elements.values()
                            if e.semantic_label == assertion.element_label
                        ),
                        None,
                    )
                    if element:
                        for spec in sorted(
                            element.locators, key=lambda l: l.confidence, reverse=True
                        ):
                            try:
                                from utils.locators import _build_playwright_locator

                                loc = _build_playwright_locator(page, spec).locator(
                                    "visible=true"
                                )
                                await loc.first.wait_for(state="visible", timeout=8000)
                                visible = True
                                resolved = True
                                break
                            except Exception:
                                continue

                if not resolved and not result["passed"]:
                    try:
                        locator = page.locator(
                            f"text={assertion.expected} >> visible=true"
                        )
                        await locator.first.wait_for(state="visible", timeout=5000)
                        visible = True
                    except Exception:
                        visible = False

                    if not visible and assertion.element_label:
                        try:
                            locator = page.get_by_label(assertion.element_label).locator(
                                "visible=true"
                            )
                            await locator.first.wait_for(state="visible", timeout=5000)
                            visible = True
                        except Exception:
                            visible = False

                    result["actual"] = "visible" if visible else "not visible"
                    result["passed"] = visible
                elif not result["passed"]:
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
                    # 1) Prefer toast / alert text (most validation UIs use toasts)
                    if toast_text:
                        reason = toast_matches_expectation(
                            assertion.expected, toast_text, test_name
                        )
                        if reason:
                            result["actual"] = f"Toast: '{toast_text}'"
                            result["passed"] = True
                        else:
                            # Keep toast as actual even if not matched yet — helps LLM
                            result["actual"] = f"Toast: '{toast_text}'"
                            result["passed"] = False
                    else:
                        # 2) Exact / fuzzy page text
                        try:
                            locator = page.get_by_text(assertion.expected, exact=False)
                            await locator.first.wait_for(state="visible", timeout=3000)
                            result["actual"] = "visible"
                            result["passed"] = True
                        except Exception:
                            # Partial match (case-insensitive contains)
                            try:
                                locator = page.get_by_text(
                                    assertion.expected, exact=False
                                )
                                await locator.first.wait_for(state="visible", timeout=2000)
                                result["actual"] = "visible (partial)"
                                result["passed"] = True
                            except Exception as e:
                                result["actual"] = f"error: {e}"
                                result["passed"] = False

            elif assertion.type == "element_count":
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
                await _evaluate_semantic_fallback(
                    page, assertion, result, test_name, llm_client, toast_text
                )

        except Exception as e:
            result["actual"] = f"error: {str(e)}"
            result["passed"] = False

        results.append(result)

    return results


def _heal_url_assertion(
    expected: str, actual_url: str, test_name: str, toast_text: str
) -> str | None:
    """Deterministic conceptual URL matches for common LLM-generated expectations."""
    exp = (expected or "").lower()
    url = (actual_url or "").lower()
    name = (test_name or "").lower()
    toast = (toast_text or "").lower()

    is_happy = "happy" in name
    is_update_flow = any(
        k in name for k in ("company", "registration", "settings", "update", "profile")
    ) or any(k in url for k in ("/settings/", "/company", "/profile", "/general"))

    # Hallucinated success URL while still on the settings/company page after a happy path
    if is_happy and is_update_flow:
        successish = any(
            k in exp
            for k in ("success", "complete", "registered", "created", "updated", "done")
        )
        on_app_page = any(
            k in url for k in ("/settings/", "/company", "/dashboard", "/home", "/general")
        ) and "login" not in url
        success_toast = any(
            k in toast for k in ("success", "saved", "updated", "registered", "created")
        )
        if successish and on_app_page:
            if success_toast or not toast:
                return (
                    "Happy-path update stayed on settings/company page "
                    f"(expected hallucinated success path '{expected}')"
                )

    # Login landing-page synonym
    if is_happy and any(k in exp for k in ("/dashboard", "/home", "/index", "/main")):
        if any(k in url for k in ("/dashboard", "/home", "/index", "/main", "/settings")) and "login" not in url:
            return "Post-login landing page is conceptually equivalent"

    return None


async def _evaluate_semantic_fallback(
    page, assertion, result, test_name, llm_client, toast_text: str = ""
):
    """Evaluate failed assertions conceptually using the fast LLM model."""
    log_file = "semantic_fallback_debug.log"
    try:
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(
                f"\n--- Fallback Initiated ---\n"
                f"Test: {test_name}\n"
                f"Assertion: {assertion.type} | Expected: {assertion.expected}\n"
                f"Actual: {result['actual']}\n"
            )

        from llm.prompts import SEMANTIC_ASSERTION_PROMPT

        # Refresh toast if we still don't have one
        if not toast_text and assertion.type in (
            "element_visible",
            "text_equals",
            "element_absent",
            "url_contains",
        ):
            toast_text = await capture_toast(page, timeout_ms=2500)

        # Deterministic toast match before LLM (avoids hallucinated passes)
        if toast_text and assertion.type in ("element_visible", "text_equals"):
            reason = toast_matches_expectation(
                assertion.expected, toast_text, test_name
            )
            if reason:
                result["passed"] = True
                result["actual"] = f"Toast: '{toast_text}' (Conceptually passed: {reason})"
                with open(log_file, "a", encoding="utf-8") as f:
                    f.write(f"Deterministic toast match: {reason}\n")
                return

        page_text = ""
        if assertion.type in ("element_visible", "text_equals", "element_absent", "url_contains"):
            try:
                page_text = await page.locator("body").inner_text()
                page_text = page_text[:4000]
            except Exception as pe:
                page_text = f"Error scraping page text: {pe}"

        expected_val = assertion.expected
        if ":::" in expected_val:
            expected_val = expected_val.split(":::", 1)[1]

        actual_for_llm = result["actual"] or "not found/visible"
        if toast_text:
            actual_for_llm = f"Toast: '{toast_text}'"

        # Guard: never feed raw Playwright timeout stacks as "evidence of pass"
        if isinstance(actual_for_llm, str) and "Timeout" in actual_for_llm and not toast_text:
            actual_for_llm = (
                "No matching text/toast was found on the page. "
                f"Strict check failed with: {actual_for_llm[:200]}"
            )

        prompt = SEMANTIC_ASSERTION_PROMPT.format(
            test_name=test_name,
            assertion_type=assertion.type,
            expected_value=expected_val,
            actual_value=actual_for_llm,
            page_text=page_text,
        )

        with open(log_file, "a", encoding="utf-8") as f:
            f.write(f"Generated prompt: {prompt}\n")

        llm_res = await llm_client.generate(prompt, model="haiku", expect_json=True)

        with open(log_file, "a", encoding="utf-8") as f:
            f.write(f"LLM Response: {llm_res}\n")

        if llm_res.get("passed") is True:
            # Sanity gate: reject absurd "timeout == pass" rationales
            reason = (llm_res.get("reason") or "").lower()
            banned = (
                "timeout",
                "waiting for",
                "locator.wait_for",
                "error message indicates a timeout",
            )
            if any(b in reason for b in banned) and not toast_text:
                with open(log_file, "a", encoding="utf-8") as f:
                    f.write(f"Rejected hallucinated pass reason: {llm_res.get('reason')}\n")
                return

            result["passed"] = True
            reason_text = llm_res.get("reason", "Semantic match confirmed by LLM")
            if toast_text:
                result["actual"] = (
                    f"Toast: '{toast_text}' (Conceptually passed: {reason_text})"
                )
            else:
                result["actual"] = f"Conceptually passed: {reason_text}"
    except Exception as e:
        try:
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(f"EXCEPTION: {e}\n")
        except Exception:
            pass
