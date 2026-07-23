# Requirements Document — Browser Agent: Project Index and Analysis

## Introduction

This document is a comprehensive reference index for the **Browser Agent** system — an AI-powered, Python-based
functional testing tool. It indexes every module, catalogues responsibilities, traces data flows between
components, enumerates correctness properties, and records known limitations and design constraints.

The Browser Agent implements a two-phase workflow:

- **LEARN phase**: The user records a browser session. The system converts that recording into a structured
  `ApplicationModel` (pages, elements, flows) stored in SQLite, then generates test cases.
- **TEST phase**: The system autonomously replays test cases using Playwright, resolves UI elements via a
  multi-strategy locator chain (falling back to LLM when needed), evaluates assertions, and produces HTML,
  JSON, and Excel reports.

This specification covers the system **as-built** based on a full source-code audit. It is a living reference
document, not a new-feature build plan.

---

## Glossary

- **ApplicationModel**: The in-memory Pydantic aggregate (`schema.ApplicationModel`) that holds one
  application's complete structural description: `AppVersion`, `PageModel` list, `ElementModel` list, and
  `UserFlow` list.
- **AppVersion**: A labelled snapshot (`v1_YYYY-MM-DD`) of an `ApplicationModel`. Only one version is
  `is_active` at a time per application.
- **PageModel**: A single URL-addressable page with a regex `url_pattern`, accessibility snapshot, and LLM-
  generated purpose description.
- **ElementModel**: A single interactive UI element with a `semantic_label`, ranked `LocatorSpec` list,
  `ValidationRule` list, and `observed_values` collected during recording.
- **LocatorSpec**: A single locator strategy (`aria_label`, `placeholder`, `role`, `id`, `css`, `xpath`,
  `chained_css_role`) plus a `confidence` score (0.0–1.0).
- **UserFlow**: A recorded end-to-end sequence of `FlowStep` objects with a name, description, start URL, and
  `ExpectedOutcome`.
- **FlowStep**: One recorded user action (`fill`, `click`, `navigate`, `select`, `check`, `hover`, `clear`)
  with an optional `element_id` and `value`.
- **TestCase**: A generated test containing `TestStep` list and `Assertion` list, categorised as `happy_path`,
  `negative`, or `edge_case`.
- **Assertion**: A typed correctness check — `url_contains`, `element_visible`, `text_equals`,
  `element_count`, or `element_absent`.
- **TestResult**: The full outcome of executing one `TestCase`, including per-step `StepResult` objects,
  `assertion_results`, duration, and optional screenshot paths.
- **StepResult**: Per-step execution record with `status` (`passed` / `failed` / `skipped`), the
  `locator_used`, and optional error text and screenshot path.
- **LLMClient**: The Groq API wrapper that maps the aliases `"haiku"` (fast, `llama-3.1-8b-instant`) and
  `"sonnet"` (smart, `qwen/qwen3-32b` default) to real Groq model strings.
- **LLMCache**: A SQLite-backed SHA-256 keyed cache (`llm_cache` table) for LLM responses, keyed on
  `hash(model + prompt)`.
- **BrowserRecorder**: The raw-event recorder that injects `event_capture.js` into a headful Chromium
  browser and polls `window.__capturedEvents` every 500 ms.
- **CodegenRecorder**: The Playwright-codegen-based recorder that runs `playwright codegen --target
  javascript` as a subprocess and captures the resulting JS file.
- **CodegenParser**: The regex-based parser (`core/codegen_parser.py`) that converts Playwright codegen JS
  output into a list of typed step dicts.
- **ModelBuilder**: The component (`core/model_builder.py`) that converts raw session data or parsed codegen
  steps into a fully persisted `ApplicationModel`.
- **TestGenerator**: The LLM-driven component that produces `TestCase` objects from a `UserFlow`, using
  segment-based prompting.
- **TestExecutor**: The Playwright-based autonomous runner that executes test cases and returns `TestResult`
  objects.
- **Verifier**: The assertion evaluator (`core/verifier.py`) with both strict Playwright checks and an LLM
  semantic fallback.
- **Database**: The async SQLite layer (`storage/db.py`) using `aiosqlite` for all persistence operations.
- **diff_models**: The version comparison utility (`storage/diff.py`) that compares two `ApplicationModel`
  instances by page URL pattern, element semantic label, and flow name.
- **EARS**: Easy Approach to Requirements Syntax — the structured natural-language pattern used for all
  acceptance criteria in this document.

---

## Requirements

---

### Requirement 1: Entry Point and CLI Architecture

**User Story:** As a developer, I want a single command-line entry point with discoverable sub-commands, so
that I can learn, test, report, and manage application versions without consulting source code.

#### Acceptance Criteria

1. THE `agent.py` Entry_Point SHALL add the `browser_agent/` directory to `sys.path` and delegate all
   execution to `cli.cli` via Click.
2. THE CLI SHALL expose exactly six commands: `learn`, `test`, `relearn`, `report`, `diff`, and `status`.
3. WHEN the host platform is `win32`, THE Entry_Point SHALL reconfigure `sys.stdout` and `sys.stderr` to
   `utf-8` encoding before any Rich output is produced.
4. THE CLI SHALL initialise the Database connection and call `db.initialize()` before any command touches
   the database.
5. WHEN an application name is passed to `test`, `report`, `diff`, or `relearn` but no matching record
   exists in the `applications` table, THE CLI SHALL print a Rich-formatted error message and exit without
   raising an unhandled exception.

---

### Requirement 2: Configuration Management

**User Story:** As an operator, I want all runtime parameters loaded from a `.env` file with safe defaults,
so that I can deploy the agent without modifying source code.

#### Acceptance Criteria

1. THE `Config` Class SHALL read each parameter from the environment at class definition time, using
   `os.environ.get` with the documented default value.
2. WHEN a `.env` file exists at `browser_agent/.env`, THE `Config` Class SHALL load it with `override=False`
   so that pre-existing environment variables take precedence.
