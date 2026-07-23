"""
core/model_builder.py — Converts raw session events into structured ApplicationModel.
"""
import json
import re
import uuid
import asyncio
from datetime import datetime, date, timezone

from schema import (
    ApplicationModel, AppVersion, PageModel, ElementModel,
    UserFlow, FlowStep, ExpectedOutcome, LocatorSpec, ValidationRule
)
from utils.locators import generate_locators
from llm.prompts import ELEMENT_LABEL_PROMPT, FLOW_IDENTIFICATION_PROMPT


class ModelBuilder:
    async def build(
        self,
        session_data: dict,
        app_name: str,
        db,
        llm_client
    ) -> ApplicationModel:
        """
        Build a complete ApplicationModel from raw session data.
        
        Args:
            session_data: Dict returned by BrowserRecorder.stop_session()
            app_name: Name of the application being modeled
            db: Database instance for persistence
            llm_client: LLMClient for element labeling and flow analysis
        
        Returns:
            Complete ApplicationModel
        """
        base_url = session_data.get("start_url", "")
        events = self._preprocess_events(session_data.get("events", []))
        navigations = session_data.get("navigations", [])

        # 1. Create Application entry
        existing_app = await db.get_application_by_name(app_name)
        if existing_app:
            app_id = existing_app.id
        else:
            app_id = str(uuid.uuid4())

        # 2. Create AppVersion entry
        today = date.today().isoformat()
        version = AppVersion(
            id=str(uuid.uuid4()),
            app_id=app_id,
            label=f"v1_{today}",
            created_at=datetime.utcnow(),
            is_active=True
        )

        # 3. Build pages from navigation log
        pages: list[PageModel] = []
        elements: list[ElementModel] = []
        url_to_page: dict[str, PageModel] = {}

        for nav in navigations:
            url = nav.get("url", "")
            if not url:
                continue

            # Deduplicate by URL
            if url in url_to_page:
                continue

            # Generate a URL pattern (regex-compatible)
            url_pattern = self._url_to_pattern(url, base_url)

            page = PageModel(
                id=str(uuid.uuid4()),
                version_id=version.id,
                url_pattern=url_pattern,
                title=nav.get("title") or "",
                purpose="",  # Will be set later
                accessibility_snapshot=nav.get("accessibility_snapshot") or {}
            )
            pages.append(page)
            url_to_page[url] = page

        # 4. Process elements from events — associate with page by URL
        # Build a map from URL to events
        # We'll use the navigation order to determine which URL was active
        nav_urls = [n.get("url", "") for n in navigations]

        for i, nav in enumerate(navigations):
            nav_url = nav.get("url", "")
            page = url_to_page.get(nav_url)
            if not page:
                continue

            # Get events that happened before the next navigation
            next_nav_time = None
            if i + 1 < len(navigations):
                next_nav_time = navigations[i + 1].get("timestamp")

            nav_time = nav.get("timestamp")
            
            # Get relevant events for this page
            page_events = self._events_for_page(events, nav_time, next_nav_time)

            # Process each unique element on this page
            seen_elements: set[str] = set()
            for event in page_events:
                elem_key = self._element_key(event)
                if elem_key in seen_elements:
                    # Still capture observed values
                    for elem in elements:
                        if elem.page_id == page.id and self._matches_event(elem, event):
                            val = event.get("value")
                            if val and val not in elem.observed_values:
                                elem.observed_values.append(val)
                    continue
                seen_elements.add(elem_key)

                # Skip events with no meaningful identification
                if not any([
                    event.get("aria_label"), event.get("placeholder"),
                    event.get("id"), event.get("name"), event.get("text_content")
                ]):
                    continue

                # Generate locators
                locators = generate_locators(event)
                if not locators:
                    continue

                # Call LLM for element labeling
                element_type = self._classify_element_type(event)
                label_result = await llm_client.generate(
                    ELEMENT_LABEL_PROMPT.format(
                        tag=event.get("tag") or "",
                        input_type=event.get("type_attr") or "",
                        element_id=event.get("id") or "",
                        name=event.get("name") or "",
                        placeholder=event.get("placeholder") or "",
                        aria_label=event.get("aria_label") or "",
                        text_content=event.get("text_content") or ""
                    ),
                    model="haiku",
                    expect_json=True
                )

                semantic_label = label_result.get("semantic_label", elem_key)
                validation_rules_raw = label_result.get("validation_rules", [])
                validation_rules = []
                for vr in validation_rules_raw:
                    if isinstance(vr, dict):
                        validation_rules.append(ValidationRule(
                            rule=vr.get("rule", ""),
                            typical_error=vr.get("typical_error")
                        ))

                observed_values = []
                val = event.get("value")
                if val:
                    observed_values.append(val)

                element = ElementModel(
                    id=str(uuid.uuid4()),
                    page_id=page.id,
                    element_type=element_type,
                    semantic_label=semantic_label,
                    locators=locators,
                    validation_rules=validation_rules,
                    observed_values=observed_values
                )
                elements.append(element)

        # 5. Group ALL events into a single End-to-End flow
        flows: list[UserFlow] = []
        if events:
            start_url = navigations[0].get("url", base_url) if navigations else base_url
            page_titles = list({n.get("title", "") for n in navigations if n.get("title")})

            # Call LLM for flow identification (using up to 100 events to ensure we capture long flows)
            flow_result = await llm_client.generate(
                FLOW_IDENTIFICATION_PROMPT.format(
                    steps_json=json.dumps(events[:100], indent=2),
                    page_titles=json.dumps(page_titles)
                ),
                model="sonnet",
                expect_json=True
            )

            # Convert all events to FlowStep objects (pass None for page to allow matching across all pages)
            flow_steps = self._events_to_flow_steps(events, elements, None)

            expected_outcome_raw = flow_result.get("expected_outcome", {})
            expected_outcome = ExpectedOutcome(
                type=expected_outcome_raw.get("type", "navigation"),
                url_pattern=expected_outcome_raw.get("url_pattern"),
                text=expected_outcome_raw.get("text")
            )

            # Determine next URL as expected navigation target based on the last navigation
            if len(navigations) > 1:
                last_url = navigations[-1].get("url", "")
                if last_url and not expected_outcome.url_pattern:
                    expected_outcome.url_pattern = self._url_to_pattern(last_url, base_url)

            flow = UserFlow(
                id=str(uuid.uuid4()),
                version_id=version.id,
                name=flow_result.get("name", "End-to-End User Journey"),
                description=flow_result.get("description", "Complete recorded session flow"),
                start_url=start_url,
                steps=flow_steps,
                expected_outcome=expected_outcome
            )
            flows.append(flow)

        # Assemble and return ApplicationModel
        app = ApplicationModel(
            id=app_id,
            name=app_name,
            base_url=base_url,
            version=version,
            pages=pages,
            elements=elements,
            flows=flows
        )

        # Persist to DB
        await db.save_application(app)
        await db.save_version(version)
        if existing_app:
            await db.set_active_version(app_id, version.id)
            
        for page in pages:
            await db.save_page(page)
        for element in elements:
            await db.save_element(element)
        for flow in flows:
            await db.save_flow(flow)

        return app

    # ─── Codegen-based build path ─────────────────────────────────────────────

    async def build_from_codegen(
        self,
        parsed_steps: list[dict],
        app_name: str,
        db,
        llm_client
    ) -> "ApplicationModel":
        """
        Build a complete ApplicationModel from parsed Playwright codegen steps.

        This is the preferred path when recording via 'playwright codegen'
        because the locators it produces (getByRole, getByText, getByLabel) are
        significantly more resilient than those generated from raw HTML events.

        Args:
            parsed_steps: Output of core.codegen_parser.parse_playwright_js()
            app_name:     Name of the application being modeled
            db:           Database instance for persistence
            llm_client:   LLMClient for semantic labeling and flow analysis

        Returns:
            Complete ApplicationModel (also persisted to the database)
        """
        # 1. Create / reuse Application entry
        existing_app = await db.get_application_by_name(app_name)
        app_id = existing_app.id if existing_app else str(uuid.uuid4())

        # Derive base URL from the first navigate step
        base_url = ""
        for step in parsed_steps:
            if step["step_type"] == "navigate":
                base_url = step["url"]
                break

        # 2. Create AppVersion entry
        today = date.today().isoformat()
        version = AppVersion(
            id=str(uuid.uuid4()),
            app_id=app_id,
            label=f"v1_{today}",
            created_at=datetime.utcnow(),
            is_active=True
        )

        # 3. Build pages from navigate steps (deduplicated by URL)
        pages: list[PageModel] = []
        url_to_page: dict[str, PageModel] = {}

        for step in parsed_steps:
            if step["step_type"] != "navigate":
                continue
            url = step["url"]
            if url in url_to_page:
                continue
            url_pattern = self._url_to_pattern(url, base_url)
            page = PageModel(
                id=str(uuid.uuid4()),
                version_id=version.id,
                url_pattern=url_pattern,
                title="",
                purpose="",
                accessibility_snapshot={}
            )
            pages.append(page)
            url_to_page[url] = page

        # 4. Build elements from action steps
        elements: list[ElementModel] = []
        # Key used to deduplicate: (locator_type, primary_value, nth)
        seen_elem_keys: set[str] = set()

        current_url = base_url
        for step in parsed_steps:
            if step["step_type"] == "navigate":
                current_url = step["url"]
                continue

            # Skip non-interactable actions
            if step.get("action") in ("press",):
                continue

            page_obj = url_to_page.get(current_url)
            if not page_obj:
                # Create a catch-all page if needed
                if current_url not in url_to_page:
                    url_pattern = self._url_to_pattern(current_url, base_url)
                    page_obj = PageModel(
                        id=str(uuid.uuid4()),
                        version_id=version.id,
                        url_pattern=url_pattern,
                        title="",
                        purpose="",
                        accessibility_snapshot={}
                    )
                    pages.append(page_obj)
                    url_to_page[current_url] = page_obj

            page_obj = url_to_page[current_url]

            locators, element_type = self._codegen_step_to_locators(step)
            if not locators:
                continue

            # Dedup key: locator_type + primary value + nth
            elem_key = self._codegen_step_key(step)
            if elem_key in seen_elem_keys:
                # Update observed_values on existing element
                for elem in elements:
                    if self._codegen_step_matches_element(elem, step) and step.get("value"):
                        if step["value"] not in elem.observed_values:
                            elem.observed_values.append(step["value"])
                continue
            seen_elem_keys.add(elem_key)

            # Ask LLM for semantic label + validation rules
            display_name = step.get("name") or step.get("text") or step.get("selector") or ""
            role_name = step.get("role") or ""
            tag = self._role_to_html_tag(role_name)
            input_type = "text" if role_name == "textbox" else ""

            await asyncio.sleep(0.2)
            label_result = await llm_client.generate(
                ELEMENT_LABEL_PROMPT.format(
                    tag=tag,
                    input_type=input_type,
                    element_id=step.get("selector", ""),
                    name="",
                    placeholder=display_name,
                    aria_label=display_name,
                    text_content=display_name
                ),
                model="haiku",
                expect_json=True
            )

            semantic_label = label_result.get("semantic_label", display_name or elem_key)
            validation_rules = []
            for vr in label_result.get("validation_rules", []):
                if isinstance(vr, dict):
                    validation_rules.append(ValidationRule(
                        rule=vr.get("rule", ""),
                        typical_error=vr.get("typical_error")
                    ))

            element = ElementModel(
                id=str(uuid.uuid4()),
                page_id=page_obj.id,
                element_type=element_type,
                semantic_label=semantic_label,
                locators=locators,
                validation_rules=validation_rules,
                observed_values=[step["value"]] if step.get("value") else []
            )
            elements.append(element)

        # 5. Build flow steps (mapping each action step to its element)
        flow_steps: list[FlowStep] = []
        sequence = 1
        current_url = base_url

        for step in parsed_steps:
            if step["step_type"] == "navigate":
                current_url = step["url"]
                continue
            if step.get("action") in ("press",):
                continue

            matched_element: ElementModel | None = None
            for elem in elements:
                if self._codegen_step_matches_element(elem, step):
                    matched_element = elem
                    break

            if matched_element is None:
                continue  # skip unresolvable steps

            action = "fill" if step["action"] == "fill" else "click"
            flow_steps.append(FlowStep(
                sequence=sequence,
                action=action,
                element_id=matched_element.id,
                value=step.get("value"),
                url=None
            ))
            sequence += 1

        # 6. Ask LLM to name/describe the overall flow
        navigations_summary = [{"url": s["url"]} for s in parsed_steps if s["step_type"] == "navigate"]
        page_titles = [p.title for p in pages if p.title]

        flow_result = await llm_client.generate(
            FLOW_IDENTIFICATION_PROMPT.format(
                steps_json=json.dumps([
                    {"action": s.get("action"), "locator_type": s.get("locator_type"),
                     "name": s.get("name") or s.get("text") or s.get("selector", ""),
                     "value": s.get("value")}
                    for s in parsed_steps if s["step_type"] == "action"
                ][:80], indent=2),
                page_titles=json.dumps(page_titles)
            ),
            model="sonnet",
            expect_json=True
        )

        expected_outcome_raw = flow_result.get("expected_outcome", {})
        if not expected_outcome_raw.get("url_pattern") and navigations_summary:
            expected_outcome_raw["url_pattern"] = self._url_to_pattern(
                navigations_summary[-1]["url"], base_url
            )

        expected_outcome = ExpectedOutcome(
            type=expected_outcome_raw.get("type", "navigation"),
            url_pattern=expected_outcome_raw.get("url_pattern"),
            text=expected_outcome_raw.get("text")
        )

        flows: list[UserFlow] = []
        if flow_steps:
            flow = UserFlow(
                id=str(uuid.uuid4()),
                version_id=version.id,
                name=flow_result.get("name", "End-to-End User Journey"),
                description=flow_result.get("description", "Complete recorded session flow"),
                start_url=base_url,
                steps=flow_steps,
                expected_outcome=expected_outcome
            )
            flows.append(flow)

        # 7. Assemble ApplicationModel and persist
        app = ApplicationModel(
            id=app_id,
            name=app_name,
            base_url=base_url,
            version=version,
            pages=pages,
            elements=elements,
            flows=flows
        )

        await db.save_application(app)
        await db.save_version(version)
        if existing_app:
            await db.set_active_version(app_id, version.id)
        for page in pages:
            await db.save_page(page)
        for element in elements:
            await db.save_element(element)
        for flow in flows:
            await db.save_flow(flow)

        return app

    # ─── Codegen helpers ──────────────────────────────────────────────────────

    def _codegen_step_key(self, step: dict) -> str:
        """Unique deduplication key for a codegen step (element identity)."""
        lt = step.get("locator_type", "")
        nth = str(step.get("nth", ""))
        if lt == "role":
            # Use "" for None name (nameless getByRole) to avoid "None" string in key
            name = step.get("name") or ""
            return f"role|{step.get('role')}|{name}|{nth}"
        elif lt in ("id", "css", "xpath"):
            return f"{lt}|{step.get('selector')}|{nth}"
        elif lt == "text":
            return f"text|{step.get('text')}|{nth}"
        elif lt in ("aria_label", "placeholder"):
            name = step.get("name") or ""
            return f"{lt}|{name}|{nth}"
        elif lt == "chained_css_role":
            name = step.get("name") or ""
            return f"chained|{step.get('selector')}|{step.get('role')}|{name}|{nth}"
        return f"unknown|{nth}"

    def _codegen_step_to_locators(self, step: dict) -> tuple[list[LocatorSpec], str]:
        """Convert a parsed codegen step to (locators, element_type)."""
        locators: list[LocatorSpec] = []
        element_type = "input"
        lt = step.get("locator_type", "")
        nth = step.get("nth")

        if lt == "role":
            role = step.get("role", "")
            # Safely coerce None → "" so LocatorSpec (which requires str) never receives None.
            # branch 2b (getByRole without { name: }) explicitly sets name=None.
            name = step.get("name") or ""
            if not name:
                # No name supplied — identify by role only (will use nth at runtime)
                element_type = self._role_to_html_tag(role)
                locators.append(LocatorSpec(strategy="role", value=f"{role}:", confidence=0.75))
            elif role == "textbox":
                element_type = "input"
                # Both aria_label and placeholder match textbox name
                locators.append(LocatorSpec(strategy="aria_label", value=name, confidence=0.95))
                locators.append(LocatorSpec(strategy="placeholder", value=name, confidence=0.85))
            elif role == "button":
                element_type = "button"
                locators.append(LocatorSpec(strategy="role", value=f"button:{name}", confidence=0.90))
            elif role == "link":
                element_type = "link"
                locators.append(LocatorSpec(strategy="role", value=f"link:{name}", confidence=0.90))
            elif role == "checkbox":
                element_type = "checkbox"
                locators.append(LocatorSpec(strategy="aria_label", value=name, confidence=0.90))
            elif role == "combobox":
                element_type = "select"
                locators.append(LocatorSpec(strategy="aria_label", value=name, confidence=0.90))
            else:
                locators.append(LocatorSpec(strategy="role", value=f"{role}:{name}", confidence=0.85))
                locators.append(LocatorSpec(strategy="aria_label", value=name, confidence=0.75))

        elif lt == "id":
            element_type = "input"
            locators.append(LocatorSpec(strategy="id", value=step["selector"], confidence=0.70))

        elif lt in ("css", "xpath"):
            locators.append(LocatorSpec(strategy="css_name", value=step["selector"], confidence=0.55))

        elif lt == "text":
            text = step.get("text", "")
            safe_text = text[:50].replace('"', '\\"')
            locators.append(LocatorSpec(
                strategy="xpath_text",
                value=f'//*[contains(text(),"{safe_text}")]',
                confidence=0.40
            ))
            element_type = "link"

        elif lt == "aria_label":
            element_type = "input"
            locators.append(LocatorSpec(strategy="aria_label", value=step.get("name", ""), confidence=0.95))

        elif lt == "placeholder":
            element_type = "input"
            locators.append(LocatorSpec(strategy="placeholder", value=step.get("name", ""), confidence=0.85))

        elif lt == "chained_css_role":
            # e.g. page.locator('#dvaddbutton').getByRole('link').click()
            selector = step.get("selector", "")   # outer container id (without '#')
            role = step.get("role", "")
            name = step.get("name") or ""
            # Encode as "#selector|role|name" — decoded by _build_playwright_locator
            chained_value = f"#{selector}|{role}|{name}"
            element_type = self._role_to_html_tag(role)
            locators.append(LocatorSpec(
                strategy="chained_css_role",
                value=chained_value,
                confidence=0.90
            ))
            # Also store a plain id fallback so legacy resolve_locator can try #selector first
            if selector:
                locators.append(LocatorSpec(
                    strategy="id",
                    value=selector,
                    confidence=0.50
                ))

        # If nth is set, store it so the executor can use .nth()
        if nth is not None and locators:
            for loc in locators:
                loc.value = f"{loc.value}::nth={nth}"

        return locators, element_type

    def _codegen_step_matches_element(self, element: ElementModel, step: dict) -> bool:
        """Check if an ElementModel was built from this codegen step (for flow step matching)."""
        step_key = self._codegen_step_key(step)
        lt = step.get("locator_type", "")
        nth = step.get("nth")
        nth_suffix = f"::nth={nth}" if nth is not None else ""

        for loc in element.locators:
            if lt == "role":
                # Safely handle None name (set by branch 2b for nameless getByRole)
                name = step.get("name") or ""
                role = step.get("role", "")
                if not name:
                    # Nameless role — match by "role:" value + nth
                    if loc.strategy == "role" and loc.value == f"{role}:{nth_suffix}":
                        return True
                else:
                    if loc.strategy == "aria_label" and loc.value == f"{name}{nth_suffix}":
                        return True
                    if loc.strategy == "placeholder" and loc.value == f"{name}{nth_suffix}":
                        return True
                    if loc.strategy == "role" and loc.value == f"{role}:{name}{nth_suffix}":
                        return True
            elif lt in ("id", "css", "xpath"):
                selector = step.get("selector", "")
                if loc.value == f"{selector}{nth_suffix}":
                    return True
            elif lt == "text":
                text = step.get("text", "")
                safe = text[:50].replace('"', '\\"')
                if loc.strategy == "xpath_text" and f'contains(text(),"{safe}")' in loc.value:
                    return True
            elif lt in ("aria_label", "placeholder"):
                name = step.get("name", "")
                if loc.strategy in ("aria_label", "placeholder") and loc.value == f"{name}{nth_suffix}":
                    return True
            elif lt == "chained_css_role":
                selector = step.get("selector", "")
                role = step.get("role", "")
                name = step.get("name") or ""
                expected_val = f"#{selector}|{role}|{name}{nth_suffix}"
                if loc.strategy == "chained_css_role" and loc.value == expected_val:
                    return True
        return False

    def _role_to_html_tag(self, role: str) -> str:
        """Map ARIA role to HTML tag name (for LLM prompt)."""
        return {
            "textbox": "input", "button": "button", "link": "a",
            "checkbox": "input", "radio": "input", "combobox": "select",
            "listbox": "select",
        }.get(role, "input")

    def _url_to_pattern(self, url: str, base_url: str) -> str:
        """Convert a URL to a simple regex pattern."""
        try:
            # Remove base_url prefix to get the path
            if base_url and url.startswith(base_url):
                path = url[len(base_url):]
            else:
                # Extract just the path from full URL
                from urllib.parse import urlparse
                parsed = urlparse(url)
                path = parsed.path or "/"

            # Escape regex special chars (except /)
            path = re.sub(r'([.?+*\[\](){}\\|^$])', r'\\\1', path)
            return f"^{path}"
        except Exception:
            return url

    def _events_for_page(
        self,
        events: list[dict],
        start_time: str | None,
        end_time: str | None
    ) -> list[dict]:
        """Filter events that occurred between two navigation timestamps."""
        if not start_time and not end_time:
            return events

        result = []
        for event in events:
            ts = event.get("timestamp")
            if ts is None:
                result.append(event)
                continue
            # Timestamps from JS are milliseconds since epoch
            # Navigation timestamps are ISO strings
            # Try to compare; if parsing fails, include the event
            try:
                if start_time:
                    dt = datetime.fromisoformat(start_time)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    start_ms = dt.timestamp() * 1000
                    if ts < start_ms:
                        continue
                if end_time:
                    dt = datetime.fromisoformat(end_time)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    end_ms = dt.timestamp() * 1000
                    if ts > end_ms:
                        continue
                result.append(event)
            except Exception:
                result.append(event)
        return result

    def _element_key(self, event: dict) -> str:
        """Generate a unique key for an element based on its attributes."""
        parts = [
            event.get("tag") or "",
            event.get("id") or "",
            event.get("name") or "",
            event.get("aria_label") or "",
            event.get("placeholder") or "",
        ]
        return "|".join(parts)

    def _matches_event(self, element: ElementModel, event: dict) -> bool:
        """Check if an element matches an event by comparing all locator strategies."""
        tag = (event.get("tag") or "").lower()
        text_content = (event.get("text_content") or "").strip()
        name = event.get("name") or ""

        for loc in element.locators:
            # High-confidence direct attribute matches
            if loc.strategy == "id" and loc.value == (event.get("id") or ""):
                return True
            if loc.strategy == "placeholder" and loc.value == (event.get("placeholder") or ""):
                return True
            if loc.strategy == "aria_label" and loc.value == (event.get("aria_label") or ""):
                return True
            # Match by name attribute (css_name strategy stores e.g. 'input[name="email"]')
            if loc.strategy == "css_name" and name and name in loc.value:
                return True
            # Match by role + text (e.g. 'button:Login' or 'link:Forgot Password')
            if loc.strategy == "role" and text_content:
                role_tag = "button" if tag == "button" else "link"
                expected = f"{role_tag}:{text_content[:100]}"
                if loc.value == expected:
                    return True
            # Match by xpath_text (e.g. //button[contains(text(),"Login")])
            if loc.strategy == "xpath_text" and tag and text_content:
                safe_text = text_content[:50].replace('"', '\\"')
                expected_xpath = f'//{tag}[contains(text(),"{safe_text}")]'
                if loc.value == expected_xpath:
                    return True
        return False

    def _classify_element_type(self, event: dict) -> str:
        """Classify an element type based on event attributes."""
        tag = (event.get("tag") or "").lower()
        type_attr = (event.get("type_attr") or "").lower()

        if tag == "input":
            if type_attr in ("checkbox",):
                return "checkbox"
            elif type_attr in ("radio",):
                return "radio"
            else:
                return "input"
        elif tag == "select":
            return "select"
        elif tag == "textarea":
            return "textarea"
        elif tag == "button":
            return "button"
        elif tag == "a":
            return "link"
        else:
            return "input"

    def _events_to_flow_steps(
        self,
        events: list[dict],
        elements: list[ElementModel],
        page: PageModel | None
    ) -> list[FlowStep]:
        """
        Convert raw events to FlowStep objects.
        Steps where no element can be matched (element_id would be null AND action is click)
        are filtered out — they represent accidental clicks on blank space or structural
        containers that have no testable effect. Keeping them causes the executor to silently
        skip critical steps (like the Login button) and leave the browser stuck on the wrong page.
        """
        steps: list[FlowStep] = []
        sequence = 1

        for event in events:
            event_type = event.get("event_type", "")
            if event_type not in ("click", "input", "change", "submit"):
                continue

            # Find matching element in the known elements database
            matched_element_id = None
            for elem in elements:
                if page and elem.page_id != page.id:
                    continue
                if self._matches_event(elem, event):
                    matched_element_id = elem.id
                    break

            # Map event type to action
            action = "click"
            if event_type in ("input", "change"):
                action = "fill"
            elif event_type == "submit":
                action = "click"

            # Filter out null-element CLICK steps — these are accidental clicks on
            # structural containers (divs, spans) that were not captured as known elements.
            # Keeping them causes critical steps (e.g., Login button) to be silently skipped
            # and the test to fail because the browser never navigates to the next page.
            # Fill steps with null element are also filtered since they cannot be executed.
            if matched_element_id is None:
                continue

            step = FlowStep(
                sequence=sequence,
                action=action,
                element_id=matched_element_id,
                value=event.get("value"),
                url=None
            )
            steps.append(step)
            sequence += 1

        return steps

    def _preprocess_events(self, events: list[dict]) -> list[dict]:
        """
        Preprocesses raw session events to merge consecutive typing,
        combine clicks with typing on the same element, and ignore intermediate states.
        """
        preprocessed_events = []
        current_fill_event = None
        pending_click_event = None
        
        for event in events:
            event_type = event.get("event_type")
            elem_key = self._element_key(event)
            
            if event_type in ("input", "change"):
                # If there was a pending click on a DIFFERENT element, commit it
                if pending_click_event and self._element_key(pending_click_event) != elem_key:
                    preprocessed_events.append(pending_click_event)
                pending_click_event = None  # Discard click on same element
                
                if current_fill_event and self._element_key(current_fill_event) == elem_key:
                    current_fill_event["value"] = event.get("value")
                    current_fill_event["timestamp"] = event.get("timestamp")
                    if event_type == "change":
                        current_fill_event["event_type"] = "change"
                else:
                    if current_fill_event:
                        preprocessed_events.append(current_fill_event)
                    current_fill_event = dict(event)
            else:
                # Non-typing event
                if current_fill_event:
                    preprocessed_events.append(current_fill_event)
                    current_fill_event = None
                if pending_click_event:
                    preprocessed_events.append(pending_click_event)
                    pending_click_event = None
                
                if event_type == "click":
                    pending_click_event = event
                else:
                    preprocessed_events.append(event)
                    
        if current_fill_event:
            preprocessed_events.append(current_fill_event)
        if pending_click_event:
            preprocessed_events.append(pending_click_event)
            
        return preprocessed_events
