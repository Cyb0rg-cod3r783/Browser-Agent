# Implementation Plan: Browser Agent Project Index and Analysis

## Overview

This implementation plan focuses on adding comprehensive property-based testing infrastructure to validate the 12 correctness properties documented in the design, fixing known gaps in test infrastructure, and ensuring the Browser Agent's core invariants are machine-verifiable. The plan uses `hypothesis` for property-based testing and `pytest` as the test runner.

## Tasks

- [ ] 1. Set up property-based testing infrastructure
  - [ ] 1.1 Install hypothesis and pytest testing frameworks
    - Add `hypothesis>=6.98.0` and `pytest>=8.0.0` to requirements.txt
    - Create `pytest.ini` with test discovery configuration
    - Create `tests/` directory structure with `__init__.py`
    - _Requirements: Known Gap 2_

  - [ ] 1.2 Create test configuration and fixtures module
    - Create `tests/conftest.py` with shared fixtures
    - Configure hypothesis profile with `max_examples=100`
    - Set up test database fixture for DB-dependent tests
    - _Requirements: Known Gap 2_

- [ ] 2. Implement property tests for configuration and schema (Properties 1-3)
  - [ ] 2.1 Create property test for Config integer parameter validation
    - **Property 1: Config integer parameters reject non-integer strings**
    - **Validates: Requirements 2.4**
    - Test file: `tests/test_config_properties.py`
    - Generate arbitrary non-integer strings with hypothesis
    - Verify ValueError raised for MAX_LLM_RETRIES, LOCATOR_TIMEOUT_MS, NAVIGATION_TIMEOUT_MS, PARALLEL_TESTS
    - _Requirements: 2.4_

  - [ ] 2.2 Create property test for LocatorSpec confidence bounds
    - **Property 2: LocatorSpec confidence bounds**
    - **Validates: Requirements 3.2**
    - Test file: `tests/test_schema_properties.py`
    - Generate floats inside and outside [0.0, 1.0] range
    - Verify Pydantic validation error for out-of-bounds values
    - _Requirements: 3.2_

  - [ ] 2.3 Create property test for schema enumeration field validation
    - **Property 3: Schema enumeration fields reject invalid values**
    - **Validates: Requirements 3.3, 3.4, 3.5, 3.6**
    - Test file: `tests/test_schema_properties.py`
    - Generate arbitrary strings and test against ElementModel.element_type, TestCase.category, TestResult.status, StepResult.status
    - Verify only documented enum values are accepted
    - _Requirements: 3.3, 3.4, 3.5, 3.6_

- [ ] 3. Implement property tests for CodegenParser (Property 4)
  - [ ] 3.1 Create property test for CodegenParser round-trip fidelity
    - **Property 4: CodegenParser round-trip fidelity**
    - **Validates: Requirements 7.1, 7.2, 7.3, 7.4, 7.5, 7.6, 7.7, 7.8**
    - Test file: `tests/test_codegen_parser_properties.py`
    - Generate valid Playwright JS lines for all 8 locator patterns
    - Parse each line and verify extracted fields match input construction
    - Cover: goto, getByRole (with/without name), getByLabel, getByPlaceholder, getByText, locator, chained_css_role
    - Include nth modifier variations (.first(), .nth(N))
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 7.6, 7.7, 7.8_

- [ ] 4. Implement property tests for locator generation (Properties 5-6)
  - [ ] 4.1 Create property test for locator confidence ordering
    - **Property 5: Locator generation confidence ordering**
    - **Validates: Requirements 9.1**
    - Test file: `tests/test_locators_properties.py`
    - Generate arbitrary element attribute dicts
    - Verify returned LocatorSpec list is sorted by descending confidence
    - Verify all confidence values are in [0.0, 1.0]
    - _Requirements: 9.1_

  - [ ] 4.2 Create property test for auto-generated ID exclusion
    - **Property 6: Auto-generated ID exclusion**
    - **Validates: Requirements 9.2**
    - Test file: `tests/test_locators_properties.py`
    - Generate element dicts with IDs matching auto-generated patterns: numeric, UUID-like, react-*, ember-*
    - Verify no LocatorSpec with strategy="id" is returned
    - _Requirements: 9.2_

- [ ] 5. Implement property tests for LLM cache layer (Properties 7-8)
  - [ ] 5.1 Create property test for LLM cache idempotence
    - **Property 7: LLM response cache idempotence**
    - **Validates: Requirements 11.3**
    - Test file: `tests/test_llm_cache_properties.py`
    - Generate arbitrary (hash, response, model) triples
    - Call set() twice with same hash
    - Verify exactly one row exists and get() returns latest value
    - _Requirements: 11.3_

  - [ ] 5.2 Create property test for LLM cache hit avoids API call
    - **Property 8: LLM cache hit avoids API call**
    - **Validates: Requirements 10.2**
    - Test file: `tests/test_llm_client_properties.py`
    - Mock Groq API with call counter
    - Generate arbitrary prompts, cache them, call LLMClient.generate twice
    - Verify second call makes zero API requests
    - _Requirements: 10.2_

- [ ] 6. Implement property tests for version diff utility (Properties 9-10)
  - [ ] 6.1 Create property test for diff_models identity property
    - **Property 9: diff_models identity property**
    - **Validates: Requirements 17.5**
    - Test file: `tests/test_diff_properties.py`
    - Generate arbitrary ApplicationModel instances
    - Call diff_models(m, m) and verify all 8 lists are empty
    - _Requirements: 17.5_

  - [ ] 6.2 Create property test for diff_models structural correctness
    - **Property 10: diff_models structural correctness**
    - **Validates: Requirements 17.1, 17.2, 17.3, 17.4**
    - Test file: `tests/test_diff_properties.py`
    - Generate pairs of ApplicationModel instances with known differences
    - Verify items appear in new_*/removed_* lists if and only if they satisfy membership conditions
    - Test pages (by url_pattern), elements (by semantic_label), flows (by name)
    - _Requirements: 17.1, 17.2, 17.3, 17.4_