3. THE `Config` Class SHALL expose at minimum: `GROQ_API_KEY`, `DB_PATH`, `SCREENSHOTS_DIR`, `REPORTS_DIR`,
   `LLM_CACHE_DIR`, `GROQ_SMART_MODEL`, `GROQ_FAST_MODEL`, `MAX_LLM_RETRIES`, `LOCATOR_TIMEOUT_MS`,
   `NAVIGATION_TIMEOUT_MS`, and `PARALLEL_TESTS`.
4. THE `Config` Class SHALL parse `MAX_LLM_RETRIES`, `LOCATOR_TIMEOUT_MS`, `NAVIGATION_TIMEOUT_MS`, and
   `PARALLEL_TESTS` as integers; a non-integer value in the environment SHALL cause an explicit `ValueError`
   at startup.
5. THE `LLMClient` SHALL read `GROQ_SMART_MODEL` and `GROQ_FAST_MODEL` from the environment independently of
   `Config`, so that both loading paths remain consistent.

---

### Requirement 3: Pydantic Data Models

**User Story:** As a developer, I want all domain objects defined as immutable Pydantic v2 models, so that
type safety is enforced at runtime and serialisation to/from SQLite JSON is automatic.

#### Acceptance Criteria

1. THE `Schema` Module SHALL define exactly these top-level models: `LocatorSpec`, `ValidationRule`,
   `ElementModel`, `PageModel`, `FlowStep`, `ExpectedOutcome`, `UserFlow`, `TestStep`, `Assertion`,
   `TestCase`, `StepResult`, `TestResult`, `AppVersion`, `ApplicationModel`.
2. THE `LocatorSpec.confidence` Field SHALL accept only values in the range `[0.0, 1.0]`.
3. THE `ElementModel.element_type` Field SHALL accept only the documented values: `"input"`, `"button"`,
   `"select"`, `"link"`, `"checkbox"`, `"radio"`, `"textarea"`.
4. THE `TestCase.category` Field SHALL accept only `"happy_path"`, `"negative"`, or `"edge_case"`.
5. THE `TestResult.status` Field SHALL accept only `"passed"`, `"failed"`, or `"errored"`.
6. THE `StepResult.status` Field SHALL accept only `"passed"`, `"failed"`, or `"skipped"`.
7. WHEN a field is listed as `Optional`, THE Schema Module SHALL default it to `None` rather than omitting
   it from serialisation via `.model_dump()`.

---

### Requirement 4: Raw-Event Browser Recorder (BrowserRecorder)

**User Story:** As a developer, I want a headful browser recorder that injects JavaScript and captures all
user interactions without requiring Playwright codegen, so that I have a fallback recording mode that works
with any modern browser.

#### Acceptance Criteria

1. THE BrowserRecorder SHALL launch a headful Chromium browser with `headless=False`, navigate to the
   user-supplied start URL, and wait for `domcontentloaded` before injecting `event_capture.js`.
2. THE BrowserRecorder SHALL call `page.add_init_script(event_capture_js)` so that `event_capture.js` runs
   on every subsequent navigation automatically.
3. THE BrowserRecorder SHALL poll `window.__capturedEvents` every 500 ms via `page.evaluate()`, extending
   `_event_buffer` and clearing `window.__capturedEvents` on each poll.
4. WHEN a page navigation event occurs, THE BrowserRecorder SHALL capture: `url`, `title`,
   `accessibility_snapshot`, `screenshot_path`, and `timestamp`.
5. WHEN a dialog (alert/confirm/prompt) appears, THE BrowserRecorder SHALL auto-dismiss it via
   `dialog.dismiss()` and log the dialog type and message.
6. THE BrowserRecorder SHALL log only API requests whose URL contains `"/api/"` to reduce log noise.
7. WHEN `stop_session()` is called, THE BrowserRecorder SHALL perform one final `_drain_events()`, close the
   browser, and return a dict containing `events`, `navigations`, `dialogs`, `api_calls`, `app_name`,
   `start_url`, and `recorded_at`.

---

### Requirement 5: Browser-Injected Event Capture (event_capture.js)

**User Story:** As a recorder, I want a JavaScript library that captures all interactive events (click,
input, change, submit) into a global buffer, so that polling can retrieve events without blocking the
browser main thread.

#### Acceptance Criteria

1. THE Event_Capture_JS SHALL initialise `window.__capturedEvents = []` on every injection if it does not
   already exist.
2. THE Event_Capture_JS SHALL set `window.__listenersAttached = true` on first injection and skip listener
   attachment on subsequent re-injections into the same page context.
3. THE Event_Capture_JS SHALL attach capture-phase listeners (`document.addEventListener(type, handler,
   true)`) for these event types: `click`, `input`, `change`, `submit`.
4. WHEN an event fires, THE Event_Capture_JS SHALL extract: `tag`, `type_attr`, `id`, `name`, `placeholder`,
   `aria_label`, `role`, `text_content`, `value`, `classes`, `event_type`, `timestamp`, and `page_url`.
5. THE Event_Capture_JS SHALL never throw an exception from event handlers or the top-level IIFE.

---

### Requirement 6: Playwright Codegen Recorder (CodegenRecorder)

**User Story:** As a developer, I want a recorder that delegates to Playwright's built-in codegen, so that I
get resilient locators (getByRole, getByText) without writing custom heuristics.

#### Acceptance Criteria

1. THE CodegenRecorder SHALL run `playwright codegen --target javascript --output <temp_file> <start_url>` as
   a subprocess using `asyncio.create_subprocess_exec`.
2. THE CodegenRecorder SHALL block in `record()` until the subprocess exits (user closes the browser).
3. WHEN the subprocess completes successfully, THE CodegenRecorder SHALL read the temp JS file and return its
   content as a string.
4. WHEN the temp file is empty or does not exist, THE CodegenRecorder SHALL return an empty string without
   raising an exception.
5. THE CodegenRecorder SHALL delete the temp file via `os.unlink` in a `finally` block to prevent file
   leaks.

---

### Requirement 7: Playwright JS Parser (CodegenParser)

**User Story:** As a model builder, I want a regex-based parser that converts Playwright codegen JS into
structured step dicts, so that I can reuse the same `ApplicationModel` builder for both recording modes.

