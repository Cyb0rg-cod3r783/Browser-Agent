"""
core/executor.py — Autonomously executes test cases using Playwright.
"""
import asyncio
import os
import uuid
from datetime import datetime

from playwright.async_api import async_playwright, Browser, BrowserContext

from schema import (
    TestCase, TestResult, StepResult, ElementModel, Assertion
)
from utils.locators import resolve_locator, ElementNotFoundError
from utils.screenshots import capture_screenshot
from core.verifier import evaluate_assertions


class TestExecutor:
    def __init__(self, db, llm_client, screenshots_dir: str, headless: bool = True):
        self.db = db
        self.llm_client = llm_client
        self.screenshots_dir = screenshots_dir
        self.headless = headless
        self.parallel_tests = int(os.environ.get("PARALLEL_TESTS", "4"))
        self.navigation_timeout_ms = int(
            os.environ.get("NAVIGATION_TIMEOUT_MS", "60000")
        )

    async def run_suite(self, app_id: str) -> list[TestResult]:
        """
        Run all test cases for an app.
        
        Returns:
            List of TestResult objects
        """
        # Load app and test cases
        app = await self.db.get_application(app_id)
        if not app:
            raise ValueError(f"Application not found: {app_id}")

        test_cases = await self.db.get_test_cases_for_app(app_id)
        if not test_cases:
            return []

        # Build element lookup map
        elements_map: dict[str, ElementModel] = {
            e.id: e for e in app.elements
        }

        # Build flow lookup map for start URLs
        flows_map = {f.id: f for f in app.flows}

        # Create test run record
        run_id = await self.db.save_test_run({
            "id": str(uuid.uuid4()),
            "app_id": app_id,
            "started_at": datetime.utcnow().isoformat(),
            "total": len(test_cases),
            "passed": 0,
            "failed": 0,
            "errored": 0
        })

        # Run tests in parallel batches
        results: list[TestResult] = []
        semaphore = asyncio.Semaphore(self.parallel_tests)

        total = len(test_cases)
        completed = 0
        results = []

        async def run_and_log(tc: TestCase) -> TestResult:
            nonlocal completed
            async with semaphore:
                flow = flows_map.get(tc.flow_id)
                start_url = flow.start_url if flow else app.base_url
                result = await self.run_test(tc, elements_map, run_id, start_url)
                completed += 1
                status_icon = "✓" if result.status == "passed" else ("✗" if result.status == "failed" else "⚠")
                print(f"[{completed}/{total}] {status_icon} {result.status.upper():8s} | {result.test_name} ({result.duration_ms}ms)", flush=True)
                if result.status in ("failed", "errored"):
                    if result.error_detail:
                        print(f"         ERROR: {result.error_detail[:120]}", flush=True)
                    elif result.step_results:
                        for sr in result.step_results:
                            if sr.status in ("failed", "errored") and sr.error:
                                print(f"         STEP {sr.sequence} [{sr.element_label}]: {sr.error[:120]}", flush=True)
                return result

        print(f"\n▶ Starting test suite: {total} tests (parallelism={self.parallel_tests})\n", flush=True)
        tasks = [run_and_log(tc) for tc in test_cases]
        results = list(await asyncio.gather(*tasks))

        # Tally results
        passed = sum(1 for r in results if r.status == "passed")
        failed = sum(1 for r in results if r.status == "failed")
        errored = sum(1 for r in results if r.status == "errored")

        await self.db.update_test_run(
            run_id,
            completed_at=datetime.utcnow().isoformat(),
            passed=passed,
            failed=failed,
            errored=errored
        )

        # Save all results
        for result in results:
            await self.db.save_test_result(result)

        return list(results)

    async def run_test(
        self,
        test_case: TestCase,
        elements: dict[str, ElementModel],
        run_id: str,
        start_url: str
    ) -> TestResult:
        """
        Execute a single test case. Never raises — always returns a TestResult.
        
        Returns:
            TestResult with status "passed", "failed", or "errored"
        """
        start_time = datetime.utcnow()
        step_results: list[StepResult] = []
        assertion_results: list[dict] = []
        failure_screenshot: str | None = None
        error_detail: str | None = None
        overall_status = "passed"

        playwright_instance = None
        browser: Browser | None = None
        context: BrowserContext | None = None
        page = None

        try:
            playwright_instance = await async_playwright().start()
            browser = await playwright_instance.chromium.launch(headless=self.headless)
            context = await browser.new_context()
            page = await context.new_page()
            page.set_default_timeout(self.navigation_timeout_ms)
            page.set_default_navigation_timeout(self.navigation_timeout_ms)

            # Navigate to start URL
            try:
                await page.goto(
                    start_url,
                    timeout=self.navigation_timeout_ms,
                    wait_until="domcontentloaded"  # faster than default "load"
                )
            except Exception as e:
                error_detail = f"Failed to navigate to {start_url}: {e}"
                overall_status = "errored"
                return TestResult(
                    id=str(uuid.uuid4()),
                    run_id=run_id,
                    test_case_id=test_case.id,
                    test_name=test_case.name,
                    category=test_case.category,
                    status="errored",
                    duration_ms=int((datetime.utcnow() - start_time).total_seconds() * 1000),
                    step_results=[],
                    assertion_results=[],
                    error_detail=error_detail,
                    failure_screenshot=None
                )

            # Execute each step
            for step in test_case.steps:
                step_result = StepResult(
                    sequence=step.sequence,
                    action=step.action,
                    element_label=None,
                    status="passed",
                    locator_used=None,
                    error=None,
                    screenshot_path=None
                )

                try:
                    if step.action == "navigate":
                        await page.goto(
                            step.url or start_url,
                            timeout=self.navigation_timeout_ms,
                            wait_until="domcontentloaded"
                        )

                    else:
                        # Resolve element
                        element = elements.get(step.element_id) if step.element_id else None

                        if element:
                            step_result.element_label = element.semantic_label
                            locator, strategy_used = await resolve_locator(
                                page, element, self.llm_client,
                                timeout_ms=10000
                            )
                            step_result.locator_used = strategy_used

                            # Execute action
                            await self._execute_action(page, locator, step)

                            # Wait for network idle (longer timeout to stabilize on slow staging)
                            try:
                                await page.wait_for_load_state(
                                    "networkidle",
                                    timeout=5000
                                )
                            except Exception:
                                pass

                        else:
                            # No element reference — skip step
                            step_result.status = "skipped"
                            step_result.error = f"Element ID {step.element_id} not found in model"

                    # Take screenshot
                    screenshot_path = await capture_screenshot(
                        page,
                        self.screenshots_dir,
                        label=f"step_{step.sequence}"
                    )
                    step_result.screenshot_path = screenshot_path

                except ElementNotFoundError as e:
                    step_result.status = "failed"
                    step_result.error = str(e)
                    overall_status = "failed"

                    failure_screenshot = await capture_screenshot(
                        page, self.screenshots_dir,
                        label=f"failure_step_{step.sequence}"
                    )
                    step_results.append(step_result)
                    break

                except Exception as e:
                    step_result.status = "errored"
                    step_result.error = str(e)
                    if overall_status == "passed":
                        overall_status = "errored"

                    failure_screenshot = await capture_screenshot(
                        page, self.screenshots_dir,
                        label=f"error_step_{step.sequence}"
                    )
                    step_results.append(step_result)
                    break

                step_results.append(step_result)

            # Evaluate assertions
            if overall_status != "errored":
                try:
                    assertion_results = await evaluate_assertions(
                        page,
                        test_case.assertions,
                        elements,
                        self.llm_client,
                        test_case.name
                    )
                    # If any assertion failed, mark test as failed
                    if any(not ar.get("passed", False) for ar in assertion_results):
                        if overall_status == "passed":
                            overall_status = "failed"
                            failure_screenshot = failure_screenshot or await capture_screenshot(
                                page, self.screenshots_dir,
                                label=f"assertion_failure"
                            )
                except Exception as e:
                    error_detail = f"Assertion evaluation failed: {e}"

        except Exception as e:
            error_detail = str(e)
            overall_status = "errored"

        finally:
            if page:
                try:
                    await page.close()
                except Exception:
                    pass
            if context:
                try:
                    await context.close()
                except Exception:
                    pass
            if browser:
                try:
                    await browser.close()
                except Exception:
                    pass
            if playwright_instance:
                try:
                    await playwright_instance.stop()
                except Exception:
                    pass

        duration_ms = int((datetime.utcnow() - start_time).total_seconds() * 1000)

        return TestResult(
            id=str(uuid.uuid4()),
            run_id=run_id,
            test_case_id=test_case.id,
            test_name=test_case.name,
            category=test_case.category,
            status=overall_status,
            duration_ms=duration_ms,
            step_results=step_results,
            assertion_results=assertion_results,
            error_detail=error_detail,
            failure_screenshot=failure_screenshot
        )

    async def _execute_action(self, page, locator, step):
        """Execute a single step action on a locator."""
        action = step.action
        value = step.value or ""

        if action == "fill":
            try:
                await locator.evaluate("el => el.removeAttribute('readonly')")
            except Exception:
                pass
            try:
                await locator.click(timeout=2000)
            except Exception:
                pass
            await locator.fill(value)
        elif action == "click":
            await locator.click()
        elif action == "check":
            await locator.check()
        elif action == "select":
            await locator.select_option(value)
        elif action == "hover":
            await locator.hover()
        elif action == "clear":
            await locator.clear()
        else:
            # Default to click for unknown actions
            await locator.click()