- [ ] 7. Implement property tests for report generation (Properties 11-12)
  - [ ] 7.1 Create property test for JSON report structure completeness
    - **Property 11: JSON report structure completeness**
    - **Validates: Requirements 19.1, 19.2**
    - Test file: `tests/test_json_reporter_properties.py`
    - Generate arbitrary lists of TestResult objects (including empty list)
    - Verify output is valid JSON with exactly 6 top-level keys
    - Verify test_results array length matches input length
    - _Requirements: 19.1, 19.2_

  - [ ] 7.2 Create property test for Excel report sort order
    - **Property 12: Excel report sort order**
    - **Validates: Requirements 20**
    - Test file: `tests/test_excel_reporter_properties.py`
    - Generate unsorted lists of TestResult with mixed categories
    - Verify Excel rows appear with "Happy Path" tests first, then lexicographically sorted
    - Parse generated .xlsx file with openpyxl to verify order
    - _Requirements: 20_

- [ ] 8. Checkpoint - Ensure all property tests pass
  - Run `pytest tests/ -v` to execute all property tests
  - Verify all 12 properties pass with hypothesis default settings
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 9. Fix known gaps and technical debt
  - [ ] 9.1 Remove unused FAILURE_DIAGNOSIS_PROMPT
    - Delete or document the unused prompt constant in `llm/prompts.py`
    - Remove or mark as deprecated in documentation
    - _Requirements: Known Gap 3_

  - [ ] 9.2 Remove unused monolithic TEST_GENERATION_PROMPT
    - Delete or document the superseded prompt constant in `llm/prompts.py`
    - Update documentation to reflect SEGMENT_TEST_GENERATION_PROMPT as the active approach
    - _Requirements: Known Gap 4_

  - [ ] 9.3 Fix hardcoded test values in HTML reporter
    - Replace hardcoded `admin@talakunchi.com` and `Test#123` in html_reporter.py get_step_value()
    - Use placeholder values or configuration-driven fallbacks
    - _Requirements: Known Gap 5_

  - [ ] 9.4 Fix semantic fallback log location
    - Move `semantic_fallback_debug.log` output to REPORTS_DIR instead of current working directory
    - Add log rotation or size-cap mechanism (max 10MB or 1000 lines)
    - _Requirements: Known Gap 8_

- [ ] 10. Add integration tests for critical paths
  - [ ] 10.1 Create integration test for locator resolution order
    - Test file: `tests/integration/test_locator_resolution.py`
    - Mock page with controlled failures per strategy
    - Verify resolve_locator tries strategies in confidence order
    - Verify LLM fallback is invoked after all static strategies fail
    - _Requirements: 9.3, 9.4, 9.5_

  - [ ] 10.2 Create integration test for LLM retry with rate limits
    - Test file: `tests/integration/test_llm_retry.py`
    - Mock Groq API to return RateLimitError N times
    - Verify exponential backoff behavior (6 retries, 2x multiplier, min 4s, max 65s)
    - Verify eventual success after transient failures
    - _Requirements: 10.3_

- [ ] 11. Add example-based unit tests for core components
  - [ ] 11.1 Create unit tests for assertion verifier
    - Test file: `tests/unit/test_verifier.py`
    - Test all 5 assertion types: url_contains, element_visible, text_equals, element_count, element_absent
    - Test happy path and failure path for each type
    - Test LLM semantic fallback invocation on strict failure
    - _Requirements: 15.1, 15.2, 15.3, 15.4, 15.5, 15.6, 15.7, 15.8, 15.9_

  - [ ] 11.2 Create unit tests for test generator
    - Test file: `tests/unit/test_test_generator.py`
    - Test happy path construction from flow steps
    - Test redundant click+fill removal
    - Test segment grouping (groups of 3 input elements)
    - Test deduplication by (element_id, value) fingerprint
    - _Requirements: 13.1, 13.2, 13.3, 13.4, 13.5, 13.6, 13.7_

- [ ] 12. Create CI/CD workflow for property tests
  - [ ] 12.1 Create GitHub Actions workflow for test execution
    - Create `.github/workflows/property-tests.yml`
    - Run pytest with coverage reporting on every push
    - Fail CI if any property test fails
    - Upload coverage report as artifact
    - _Requirements: Known Gap 2_

- [ ] 13. Final checkpoint - Complete test suite validation
  - Run full test suite: `pytest tests/ -v --cov=browser_agent --cov-report=html`
  - Verify all 12 property tests pass
  - Verify integration tests pass
  - Verify unit tests pass
  - Ensure coverage is above 70% for core modules (schema, parser, locators, cache, diff, reporters)
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP (none in this plan - all tasks are essential for correctness validation)
- Property tests use `hypothesis` with minimum 100 examples per test
- Integration tests use mocked Playwright pages and Groq API clients
- All test files include property number and requirements traceability in comments
- Test infrastructure is required before property tests can be created
- Known gaps are addressed as technical debt cleanup tasks after core property tests are complete

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["1.2"] },
    { "id": 2, "tasks": ["2.1", "2.2", "2.3", "3.1"] },
    { "id": 3, "tasks": ["4.1", "4.2", "5.1", "5.2"] },
    { "id": 4, "tasks": ["6.1", "6.2", "7.1", "7.2"] },
    { "id": 5, "tasks": ["9.1", "9.2", "9.3", "9.4"] },
    { "id": 6, "tasks": ["10.1", "10.2", "11.1", "11.2"] },
    { "id": 7, "tasks": ["12.1"] }
  ]
}
```