#### Acceptance Criteria

1. THE CodegenParser SHALL parse `await page.goto('url')` lines into `{"step_type": "navigate", "url":
   "..."}` dicts.
2. THE CodegenParser SHALL parse `page.getByRole('role', { name: 'label' })` into `{"step_type": "action",
   "locator_type": "role", "role": "...", "name": "...", ...}` dicts.
3. THE CodegenParser SHALL parse `page.getByRole('role')` (no name option) into a dict with `"name": None`.
4. THE CodegenParser SHALL parse `page.locator('#id').getByRole('role')` chained locators into
   `{"locator_type": "chained_css_role", "selector": "id", "role": "...", "name": None or "..."}`.
5. THE CodegenParser SHALL parse `.nth(N)` and `.first()` modifiers and store them as an `"nth"` integer
   (0-indexed for `.first()`).
6. THE CodegenParser SHALL recognise `page.locator(selector)` lines and classify them by selector prefix:
   `#...` → `id`, `//...` → `xpath`, else → `css`.
7. THE CodegenParser SHALL extract the first string argument from `.fill('...')` calls using a regex and
   return it as `"value"`.
8. THE CodegenParser SHALL skip non-action lines (comments, imports, `page.pause()`) and only return
   `navigate` and `action` step dicts.

---

### Requirement 8: Application Model Builder (ModelBuilder)

**User Story:** As the LEARN pipeline, I want a component that converts raw session data or codegen steps
into a fully structured and persisted `ApplicationModel`, so that the TEST phase has a stable, queryable
representation of the application.

#### Acceptance Criteria

1. THE ModelBuilder SHALL support two entry points: `build(session_data, ...)` for raw events and
   `build_from_codegen(parsed_steps, ...)` for codegen steps.
2. THE ModelBuilder SHALL reuse an existing `app_id` if an application with the same name already exists in
   the database, rather than creating a duplicate application record.
3. THE ModelBuilder SHALL create a new `AppVersion` with label `v1_{YYYY-MM-DD}` for every call to `build`
   or `build_from_codegen`.
4. THE ModelBuilder SHALL deduplicate pages by URL: each unique URL SHALL produce exactly one `PageModel`.
5. THE ModelBuilder SHALL deduplicate elements within a page by element key (tag + id + name + aria_label +
   placeholder) and, for codegen mode, by `_codegen_step_key`.
6. FOR ALL elements, THE ModelBuilder SHALL call `LLMClient.generate` with `ELEMENT_LABEL_PROMPT` using the
   `"haiku"` model to obtain a `semantic_label` and `validation_rules`.
7. THE ModelBuilder SHALL call `LLMClient.generate` with `FLOW_IDENTIFICATION_PROMPT` using the `"sonnet"`
   model to obtain a flow `name`, `description`, and `expected_outcome`.
8. THE ModelBuilder SHALL persist the complete `ApplicationModel` to the database (application, version,
   pages, elements, flows) before returning it.
9. WHEN `existing_app` was found, THE ModelBuilder SHALL call `db.set_active_version` to mark the new
   version as the active one.
10. THE `_url_to_pattern` Helper SHALL convert full URLs to regex path patterns by stripping the base URL
    prefix and escaping regex special characters except `/`.

---

### Requirement 9: Locator Generation and Multi-Strategy Resolution

**User Story:** As the executor, I want a ranked list of locators per element and a resolution algorithm
that tries them in order, so that I can locate elements reliably even after minor UI changes.

#### Acceptance Criteria

1. THE Locator_Generator SHALL produce locators in this priority order (highest confidence first):
   `chained_css_role` (0.90), `aria_label` (0.95), `placeholder` (0.85), `role+text` (0.80), `id` (0.70),
   `css_name` (0.55), `xpath_text` (0.40).
2. THE Locator_Generator SHALL skip `id` locators whose value matches auto-generated patterns: purely
   numeric, UUID-like, or prefixed with `react-` or `ember`.
3. THE `resolve_locator` Function SHALL attempt each `LocatorSpec` in descending `confidence` order, calling
   `loc.first.wait_for(state="visible", timeout=timeout_ms)` for each.
4. WHEN all static locators fail, THE `resolve_locator` Function SHALL call the LLM with
   `LOCATOR_FALLBACK_PROMPT` and the current accessibility tree snapshot to obtain an alternative strategy
   and value.
5. WHEN the LLM fallback also fails, THE `resolve_locator` Function SHALL raise `ElementNotFoundError` with
   the element's `semantic_label` and the last exception message.
6. THE `_build_playwright_locator` Function SHALL handle all eight strategy types: `aria_label`,
   `placeholder`, `role`, `text`, `id`, `css_name`, `xpath_text`, and `chained_css_role`.
7. WHEN a locator `value` contains the `::nth=N` suffix, THE `_build_playwright_locator` Function SHALL
   call `.nth(N)` on the locator before returning it.

---

### Requirement 10: LLM Client (LLMClient)

**User Story:** As the system, I want a robust Groq API wrapper with retry, caching, and JSON extraction, so
that LLM calls are reliable, idempotent, and cost-efficient.

#### Acceptance Criteria

1. THE LLMClient SHALL map the alias `"haiku"` to `GROQ_FAST_MODEL` and `"sonnet"` to `GROQ_SMART_MODEL`.
2. WHEN a cached response exists for `sha256(model + prompt)`, THE LLMClient SHALL return the cached value
   without calling the Groq API.
3. THE LLMClient._call_api Method SHALL retry up to 6 times with exponential backoff (multiplier=2, min=4 s,
   max=65 s) on `RateLimitError`, `APIStatusError`, and `APIConnectionError`.
4. WHEN `expect_json=True`, THE LLMClient SHALL request `response_format={"type": "json_object"}` and parse
   the response as JSON.
5. WHEN the initial JSON parse fails, THE LLMClient SHALL make one additional API call with a self-correction
   prompt asking the model to fix its own output.
6. THE LLMClient SHALL use `temperature=0.1` and `max_tokens=4096` for all calls.
7. WHEN a successful response is obtained, THE LLMClient SHALL store it in `LLMCache` before returning.

