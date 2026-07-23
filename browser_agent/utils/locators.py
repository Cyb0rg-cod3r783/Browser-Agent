"""
utils/locators.py — Multi-strategy locator generation + resolution.
"""
import re
import json
from typing import Any, Optional

from playwright.async_api import Page, expect, Error as PlaywrightError

from schema import ElementModel, LocatorSpec


class ElementNotFoundError(Exception):
    """Raised when no locator strategy can find the element."""
    pass


def _is_auto_generated_id(id_value: str) -> bool:
    """
    Returns True if the ID appears to be auto-generated.
    Auto-generated IDs: purely numeric, UUID-like, react- or ember- prefixed.
    """
    if not id_value:
        return True
    patterns = [
        r'^[0-9]+$',           # purely numeric
        r'^[a-f0-9]{8}-',      # UUID-like
        r'^react-',            # React generated
        r'^ember',             # Ember generated
    ]
    return any(re.match(p, id_value) for p in patterns)


def generate_locators(element_attrs: dict) -> list[LocatorSpec]:
    """
    Given raw element attributes from the JS event capture,
    generate a ranked list of LocatorSpec objects in priority order.
    """
    locators: list[LocatorSpec] = []

    # 1. aria_label (confidence 0.95)
    aria_label = element_attrs.get("aria_label")
    if aria_label:
        locators.append(LocatorSpec(
            strategy="aria_label",
            value=aria_label,
            confidence=0.95
        ))

    # 2. placeholder (confidence 0.85)
    placeholder = element_attrs.get("placeholder")
    if placeholder:
        locators.append(LocatorSpec(
            strategy="placeholder",
            value=placeholder,
            confidence=0.85
        ))

    # 3. role+text (confidence 0.80) — button or link with text_content
    tag = (element_attrs.get("tag") or "").lower()
    text_content = (element_attrs.get("text_content") or "").strip()
    type_attr = element_attrs.get("type_attr") or ""
    if tag in ("button", "a") and text_content:
        role = "button" if tag == "button" else "link"
        locators.append(LocatorSpec(
            strategy="role",
            value=f"{role}:{text_content[:100]}",
            confidence=0.80
        ))

    # 4. id (confidence 0.70) — only if not auto-generated
    element_id = element_attrs.get("id") or ""
    if element_id and not _is_auto_generated_id(element_id):
        locators.append(LocatorSpec(
            strategy="id",
            value=element_id,
            confidence=0.70
        ))

    # 5. css_name (confidence 0.55) — input[name="X"] or button[type="submit"]
    name = element_attrs.get("name") or ""
    if name:
        locators.append(LocatorSpec(
            strategy="css_name",
            value=f'{tag}[name="{name}"]' if tag else f'[name="{name}"]',
            confidence=0.55
        ))
    elif tag == "button" and type_attr == "submit":
        locators.append(LocatorSpec(
            strategy="css_name",
            value='button[type="submit"]',
            confidence=0.55
        ))

    # 5b. password type (confidence 0.88) — input[type="password"] is unambiguous
    if type_attr == "password":
        locators.append(LocatorSpec(
            strategy="css_name",
            value='input[type="password"]',
            confidence=0.88
        ))

    # 6. xpath_text (confidence 0.40) — //button[contains(text(),"X")]
    if text_content and tag:
        safe_text = text_content[:50].replace('"', '\\"')
        locators.append(LocatorSpec(
            strategy="xpath_text",
            value=f'//{tag}[contains(text(),"{safe_text}")]',
            confidence=0.40
        ))

    # 7. chained_css_role (confidence 0.90) — page.locator('#outer').getByRole('role')
    chained_selector = element_attrs.get("chained_selector")
    chained_role = element_attrs.get("chained_role")
    if chained_selector and chained_role:
        chained_name = element_attrs.get("chained_name", "")
        chained_value = f"#{chained_selector}|{chained_role}|{chained_name or ''}"
        locators.append(LocatorSpec(
            strategy="chained_css_role",
            value=chained_value,
            confidence=0.90
        ))

    locators.sort(key=lambda l: l.confidence, reverse=True)
    return locators


