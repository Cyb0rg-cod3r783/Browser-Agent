"""
utils/toast.py — Capture toast / alert / validation messages from the page.
"""
import re
import unicodedata
from typing import Optional


# Prefer structured toastr/squad1 toasts, then generic alerts.
TOAST_SELECTORS = [
    ".toast-message",
    ".toast-title",
    "#toast-container .toast",
    ".toast",
    "[role='alert']",
    ".alert-danger",
    ".alert-error",
    ".alert-warning",
    ".alert-success",
    ".alert",
    ".notification",
    ".MuiAlert-message",
    ".ant-message-notice-content",
    ".ant-notification-notice-message",
    ".swal2-html-container",
    ".swal2-title",
]


async def capture_toast(page, timeout_ms: int = 5000) -> str:
    """
    Wait briefly for a toast/alert and return its cleaned text.
    Returns "" if nothing appears.
    """
    # Structured title + message (toastr / squad1)
    try:
        title_loc = page.locator(".toast-title, #toast-container .toast-title")
        msg_loc = page.locator(".toast-message, #toast-container .toast-message")
        container = page.locator("#toast-container .toast, .toast.toast-error, .toast.toast-success, .toast.toast-warning, .toast.toast-info")

        # Wait for any toast container to show
        try:
            await container.first.wait_for(state="visible", timeout=timeout_ms)
        except Exception:
            # Fall through to generic selectors
            pass
        else:
            title_raw = ""
            msg_raw = ""
            try:
                if await title_loc.count() > 0:
                    title_raw = (await title_loc.first.inner_text() or "").strip()
            except Exception:
                pass
            try:
                if await msg_loc.count() > 0:
                    msg_raw = (await msg_loc.first.inner_text() or "").strip()
                    msg_raw = " | ".join(p.strip() for p in msg_raw.splitlines() if p.strip())
            except Exception:
                pass
            parts = [p for p in [title_raw, msg_raw] if p]
            if parts:
                return " | ".join(parts)
            # Container visible but no title/message — use container text
            raw = (await container.first.inner_text() or "").strip()
            cleaned = _clean_toast_text(raw)
            if cleaned:
                return cleaned
    except Exception:
        pass

    # Generic selectors
    for sel in TOAST_SELECTORS:
        try:
            loc = page.locator(sel)
            await loc.first.wait_for(state="visible", timeout=min(1500, timeout_ms))
            raw = (await loc.first.inner_text()) or ""
            cleaned = _clean_toast_text(raw)
            if cleaned:
                return cleaned
        except Exception:
            continue

    return ""


def _clean_toast_text(raw: str) -> str:
    cleaned_lines = []
    for line in (raw or "").splitlines():
        line = line.strip()
        if not line:
            continue
        # Drop pure symbol / close-button glyphs
        if all(unicodedata.category(c) in ("So", "Sm", "Sk", "Sc", "Cn", "P") for c in line):
            continue
        if line in ("×", "x", "X", "✕", "✖"):
            continue
        cleaned_lines.append(line)
    return " | ".join(cleaned_lines)


def toast_matches_expectation(expected: str, toast_text: str, test_name: str = "") -> Optional[str]:
    """
    Deterministic fuzzy match between an expected validation message and a toast.
    Returns a reason string if matched, else None.
    """
    if not toast_text or not expected:
        return None

    exp = expected.lower().strip()
    toast = toast_text.lower().strip()
    name = (test_name or "").lower()

    # Exact / substring
    if exp in toast or toast in exp:
        return f"Toast contains expected text: '{toast_text}'"

    # Token overlap on meaningful words
    stop = {
        "the", "a", "an", "is", "are", "be", "to", "of", "and", "or", "for",
        "must", "please", "enter", "field", "input", "value", "format",
    }
    exp_tokens = {t for t in re.findall(r"[a-z0-9]+", exp) if len(t) > 2 and t not in stop}
    toast_tokens = {t for t in re.findall(r"[a-z0-9]+", toast) if len(t) > 2 and t not in stop}

    # Field-oriented keywords from test name / expected
    field_hints = set()
    for source in (exp, name):
        for hint in (
            "license", "website", "domain", "email", "password", "company",
            "code", "name", "contact", "phone", "mobile", "search", "query",
            "person", "url",
        ):
            if hint in source:
                field_hints.add(hint)

    validation_hints = {
        "required", "invalid", "valid", "exceed", "maximum", "minimum",
        "length", "empty", "error", "fail", "wrong", "numeric", "number",
        "format", "character", "long", "short",
    }

    has_field = bool(field_hints & toast_tokens) or any(h in toast for h in field_hints)
    has_validation = bool(validation_hints & toast_tokens) or any(
        v in toast for v in ("please", "required", "invalid", "valid", "error", "enter")
    )

    # Strong signal: toast mentions the same field AND looks like a validation message
    if has_field and has_validation:
        return f"Toast validation for same field: '{toast_text}'"

    # Moderate: significant token overlap
    if exp_tokens:
        overlap = exp_tokens & toast_tokens
        if len(overlap) >= 2 or (len(overlap) == 1 and has_validation and len(exp_tokens) <= 3):
            return f"Toast conceptually matches expected ('{toast_text}') via tokens {sorted(overlap)}"

    # Success toasts for happy-path success assertions
    success_words = {"success", "successful", "successfully", "saved", "updated", "registered", "created"}
    if any(w in exp for w in success_words) and any(w in toast for w in success_words):
        return f"Success toast matches expected outcome: '{toast_text}'"

    return None