---

### Requirement 11: LLM Response Cache (LLMCache)

**User Story:** As the operator, I want LLM responses cached to SQLite, so that repeated learn/test runs
against the same page structure do not incur API costs or latency.

#### Acceptance Criteria

1. THE LLMCache SHALL use the `llm_cache` table in the same SQLite database file as all other data.
2. THE LLMCache.get Method SHALL return `None` silently if no row matches `input_hash`, never raising an
   exception.
3. THE LLMCache.set Method SHALL use `INSERT OR REPLACE` so that re-running the same prompt updates the
   cached response rather than duplicating rows.
4. WHEN a database I/O error occurs in `LLMCache.set`, THE LLMCache SHALL swallow the exception and
   continue, as cache failures are non-fatal.

---

### Requirement 12: LLM Prompt Templates

**User Story:** As a developer, I want all LLM prompts defined as module-level string constants, so that
prompt changes are centralised and auditable.

#### Acceptance Criteria

1. THE `prompts` Module SHALL define: `ELEMENT_LABEL_PROMPT`, `FLOW_IDENTIFICATION_PROMPT`,
   `TEST_GENERATION_PROMPT`, `LOCATOR_FALLBACK_PROMPT`, `FAILURE_DIAGNOSIS_PROMPT`,
   `SEMANTIC_ASSERTION_PROMPT`, and `SEGMENT_TEST_GENERATION_PROMPT`.
2. THE `ELEMENT_LABEL_PROMPT` SHALL instruct the LLM to return a JSON object with keys `semantic_label`,
   `purpose`, `validation_rules`, and `locator_priority`.
3. THE `FLOW_IDENTIFICATION_PROMPT` SHALL instruct the LLM to return a JSON object with keys `name`,
   `description`, and `expected_outcome`.
4. THE `TEST_GENERATION_PROMPT` SHALL instruct the LLM to return exactly 1 happy path, 3–5 negative, and
   2–3 edge case tests in a `test_cases` JSON array.
5. THE `SEGMENT_TEST_GENERATION_PROMPT` SHALL target a specific subset of fields (a "segment") and instruct
   the LLM to return only `negative` and `edge_case` tests, never `happy_path`.
6. THE `SEMANTIC_ASSERTION_PROMPT` SHALL instruct the LLM to return a JSON object with exactly two keys:
   `"passed"` (boolean) and `"reason"` (string).

---

### Requirement 13: Test Case Generator (TestGenerator)

**User Story:** As the LEARN pipeline, I want to generate comprehensive test cases per flow without manual
scripting, so that the TEST phase has coverage of happy path, validation failures, and edge inputs.

#### Acceptance Criteria

1. THE TestGenerator SHALL build the `happy_path` test directly from `flow.steps` without calling the LLM,
   by copying steps with re-indexed sequence numbers.
2. THE TestGenerator SHALL remove redundant consecutive `click` + `fill` steps on the same element from the
   happy path sequence.
3. THE TestGenerator SHALL identify input-like elements (types: `textbox`, `textarea`, `combobox`,
   `checkbox`, `input`, `password`, `select`) from flow steps to target for mutation.
4. THE TestGenerator SHALL group input elements into segments of 3 and call `SEGMENT_TEST_GENERATION_PROMPT`
   once per segment, applying a 15-second delay between API calls.
5. WHEN a generated test case's step fingerprint `(element_id, value)` matches an existing test case,
   THE TestGenerator SHALL discard the duplicate.
6. THE TestGenerator SHALL reconstruct mutated test step sequences programmatically from the happy path,
   replacing the target element's value with `mutated_value`.
7. WHEN the target element precedes the login-button step in the happy path, THE TestGenerator SHALL stop
   the test sequence immediately after the login-button click.

---

### Requirement 14: Test Executor (TestExecutor)

**User Story:** As the TEST phase, I want an autonomous Playwright-based executor that runs each test case
in isolation, so that test failures are independent and produce meaningful artefacts.

#### Acceptance Criteria

1. THE TestExecutor SHALL run test cases in parallel using `asyncio.Semaphore(PARALLEL_TESTS)`.
2. THE TestExecutor SHALL launch a fresh `chromium` browser, context, and page for every single test case
   and close all resources in a `finally` block.
3. WHEN a test navigates to the start URL, THE TestExecutor SHALL call `wait_for_page_stability` which
   waits for: `document.readyState === 'complete'`, network idle (cap 10 s), and disappearance of known
   loading spinner selectors.
4. THE `wait_for_page_stability` Function SHALL apply stability checks after every `navigate` step and after
   every element-interaction step.
5. WHEN an element ID in a `TestStep` is not found in the `elements` map, THE TestExecutor SHALL set the
   step status to `"skipped"` and record the reason, but SHALL NOT abort the test.
6. WHEN an `ElementNotFoundError` is raised, THE TestExecutor SHALL capture a failure screenshot, mark the
   test as `"failed"`, and stop executing further steps.
7. WHEN any other exception is raised during step execution, THE TestExecutor SHALL capture an error
   screenshot, mark the test as `"errored"`, and stop executing further steps.
8. THE `_execute_action` Method SHALL support these actions: `fill`, `click`, `check`, `select`, `hover`,
   `clear`, and SHALL default unknown actions to `click`.
9. FOR `fill` actions, THE TestExecutor SHALL attempt to remove the `readonly` attribute and click the
   element before calling `.fill()`.
10. THE TestExecutor SHALL evaluate assertions only if the test status is not `"errored"` after step
    execution.

---

### Requirement 15: Assertion Verifier (Verifier)

**User Story:** As the executor, I want an assertion evaluator with both strict Playwright checks and an LLM
semantic fallback, so that minor rendering or URL differences do not cause false failures.

#### Acceptance Criteria

1. THE Verifier SHALL evaluate these assertion types: `url_contains`, `element_visible`, `text_equals`,
   `element_count`, and `element_absent`.
2. FOR `url_contains` assertions, THE Verifier SHALL call `page.wait_for_url` with the expected fragment
   and timeout 5000 ms before reading `page.url`.
