"""
utils/locators.py — Multi-strategy locator generation + resolution.
"""
import re
import json
from typing import Any

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
    #    Emitted when the event carries a 'chained_selector' attribute from the parser.
    chained_selector = element_attrs.get("chained_selector")
    chained_role = element_attrs.get("chained_role")
    if chained_selector and chained_role:
        chained_name = element_attrs.get("chained_name", "")
        # Encode as  "#selector|role|name"  so the value stays a plain string
        chained_value = f"#{chained_selector}|{chained_role}|{chained_name or ''}"
        locators.append(LocatorSpec(
            strategy="chained_css_role",
            value=chained_value,
            confidence=0.90
        ))

    # Sort by confidence descending
    locators.sort(key=lambda l: l.confidence, reverse=True)
    return locators


def _build_playwright_locator(page: Page, spec: LocatorSpec):
    """Build a Playwright locator from a LocatorSpec."""
    strategy = spec.strategy
    value = spec.value

    # Strip ::nth=N suffix (used internally for disambiguation)
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
        # value format: "role:name"
        parts = value.split(":", 1)
        role = parts[0]
        name = parts[1] if len(parts) > 1 else None
        if name:
            loc = page.get_by_role(role, name=name)
        else:
            loc = page.get_by_role(role)
    elif strategy == "text":
        loc = page.get_by_text(value, exact=False)
    elif strategy == "id":
        loc = page.locator(f"#{value}")
    elif strategy in ("css_name", "xpath_text", "css"):
        loc = page.locator(value)
    elif strategy == "chained_css_role":
        # value format: "#selector|role|name"
        # Reconstruct: page.locator('#selector').getByRole('role', name='name')
        parts = value.split("|", 2)
        outer_sel = parts[0]          # e.g. "#dvaddbutton"
        inner_role = parts[1]         # e.g. "link"
        inner_name = parts[2] if len(parts) > 2 else ""
        container = page.locator(outer_sel)
        if inner_name:
            loc = container.get_by_role(inner_role, name=inner_name)
        else:
            loc = container.get_by_role(inner_role)
    else:
        loc = page.locator(value)

    # Apply .nth() if specified
    if nth is not None:
        loc = loc.nth(nth)

    return loc


async def resolve_locator(
    page: Page,
    element: ElementModel,
    llm_client,
    timeout_ms: int = 3000
) -> tuple[Any, str]:
    """
    Try each locator in element.locators sorted by confidence descending.
    Falls back to LLM if all fail. Raises ElementNotFoundError if LLM also fails.
    
    Returns: (playwright_locator, strategy_name)
    """
    # Sort by confidence (highest first)
    sorted_locators = sorted(element.locators, key=lambda l: l.confidence, reverse=True)

    for spec in sorted_locators:
        try:
            loc = _build_playwright_locator(page, spec)
            # Use .first to avoid Playwright strict mode errors when a locator matches multiple elements
            first_loc = loc.first
            await expect(first_loc).to_be_visible(timeout=timeout_ms)
            return (first_loc, spec.strategy)
        except Exception:
            continue

    # All locators failed — use LLM fallback
    try:
        from llm.prompts import LOCATOR_FALLBACK_PROMPT
        accessibility_tree = await page.accessibility.snapshot()
        prompt = LOCATOR_FALLBACK_PROMPT.format(
            semantic_label=element.semantic_label,
            accessibility_tree_json=json.dumps(accessibility_tree, indent=2)
        )
        llm_result = await llm_client.generate(prompt, model="haiku", expect_json=True)

        strategy = llm_result.get("strategy", "css")
        value = llm_result.get("value", "")
        role_name = llm_result.get("role_name", "")

        if strategy == "role" and role_name:
            fallback_spec = LocatorSpec(strategy="role", value=f"{role_name}:{value}", confidence=0.5)
        else:
            fallback_spec = LocatorSpec(strategy=strategy, value=value, confidence=0.5)

        loc = _build_playwright_locator(page, fallback_spec)
        await expect(loc).to_be_visible(timeout=timeout_ms)
        return (loc, "llm_fallback")
    except Exception as e:
        raise ElementNotFoundError(
            f"Could not find element '{element.semantic_label}': {e}"
        )