def _build_playwright_locator(page: Page, spec: LocatorSpec):
    """Build a Playwright locator from a LocatorSpec."""
    strategy = spec.strategy
    value = spec.value

    nth = None
    if "::nth=" in value:
        value, nth_str = value.rsplit("::nth=", 1)
        try:
            nth = int(nth_str)
        except ValueError:
            nth = None

    if strategy == "aria_label":
        loc = page.get_by_label(value)
    elif strategy == "placeholder":
        loc = page.get_by_placeholder(value)
    elif strategy == "role":
        parts = value.split(":", 1)
        role = parts[0]
        name = parts[1] if len(parts) > 1 else None
        if name:
            loc = page.get_by_role(role, name=name)
        else:
            loc = page.get_by_role(role)
    elif strategy == "text":
        loc = page.get_by_text(value, exact=False)
    elif strategy == "label":
        loc = page.get_by_label(value)
    elif strategy == "id":
        loc = page.locator(f"#{value}")
    elif strategy in ("css_name", "xpath_text", "css"):
        loc = page.locator(value)
    elif strategy == "chained_css_role":
        parts = value.split("|", 2)
        outer_sel = parts[0]
        inner_role = parts[1]
        inner_name = parts[2] if len(parts) > 2 else ""
        container = page.locator(outer_sel)
        if inner_name:
            loc = container.get_by_role(inner_role, name=inner_name)
        else:
            loc = container.get_by_role(inner_role)
    else:
        loc = page.locator(value)

    if nth is not None:
        loc = loc.nth(nth)

    return loc


async def _is_fillable(locator) -> bool:
    """Return True if the locator points at an editable control."""
    try:
        tag = (await locator.evaluate("el => (el.tagName || '').toLowerCase()")).lower()
        if tag in ("input", "textarea", "select"):
            return True
        editable = await locator.evaluate(
            "el => !!(el.isContentEditable || el.getAttribute('contenteditable') === 'true' "
            "|| el.getAttribute('role') === 'textbox' || el.getAttribute('role') === 'searchbox')"
        )
        return bool(editable)
    except Exception:
        return False


async def _promote_to_fillable(page: Page, locator):
    """
    If locator resolved to a <label> or plain text node, try to find the
    associated input/textarea so fill() does not blow up.
    """
    try:
        tag = (await locator.evaluate("el => (el.tagName || '').toLowerCase()")).lower()
    except Exception:
        return locator

    if tag in ("input", "textarea", "select"):
        return locator

    # label[for] → #id
    if tag == "label":
        try:
            for_id = await locator.get_attribute("for")
            if for_id:
                candidate = page.locator(f"#{for_id}").first
                if await candidate.count() > 0:
                    return candidate
        except Exception:
            pass
        try:
            candidate = locator.locator("input, textarea, select").first
            if await candidate.count() > 0:
                return candidate
        except Exception:
            pass

    # Nearby input after a label/text node
    try:
        candidate = locator.locator("xpath=following::input[1] | following::textarea[1]").first
        if await candidate.count() > 0 and await candidate.is_visible():
            return candidate
    except Exception:
        pass

    return locator


def _heuristic_specs(element: ElementModel) -> list[LocatorSpec]:
    """
    Build extra locator candidates from the semantic label / observed values
    when stored locators fail (self-heal before calling the LLM).
    """
    label = (element.semantic_label or "").strip()
    specs: list[LocatorSpec] = []
    if not label:
        return specs

    # Common label variants (Company code ↔ Company Code *)
    variants = {
        label,
        label.rstrip(" *"),
        label.replace(" Input Field", "").strip(),
        label.replace(" field", "").strip(),
        label.replace(" Field", "").strip(),
    }
    # Title-case / lower variants
    more = set()
    for v in list(variants):
        more.add(v.title())
        more.add(v.lower())
        more.add(v.capitalize())
    variants |= more

    for v in variants:
        if not v:
            continue
        specs.append(LocatorSpec(strategy="placeholder", value=v, confidence=0.65))
        specs.append(LocatorSpec(strategy="aria_label", value=v, confidence=0.64))
        specs.append(LocatorSpec(strategy="label", value=v, confidence=0.63))
        specs.append(LocatorSpec(strategy="role", value=f"textbox:{v}", confidence=0.60))

    # Observed placeholder/value hints
    for ov in (element.observed_values or [])[:5]:
        if ov and len(ov) < 80:
            specs.append(LocatorSpec(strategy="placeholder", value=ov, confidence=0.58))

    return specs