3. FOR `element_visible` assertions, THE Verifier SHALL first attempt to resolve the element through its
   `ElementModel` locators, then fall back to `page.locator(f"text={expected}")` and `page.get_by_label`.
4. FOR `text_equals` assertions using `selector:::text` format, THE Verifier SHALL split on `:::` and use
   the left part as a CSS selector and the right part as the expected text.
5. FOR `element_count` assertions using `selector:::count` format, THE Verifier SHALL split on `:::` and
   compare `page.locator(selector).count()` to the expected integer.
6. WHEN a strict assertion fails and `llm_client` is provided, THE Verifier SHALL call
   `_evaluate_semantic_fallback` using the `"haiku"` model.
7. THE `_evaluate_semantic_fallback` Function SHALL attempt to read toast/alert text from `.toast-title`,
   `.toast-message`, and a list of fallback selectors (`[role='alert']`, `.alert`, etc.) before constructing
   the LLM prompt.
8. WHEN the LLM semantic fallback returns `{"passed": true}`, THE Verifier SHALL mark the assertion as
   passed and set `result["actual"]` to the LLM's `reason` string.
9. THE Verifier SHALL write all semantic fallback attempts and results to `semantic_fallback_debug.log` for
   post-run debugging.

---

### Requirement 16: Persistent Storage (Database)

**User Story:** As the system, I want all domain objects persisted in a single SQLite file, so that
application models and test results survive process restarts and support multiple versions.

#### Acceptance Criteria

1. THE Database.initialize Method SHALL execute `storage/schema.sql` using `executescript`, creating all 10
   tables only if they do not exist.
2. THE Database SHALL use a new `aiosqlite.connect()` context for every CRUD operation to avoid connection
   state sharing between concurrent coroutines.
3. THE Database SHALL serialise `list` and `dict` fields (locators, validation_rules, steps, assertions,
   etc.) to JSON strings when writing and deserialise them when reading.
4. FOR `app_versions`, THE Database SHALL maintain the invariant that exactly one version per `app_id` has
   `is_active = 1`, enforced by `set_active_version` which first sets all versions to `is_active = 0`.
5. THE Database.get_test_cases_for_app Method SHALL join through `user_flows` and `app_versions` to return
   only test cases linked to the active version.
6. THE Database.save_test_result Method SHALL use `INSERT OR REPLACE` so that re-running tests for the same
   test case ID updates the record.

---

### Requirement 17: Version Diff (diff_models)

**User Story:** As an operator using `relearn`, I want a structured diff of two ApplicationModel versions,
so that I can decide whether to activate the new version or discard it.

#### Acceptance Criteria

1. THE `diff_models` Function SHALL compare pages by `url_pattern`, elements by `semantic_label`, and flows
   by `name`.
2. THE `diff_models` Function SHALL return a dict with exactly these keys: `new_pages`, `removed_pages`,
   `new_elements`, `removed_elements`, `changed_elements`, `new_flows`, `removed_flows`, `changed_flows`.
3. FOR changed elements, THE `diff_models` Function SHALL report `element_type` changes and validation rule
   additions/removals.
4. FOR changed flows, THE `diff_models` Function SHALL report `start_url` changes and step-count changes.
5. WHEN no differences exist between two versions, THE `diff_models` Function SHALL return a dict where all
   eight lists are empty.

---

### Requirement 18: HTML Report Generation

**User Story:** As a QA engineer, I want a self-contained HTML report with embedded screenshots and
per-assertion details, so that I can review test results without access to the server file system.

#### Acceptance Criteria

1. THE HTML_Reporter SHALL use Jinja2 with the `reporting/templates/report.html` template and
   `autoescape=True` to render the report.
2. THE HTML_Reporter SHALL compute `total`, `passed`, `failed`, `errored`, `duration_s`, and `pass_rate`
   from the `results` list and pass them to the template as a `summary` dict.
3. THE HTML_Reporter SHALL embed failure screenshots as base64 inline `<img>` tags; screenshots for steps
   with status `"passed"` SHALL be omitted to keep file size small.
4. THE HTML_Reporter SHALL pre-fetch step values from `test_cases.steps` in the database and fall back to
   the `get_step_value` heuristic for display in the step table.
5. THE HTML_Reporter SHALL write the output file atomically and create parent directories if they do not
   exist.

---

### Requirement 19: JSON Report Generation

**User Story:** As a CI/CD pipeline, I want a structured JSON report per run, so that downstream tools can
parse test outcomes programmatically without parsing HTML.

#### Acceptance Criteria

1. THE JSON_Reporter SHALL write a JSON object with top-level keys: `run_id`, `app`, `generated_at`,
   `summary`, `duration_seconds`, and `test_results`.
2. THE `test_results` Array SHALL contain one object per `TestResult` with: `id`, `test_case_id`,
   `test_name`, `category`, `status`, `duration_ms`, `step_results`, `assertion_results`, and
   `error_detail`.
3. THE JSON_Reporter SHALL use `json.dump(..., default=str)` to handle `datetime` and other non-serialisable
   types.

---

### Requirement 20: Excel Report Generation

**User Story:** As a project manager, I want a styled Excel (.xlsx) report that maps to a standard test
case template, so that I can share results with stakeholders who do not use developer tools.

#### Acceptance Criteria

1. THE Excel_Reporter SHALL create an `.xlsx` workbook with a single sheet named `"Sheet1"` using
   `openpyxl`.
2. THE Excel_Reporter SHALL sort results with happy path tests first, then all other tests alphabetically by
   name.
3. THE Excel_Reporter SHALL include these columns: Module, Sub-Module, Test Case Id, Test Case Scenario,
   Test Case Steps, Test Data, Expected Result, Actual Result, Status (Pass/Fail).
4. THE Excel_Reporter SHALL merge the Module column cells across all data rows.
5. THE Excel_Reporter SHALL render PASS status in bold green (`#008000`) and FAIL in bold red (`#FF0000`).
6. THE Excel_Reporter SHALL query the database directly via `sqlite3` (synchronous) to obtain element
   labels and test case steps.
7. THE `get_test_data_smart` Helper SHALL return `"NA"` for happy path tests and otherwise diff the test
   case steps against happy path steps to identify the mutated field and value.

---

### Requirement 21: Screenshot Capture

**User Story:** As a test reporter, I want screenshots captured at each test step and on failure, so that
test failures can be visually diagnosed.

#### Acceptance Criteria

1. THE Screenshot_Utility SHALL create the `screenshots_dir` directory if it does not exist.
2. THE Screenshot_Utility SHALL name each screenshot `{UTC_timestamp}_{safe_label}.png` where `safe_label`
   contains only alphanumeric, `-`, and `_` characters, truncated to 50 characters.
3. WHEN `page.screenshot()` raises an exception (e.g., page navigated away), THE Screenshot_Utility SHALL
   return an empty string without propagating the exception.
4. THE `screenshot_to_base64` Function SHALL return an empty string when the file does not exist rather than
   raising `FileNotFoundError`.

---

### Requirement 22: Full Data Flow — LEARN Phase

**User Story:** As a developer, I want the LEARN phase data flow fully documented, so that I can trace any
data transformation or persistence failure from recording through to test case storage.

#### Acceptance Criteria

1. THE LEARN Phase SHALL execute the following data flow in order:
   a. `CodegenRecorder.record()` → raw JavaScript string
   b. `CodegenParser.parse_playwright_js()` → `list[dict]` steps
   c. `ModelBuilder.build_from_codegen()` → `ApplicationModel` + DB persistence
   d. `TestGenerator.generate()` → `list[TestCase]` + DB persistence
2. WHEN the codegen JS string is empty after recording, THE CLI SHALL print an error and return without
   calling `ModelBuilder` or `TestGenerator`.
3. WHEN `ModelBuilder.build_from_codegen` raises an exception, THE CLI SHALL print the error with a
   traceback and not silently swallow it.
4. THE `ApplicationModel` produced by `ModelBuilder` SHALL be immediately available for `TestGenerator`
   without reloading from the database.

---

### Requirement 23: Full Data Flow — TEST Phase

**User Story:** As a developer, I want the TEST phase data flow fully documented, so that I can trace test
execution from loading test cases through to report file generation.

#### Acceptance Criteria

1. THE TEST Phase SHALL execute the following data flow in order:
   a. `Database.get_test_cases_for_app()` → `list[TestCase]`
   b. `TestExecutor.run_test()` (parallel, semaphore-limited) → `list[TestResult]`
   c. `Database.save_test_result()` for each result
   d. `Database.update_test_run()` with pass/fail/error totals
   e. `generate_html_report()`, `generate_json_report()`, `generate_excel_report()` → output files
2. THE TEST Phase SHALL create a `test_runs` row before executing any test, so that partial results can be
   attributed to a run ID even if the process is interrupted.
3. WHEN `--suite` is specified, THE CLI SHALL filter test cases by `category` before creating the test run
   record.
4. THE TEST Phase SHALL copy the Excel report to a root-level `{app_name}_test_report.xlsx` file after
   writing the timestamped copy, providing a stable "latest" path for integrations.

---

### Requirement 24: Database Schema Integrity

**User Story:** As a developer, I want the SQLite schema formally documented, so that I can identify the
relationship between tables and write correct joins.

#### Acceptance Criteria

1. THE `applications` Table SHALL be the root entity with columns: `id` (PK), `name`, `base_url`,
   `created_at`.
2. THE `app_versions` Table SHALL reference `applications.id` and contain columns: `id` (PK), `app_id`
   (FK), `label`, `created_at`, `is_active`.
3. THE `pages` Table SHALL reference `app_versions.id` and contain columns: `id` (PK), `version_id` (FK),
   `url_pattern`, `title`, `purpose`, `accessibility_snapshot` (JSON text).
4. THE `elements` Table SHALL reference `pages.id` and contain columns: `id` (PK), `page_id` (FK),
   `element_type`, `semantic_label`, `locators` (JSON), `validation_rules` (JSON), `observed_values`
   (JSON).
5. THE `user_flows` Table SHALL reference `app_versions.id` and contain columns: `id` (PK), `version_id`
   (FK), `name`, `description`, `start_url`, `steps` (JSON), `expected_outcome` (JSON).
6. THE `test_cases` Table SHALL reference `user_flows.id` and contain columns: `id` (PK), `flow_id` (FK),
   `name`, `category`, `steps` (JSON), `assertions` (JSON), `confidence`, `generated_at`.
7. THE `test_runs` Table SHALL reference `applications.id` and contain columns: `id` (PK), `app_id` (FK),
   `started_at`, `completed_at`, `total`, `passed`, `failed`, `errored`.
8. THE `test_results` Table SHALL reference both `test_runs.id` and `test_cases.id` and contain columns:
   `id` (PK), `run_id` (FK), `test_case_id` (FK), `test_name`, `category`, `status`, `duration_ms`,
   `step_results` (JSON), `assertion_results` (JSON), `error_detail`, `failure_screenshot`.
9. THE `llm_cache` Table SHALL contain columns: `input_hash` (PK), `response`, `model`, `created_at`.

---

### Requirement 25: Correctness Properties

**User Story:** As a quality engineer, I want the key correctness properties of the system formally
specified, so that property-based or integration tests can verify system invariants beyond unit coverage.

#### Acceptance Criteria

1. **Round-Trip: ApplicationModel Serialisation**
   FOR ALL `ApplicationModel` objects persisted via `Database.save_*`, THE Database
   SHALL produce an object via `Database.get_application()` that is structurally equal to the original
   (same page count, element count, flow count, and locator strategies).

2. **Round-Trip: TestCase Serialisation**
   FOR ALL `TestCase` objects persisted via `Database.save_test_case()`, THE Database
   SHALL produce an object via `Database.get_test_cases_for_app()` whose `steps`, `assertions`, and
   `category` are identical to the original.

3. **Idempotence: LLM Cache**
   FOR ALL prompts P and models M, calling `LLMClient.generate(P, M)` twice SHALL return the same
   result the second time without a network call (cache hit), provided the Groq API key and model string
   have not changed.