async def _try_spec(
    page: Page,
    spec: LocatorSpec,
    timeout_ms: int,
    require_fillable: bool,
) -> Optional[Any]:
    """Try one locator spec; return locator or None."""
    try:
        loc = _build_playwright_locator(page, spec)
        first_loc = loc.first
        await expect(first_loc).to_be_visible(timeout=timeout_ms)
        if require_fillable:
            first_loc = await _promote_to_fillable(page, first_loc)
            if not await _is_fillable(first_loc):
                return None
            # Ensure still visible after promotion
            await expect(first_loc).to_be_visible(timeout=min(2000, timeout_ms))
        return first_loc
    except Exception:
        return None


async def resolve_locator(
    page: Page,
    element: ElementModel,
    llm_client,
    timeout_ms: int = 60000,
    require_fillable: bool = False,
) -> tuple[Any, str]:
    """
    Try each locator in element.locators sorted by confidence descending.
    Then try heuristic label-based locators. Finally fall back to LLM.
    Raises ElementNotFoundError if all fail.

    Returns: (playwright_locator, strategy_name)
    """
    sorted_locators = sorted(element.locators, key=lambda l: l.confidence, reverse=True)

    for spec in sorted_locators:
        loc = await _try_spec(page, spec, timeout_ms, require_fillable)
        if loc is not None:
            return (loc, spec.strategy)

    # Heuristic self-heal from semantic label (before expensive LLM call)
    for spec in _heuristic_specs(element):
        loc = await _try_spec(page, spec, min(timeout_ms, 4000), require_fillable)
        if loc is not None:
            return (loc, f"heuristic_{spec.strategy}")

    # LLM fallback
    try:
        from llm.prompts import LOCATOR_FALLBACK_PROMPT
        accessibility_tree = await page.accessibility.snapshot()
        # Keep tree payload bounded so the fast model stays reliable
        tree_json = json.dumps(accessibility_tree)
        if len(tree_json) > 12000:
            tree_json = tree_json[:12000] + "...(truncated)"

        prompt = LOCATOR_FALLBACK_PROMPT.format(
            semantic_label=element.semantic_label,
            accessibility_tree_json=tree_json,
        )
        llm_result = await llm_client.generate(prompt, model="haiku", expect_json=True)

        strategy = llm_result.get("strategy", "css")
        # Normalize aliases
        if strategy == "label":
            strategy = "aria_label"
        value = llm_result.get("value", "")
        role_name = llm_result.get("role_name", "")

        if strategy == "role":
            role = role_name or "textbox"
            fallback_spec = LocatorSpec(
                strategy="role",
                value=f"{role}:{value}" if value else role,
                confidence=0.5,
            )
        else:
            fallback_spec = LocatorSpec(strategy=strategy, value=value, confidence=0.5)

        loc = await _try_spec(page, fallback_spec, timeout_ms, require_fillable)
        if loc is not None:
            return (loc, "llm_fallback")

        # Last chance: if LLM returned plain text, try placeholder/label/textbox
        if value:
            for alt in (
                LocatorSpec(strategy="placeholder", value=value, confidence=0.4),
                LocatorSpec(strategy="aria_label", value=value, confidence=0.4),
                LocatorSpec(strategy="role", value=f"textbox:{value}", confidence=0.4),
            ):
                loc = await _try_spec(page, alt, min(timeout_ms, 3000), require_fillable)
                if loc is not None:
                    return (loc, "llm_fallback")

        raise ElementNotFoundError(
            f"LLM fallback locator not visible for '{element.semantic_label}'"
        )
    except ElementNotFoundError:
        raise
    except Exception as e:
        raise ElementNotFoundError(
            f"Could not find element '{element.semantic_label}': {e}"
        )