4. **Idempotence: learn re-run**
   WHEN `learn` is called twice for the same `app_name` and `url`, THE Database SHALL contain exactly one
   `applications` row with that `name` and two `app_versions` rows, with only the latest having
   `is_active = 1`.

5. **Invariant: Active Version Uniqueness**
   FOR ALL applications, THE `app_versions` Table SHALL contain at most one row with `is_active = 1` at
   any point in time; a call to `set_active_version(app_id, v)` SHALL set all other versions to
   `is_active = 0`.

6. **Invariant: Locator Confidence Ordering**
   FOR ALL `ElementModel` objects, THE `locators` list SHALL be sorted in descending order of `confidence`
   when generated by `generate_locators()` and when built by `ModelBuilder._codegen_step_to_locators()`.

7. **Invariant: TestCase Step Fingerprint Uniqueness**
   FOR ALL `TestCase` objects produced by `TestGenerator.generate()` for a single flow, no two test cases
   SHALL have the same tuple of `(element_id, value)` pairs across all steps.

8. **Invariant: Happy Path Preservation**
   THE `TestGenerator` SHALL produce exactly one `TestCase` with `category = "happy_path"` per flow, and
   that test case's steps SHALL be a sequence-renumbered copy of `flow.steps` with redundant click+fill
   pairs removed.

9. **Metamorphic: Locator Resolution Fallback**
   WHEN at least one `LocatorSpec` in an element's locator list with `confidence >= 0.85` resolves
   successfully, THE `resolve_locator` Function SHALL NOT call the LLM.

10. **Invariant: Test Isolation**
    FOR ALL concurrent test executions, each test SHALL use a separate Playwright `Browser` instance;
    no shared browser state SHALL persist between tests.

11. **Invariant: Diff Symmetry (near)**
    FOR ALL pairs of `ApplicationModel` versions `(A, B)`, `diff_models(A, B).new_pages` SHALL equal
    `diff_models(B, A).removed_pages` in URL pattern content (order may differ).

12. **Round-Trip: CodegenParser**
    FOR ALL Playwright codegen JS files that only contain `goto`, `getByRole`, `locator`, `getByText`,
    `getByLabel`, and `getByPlaceholder` patterns, parsing with `parse_playwright_js` and back-converting
    to action descriptions SHALL preserve the action type and primary locator value for every action step.

---

### Requirement 26: Known Limitations and Out-of-Scope Constraints

**User Story:** As a developer evaluating this system, I want all known limitations formally documented, so
that I can make informed decisions about what the agent can and cannot test.

#### Acceptance Criteria

1. THE Browser_Agent SHALL NOT support CAPTCHA mechanisms (reCAPTCHA, hCaptcha); such flows will cause
   test execution to stall and eventually time out.
2. THE Browser_Agent SHALL NOT support SMS or email OTP flows that require real-time credential access to
   external communication services.
3. THE Browser_Agent SHALL NOT interact with cross-origin iframes (e.g., Stripe payment forms, Google Maps
   embeds) due to browser security restrictions on cross-origin DOM access.
4. THE Browser_Agent SHALL NOT interact with Canvas or WebGL UI elements, as they are not accessible via
   the DOM or the accessibility tree.
5. THE Browser_Agent SHALL NOT support mobile browser emulation; all testing uses desktop Chromium only.
6. THE `BrowserRecorder` event capture SHALL NOT capture events inside cross-origin iframes (a limitation
   of the same-origin policy that applies to `window.__capturedEvents`).
7. THE `CodegenParser` SHALL NOT parse multi-line Playwright codegen statements; each locator chain MUST
   fit on a single line.
8. THE Excel_Reporter `format_steps` Function SHALL hardcode the login URL path for step 1; this MUST be
   updated manually if the target application's login path changes.
9. THE `SEMANTIC_ASSERTION_PROMPT` reasoning is best-effort and MAY produce false positives on asserting
   URL navigation success when the application redirects to an unexpected authenticated landing page.
10. THE LLMCache SHALL NOT expire cached responses; a stale cache entry will be returned indefinitely until
    the `llm_cache` table is manually cleared.
11. THE Database Class SHALL NOT use connection pooling; every CRUD operation opens and closes a new
    `aiosqlite` connection, which may become a bottleneck at high `PARALLEL_TESTS` values.
12. THE `relearn` Command SHALL use `BrowserRecorder` (raw-event mode) rather than `CodegenRecorder`, which
    produces lower-quality locators than the `learn` command's codegen path.

---

### Requirement 27: Dependency Constraints

**User Story:** As a deployment engineer, I want all external dependencies and their minimum versions
documented, so that I can provision the correct environment.

#### Acceptance Criteria

1. THE System SHALL require Python 3.11 or higher.
2. THE System SHALL require these exact packages (as of the audited source):
   - `playwright==1.51.0`
   - `groq>=0.9.0`
   - `aiosqlite==0.20.0`
   - `click==8.1.8`
   - `rich==13.9.4`
   - `pydantic==2.10.6`
   - `jinja2==3.1.5`
   - `tenacity==9.0.0`
   - `python-dotenv==1.0.1`
   - `openpyxl` (inferred from `reporting/excel_reporter.py`, version not pinned in requirements.txt)
3. THE System SHALL require Chromium installed via `playwright install chromium`.
4. THE `agent.py` Entry_Point SHALL fail gracefully if the `browser_agent/` directory is not in `sys.path`.

---

### Requirement 28: Module Responsibility Matrix

**User Story:** As a developer onboarding to this codebase, I want a table indexing every source module and
its exact responsibility, so that I know where to make each kind of change.

#### Acceptance Criteria

1. THE Documentation SHALL include a responsibility matrix with columns: Module Path, Primary
   Responsibility, Inputs, Outputs, Calls, Called By.

| Module Path | Primary Responsibility | Inputs | Outputs | Calls | Called By |
|-------------|------------------------|--------|---------|-------|-----------|
| `agent.py` | Entry point | Command-line args | Exit code | `cli.cli` | OS shell |
| `cli.py` | Click command definitions | CLI args, user input | Rich console output | `core/*`, `storage/db`, `llm/client`, `reporting/*` | `agent.py` |
| `config.py` | Environment variable loader | `.env` file | `Config` class with typed attrs | `dotenv.load_dotenv`, `os.environ` | All modules |
| `schema.py` | Pydantic v2 data models | None (type definitions) | Model classes | `pydantic.BaseModel` | All modules |
| `core/recorder.py` | Raw-event browser recorder | `start_url`, `app_name` | Session dict with events | `playwright`, `utils/screenshots` | `cli._relearn` |
| `core/codegen_recorder.py` | Playwright codegen wrapper | `start_url` | Raw JS string | `asyncio.subprocess` | `cli._learn` |
| `core/codegen_parser.py` | JS-to-step parser | Codegen JS string | `list[dict]` steps | `re` module | `cli._learn` |
| `core/model_builder.py` | ApplicationModel builder | Session data or codegen steps | `ApplicationModel` + DB persistence | `llm/client`, `storage/db`, `utils/locators` | `cli._learn`, `cli._relearn` |
| `core/test_generator.py` | Test case generator | `UserFlow`, `list[ElementModel]` | `list[TestCase]` | `llm/client`, `llm/prompts` | `cli._learn`, `cli._relearn` |
| `core/executor.py` | Autonomous test runner | `TestCase`, element map | `TestResult` | `playwright`, `utils/locators`, `core/verifier`, `utils/screenshots` | `cli._run_tests` |
| `core/verifier.py` | Assertion evaluator | `list[Assertion]`, page | `list[dict]` (assertion results) | `playwright`, `llm/client`, `llm/prompts` | `core/executor` |
| `llm/client.py` | Groq API wrapper | Prompt, model alias | Parsed JSON or text | `groq.AsyncGroq`, `llm/cache` | `core/model_builder`, `core/test_generator`, `core/verifier`, `utils/locators` |
| `llm/cache.py` | LLM response cache | Input hash | Cached JSON/text | `aiosqlite` | `llm/client` |
| `llm/prompts.py` | LLM prompt templates | None (string constants) | Formatted strings | None | All LLM-calling modules |
| `storage/db.py` | Async SQLite CRUD | Domain model objects | Same, persisted | `aiosqlite`, `schema` | All commands in `cli.py` |
| `storage/diff.py` | Version diff utility | Two `ApplicationModel` objects | Diff dict | `schema` | `cli._relearn`, `cli._diff` |
| `storage/schema.sql` | SQLite DDL | None (SQL script) | Created tables | None | `storage/db.initialize` |
| `utils/locators.py` | Locator generation + resolution | `ElementModel`, page | Playwright locator + strategy name | `playwright`, `llm/client` | `core/model_builder`, `core/executor` |
| `utils/screenshots.py` | Screenshot capture + encoding | Page, dir path | PNG path, base64 string | `playwright.Page.screenshot` | `core/recorder`, `core/executor`, `reporting/html_reporter` |
| `reporting/html_reporter.py` | HTML report builder | `run_id`, `list[TestResult]` | Self-contained HTML file | `jinja2`, `utils/screenshots`, `storage/db` | `cli._run_tests` |
| `reporting/json_reporter.py` | JSON report builder | `run_id`, `list[TestResult]` | JSON file | `json` stdlib | `cli._run_tests` |
| `reporting/excel_reporter.py` | Excel report builder | `run_id`, `list[TestResult]` | `.xlsx` file | `openpyxl`, `sqlite3`, `storage/db` | `cli._run_tests` |
| `assets/event_capture.js` | Browser event listener | None (runs in page) | `window.__capturedEvents` array | DOM API | `core/recorder` |

---

### Requirement 29: File and Directory Layout

**User Story:** As a developer, I want the canonical directory structure formally documented, so that I know
where each category of file lives and where new components should be placed.

#### Acceptance Criteria

1. THE Project Root SHALL contain: `agent.py`, `cli.py`, `config.py`, `schema.py`, `requirements.txt`,
   `README.md`, `.env.example`, `.gitignore`.
2. THE `core/` Directory SHALL contain: `recorder.py`, `codegen_recorder.py`, `codegen_parser.py`,
   `model_builder.py`, `test_generator.py`, `executor.py`, `verifier.py`, `__init__.py`.
3. THE `llm/` Directory SHALL contain: `client.py`, `cache.py`, `prompts.py`, `__init__.py`.
4. THE `storage/` Directory SHALL contain: `db.py`, `diff.py`, `schema.sql`, `__init__.py`.
5. THE `utils/` Directory SHALL contain: `locators.py`, `screenshots.py`, `__init__.py`.
6. THE `reporting/` Directory SHALL contain: `html_reporter.py`, `json_reporter.py`, `excel_reporter.py`,
   `templates/report.html`, `__init__.py`.
7. THE `assets/` Directory SHALL contain: `event_capture.js`.
8. THE `reports/` Directory SHALL be created at runtime and contain HTML, JSON, and Excel files.
9. THE `screenshots/` Directory SHALL be created at runtime and contain PNG files.
10. THE `browser_agent.db` File SHALL be created at runtime in the project root (or at `DB_PATH`).

---

## Acceptance Summary

This requirements document indexes **29 major functional areas** of the Browser Agent system, encompassing:

- **6 CLI commands** (`learn`, `test`, `relearn`, `report`, `diff`, `status`)
- **2 recording modes** (raw-event + codegen)
- **1 parser** (regex-based JS tokeniser)
- **1 model builder** (LLM-driven structure generator)
- **1 test generator** (segment-based LLM prompter)
- **1 test executor** (Playwright parallel runner)
- **1 assertion verifier** (with LLM fallback)
- **3 report formats** (HTML, JSON, Excel)
- **1 database layer** (async SQLite with JSON serialisation)
- **1 LLM client** (Groq + cache + retry)
- **12 correctness properties** (round-trip, idempotence, invariants)
- **12 known limitations** (CAPTCHA, OTP, iframes, Canvas, mobile, etc.)

All data flows, component responsibilities, and inter-module calls are now fully traced and indexed.
