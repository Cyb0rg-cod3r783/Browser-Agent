"""
cli.py — All Click CLI commands for the Browser Agent.
Implements: learn, test, relearn, report, diff, status commands.
"""
import asyncio
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import click
from dotenv import load_dotenv
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from rich.table import Table
from rich.panel import Panel
from rich import print as rprint

# Load .env from the same directory as cli.py
load_dotenv(dotenv_path=Path(__file__).parent / ".env")

console = Console()


def _get_db():
    """Initialize and return a Database instance."""
    from storage.db import Database
    db_path = os.environ.get("DB_PATH", "./browser_agent.db")
    return Database(db_path)


def _get_llm():
    """Initialize and return an LLMClient."""
    from llm.client import LLMClient
    db_path = os.environ.get("DB_PATH", "./browser_agent.db")
    return LLMClient(db_path=db_path)


@click.group()
def cli():
    """🤖 Browser Agent — AI-Powered Functional Testing"""
    pass


# ─── learn ───────────────────────────────────────────────────────────────────

@cli.command()
@click.option("--app", required=True, help="Application name")
@click.option("--url", required=True, help="Starting URL to record")
def learn(app: str, url: str):
    """Record a user session and generate test cases."""
    asyncio.run(_learn(app, url))


async def _learn(app_name: str, url: str):
    from core.codegen_recorder import CodegenRecorder
    from core.codegen_parser import parse_playwright_js
    from core.model_builder import ModelBuilder
    from core.test_generator import TestGenerator

    db = _get_db()
    await db.initialize()
    llm = _get_llm()

    console.print(Panel(
        f"[bold green]🎥 Playwright Codegen Recording[/bold green]\n"
        f"[dim]URL:[/dim] {url}\n"
        f"[dim]App:[/dim] {app_name}\n\n"
        "[yellow]A Chromium window will open. Interact with the app normally.\n"
        "Close the browser window when you are done recording.[/yellow]",
        title="Browser Agent — LEARN Mode",
        border_style="green"
    ))

    # Launch playwright codegen and wait for user to finish
    recorder = CodegenRecorder(url, app_name)
    js_code = await recorder.record()

    if not js_code.strip():
        console.print("[red]❌ No actions were captured. Did you close the browser without interacting?[/red]")
        return

    # Parse the generated JS into structured steps
    parsed_steps = parse_playwright_js(js_code)
    action_steps = [s for s in parsed_steps if s["step_type"] == "action"]
    nav_steps = [s for s in parsed_steps if s["step_type"] == "navigate"]

    console.print(f"\n[green]✅ Recording complete[/green] — {len(action_steps)} actions, {len(nav_steps)} navigations")

    if not action_steps:
        console.print("[red]❌ No actions detected in the recording. Please interact with the page before closing.[/red]")
        return

    # Build the ApplicationModel from codegen steps
    with console.status("[bold cyan]🧠 Analyzing recording with LLM..."):
        try:
            builder = ModelBuilder()
            app_model = await builder.build_from_codegen(parsed_steps, app_name, db, llm)
        except Exception as e:
            console.print(f"[red]❌ Model building failed: {e}[/red]")
            raise


    console.print(f"[green]✅ Application model built[/green]")

    # Generate test cases
    with console.status("[bold cyan]📝 Generating test cases..."):
        generator = TestGenerator()
        all_test_cases = []
        for flow in app_model.flows:
            flow_elements = [e for e in app_model.elements]
            test_cases = await generator.generate(flow, flow_elements, llm)
            for tc in test_cases:
                await db.save_test_case(tc)
            all_test_cases.extend(test_cases)

    # Summary table
    table = Table(title="📊 Learn Session Summary", border_style="bright_black")
    table.add_column("Metric", style="dim")
    table.add_column("Value", style="bold green")
    table.add_row("Pages discovered", str(len(app_model.pages)))
    table.add_row("Elements modeled", str(len(app_model.elements)))
    table.add_row("Flows identified", str(len(app_model.flows)))
    table.add_row("Test cases generated", str(len(all_test_cases)))

    category_counts = {}
    for tc in all_test_cases:
        category_counts[tc.category] = category_counts.get(tc.category, 0) + 1
    for cat, count in category_counts.items():
        table.add_row(f"  └─ {cat}", str(count))

    console.print(table)
    console.print(f"\n[bold green]✅ App '{app_name}' is ready for testing![/bold green]")
    console.print(f"[dim]Run:[/dim] python agent.py test --app {app_name}")


# ─── test ────────────────────────────────────────────────────────────────────

@cli.command(name="test")
@click.option("--app", required=True, help="Application name")
@click.option("--headless/--no-headless", default=True, help="Run browser in headless mode")
@click.option("--suite", default=None, help="Filter by category: happy_path, negative, edge_case")
def run_tests(app: str, headless: bool, suite: str):
    """Execute all test cases for an application."""
    asyncio.run(_run_tests(app, headless, suite))


async def _run_tests(app_name: str, headless: bool, suite: str | None):
    from core.executor import TestExecutor
    from reporting.html_reporter import generate_html_report
    from reporting.json_reporter import generate_json_report

    screenshots_dir = os.environ.get("SCREENSHOTS_DIR", "./screenshots")
    reports_dir = os.environ.get("REPORTS_DIR", "./reports")
    db = _get_db()
    await db.initialize()

    app = await db.get_application_by_name(app_name)
    if not app:
        console.print(f"[red]❌ App '{app_name}' not found. Run 'learn' first.[/red]")
        return

    test_cases = await db.get_test_cases_for_app(app.id)
    if suite:
        test_cases = [tc for tc in test_cases if tc.category == suite]

    if not test_cases:
        console.print(f"[yellow]⚠ No test cases found for '{app_name}'.[/yellow]")
        return

    console.print(Panel(
        f"[bold]Running [cyan]{len(test_cases)}[/cyan] test cases for [green]{app_name}[/green][/bold]\n"
        f"[dim]Headless: {headless} | Suite: {suite or 'all'}[/dim]",
        title="Browser Agent — TEST Mode",
        border_style="blue"
    ))

    llm = _get_llm()
    executor = TestExecutor(db, llm, screenshots_dir, headless=headless)

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console
    ) as progress:
        task = progress.add_task("Running tests...", total=len(test_cases))
        
        results = []
        semaphore = asyncio.Semaphore(int(os.environ.get("PARALLEL_TESTS", "4")))
        
        import uuid
        run_id = await db.save_test_run({
            "id": str(uuid.uuid4()),
            "app_id": app.id,
            "started_at": datetime.utcnow().isoformat(),
            "total": len(test_cases),
        })
        
        elements_map = {e.id: e for e in app.elements}
        flows_map = {f.id: f for f in app.flows}
        
        async def run_one(tc):
            async with semaphore:
                flow = flows_map.get(tc.flow_id)
                start_url = flow.start_url if flow else app.base_url
                progress.update(task, description=f"▶ {tc.name[:45]}...")
                result = await executor.run_test(tc, elements_map, run_id, start_url)
                await db.save_test_result(result)
                progress.advance(task)
                return result
        
        results = list(await asyncio.gather(*[run_one(tc) for tc in test_cases]))

    # Update run totals
    passed = sum(1 for r in results if r.status == "passed")
    failed = sum(1 for r in results if r.status == "failed")
    errored = sum(1 for r in results if r.status == "errored")

    await db.update_test_run(
        run_id,
        completed_at=datetime.utcnow().isoformat(),
        passed=passed,
        failed=failed,
        errored=errored
    )

    # Print result table
    result_table = Table(title="📊 Test Results", border_style="bright_black")
    result_table.add_column("Test Name", style="dim", max_width=50)
    result_table.add_column("Category", style="dim")
    result_table.add_column("Status")
    result_table.add_column("Duration")

    for r in results:
        status_str = {
            "passed": "[green]✓ PASS[/green]",
            "failed": "[red]✗ FAIL[/red]",
            "errored": "[yellow]⚠ ERROR[/yellow]"
        }.get(r.status, r.status)
        result_table.add_row(
            r.test_name[:50],
            r.category,
            status_str,
            f"{r.duration_ms}ms"
        )

    console.print(result_table)

    # Summary
    total = len(results)
    pass_rate = (passed / total * 100) if total > 0 else 0
    console.print(f"\n[bold]Summary:[/bold] {passed}/{total} passed ({pass_rate:.0f}%) | "
                  f"[red]{failed} failed[/red] | [yellow]{errored} errored[/yellow]")

    # Generate reports
    Path(reports_dir).mkdir(parents=True, exist_ok=True)
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    html_path = str(Path(reports_dir) / f"{app_name}_{timestamp}.html")
    json_path = str(Path(reports_dir) / f"{app_name}_{timestamp}.json")
    excel_path = str(Path(reports_dir) / f"{app_name}_{timestamp}.xlsx")

    with console.status("Generating reports..."):
        await generate_html_report(run_id, results, db, html_path)
        generate_json_report(run_id, results, json_path, app_name)
        
        # Generate Excel report
        try:
            from reporting.excel_reporter import generate_excel_report
            await generate_excel_report(run_id, results, db, excel_path, app_name)
            
            # Save a copy to the root workspace
            root_excel_path = f"./{app_name}_test_report.xlsx"
            import shutil
            shutil.copy2(excel_path, root_excel_path)
            latest_excel_msg = f" (latest: [link]{root_excel_path}[/link])"
        except Exception as e:
            latest_excel_msg = f" (failed to generate Excel: {e})"

    console.print(f"\n[bold green]📄 Reports generated:[/bold green]")
    console.print(f"  HTML: [link]{html_path}[/link]")
    console.print(f"  JSON: [link]{json_path}[/link]")
    console.print(f"  Excel: [link]{excel_path}[/link]{latest_excel_msg}")
    console.print(f"\n[dim]Run:[/dim] python agent.py report --app {app_name}")


# ─── relearn ─────────────────────────────────────────────────────────────────

@cli.command()
@click.option("--app", required=True, help="Application name")
def relearn(app: str):
    """Re-record a session and update the application model."""
    asyncio.run(_relearn(app))


async def _relearn(app_name: str):
    from core.recorder import BrowserRecorder
    from core.model_builder import ModelBuilder
    from core.test_generator import TestGenerator
    from storage.diff import diff_models

    db = _get_db()
    await db.initialize()

    old_app = await db.get_application_by_name(app_name)
    if not old_app:
        console.print(f"[red]❌ App '{app_name}' not found. Run 'learn' first.[/red]")
        return

    base_url = old_app.base_url
    screenshots_dir = os.environ.get("SCREENSHOTS_DIR", "./screenshots")
    llm = _get_llm()

    console.print(Panel(
        f"[bold yellow]🎥 Re-recording for '{app_name}'[/bold yellow]\n"
        f"[dim]URL:[/dim] {base_url}\n\n"
        "[yellow]Use the app normally. Press Ctrl+C when done.[/yellow]",
        title="Browser Agent — RELEARN Mode",
        border_style="yellow"
    ))

    recorder = BrowserRecorder(base_url, app_name, screenshots_dir)
    try:
        await asyncio.wait_for(recorder.start_session(), timeout=3600)
    except (KeyboardInterrupt, asyncio.CancelledError, asyncio.TimeoutError):
        pass
    finally:
        with console.status("[yellow]Saving session..."):
            session_data = await recorder.stop_session()

    with console.status("[bold cyan]🧠 Building new model..."):
        builder = ModelBuilder()
        new_app = await builder.build(session_data, app_name, db, llm)

    # Run diff
    diff = diff_models(old_app, new_app)

    # Print diff table
    diff_table = Table(title="📊 Model Diff", border_style="bright_black")
    diff_table.add_column("Change Type", style="dim")
    diff_table.add_column("Item")
    diff_table.add_column("Details", style="dim")

    for p in diff["new_pages"]:
        diff_table.add_row("[green]+ New Page[/green]", p["url_pattern"], p.get("title", ""))
    for p in diff["removed_pages"]:
        diff_table.add_row("[red]- Removed Page[/red]", p["url_pattern"], p.get("title", ""))
    for e in diff["new_elements"]:
        diff_table.add_row("[green]+ New Element[/green]", e["label"], f"on {e['page']}")
    for e in diff["removed_elements"]:
        diff_table.add_row("[red]- Removed Element[/red]", e["label"], e.get("type", ""))
    for e in diff["changed_elements"]:
        diff_table.add_row("[yellow]~ Changed Element[/yellow]", e["label"], "; ".join(e["changes"]))
    for f in diff["new_flows"]:
        diff_table.add_row("[green]+ New Flow[/green]", f["name"], f.get("description", "")[:50])
    for f in diff["removed_flows"]:
        diff_table.add_row("[red]- Removed Flow[/red]", f["name"], "")

    if diff_table.row_count == 0:
        diff_table.add_row("[dim]No changes[/dim]", "", "")

    console.print(diff_table)

    # Ask user to activate
    activate = click.confirm("\n✅ Mark new version as active?", default=False)
    if activate:
        await db.set_active_version(old_app.id, new_app.version.id)

        with console.status("[bold cyan]📝 Regenerating test cases..."):
            generator = TestGenerator()
            for flow in new_app.flows:
                test_cases = await generator.generate(flow, new_app.elements, llm)
                for tc in test_cases:
                    await db.save_test_case(tc)

        console.print(f"[bold green]✅ New version '{new_app.version.label}' is now active.[/bold green]")
    else:
        console.print("[dim]New version not activated.[/dim]")


# ─── report ──────────────────────────────────────────────────────────────────

@cli.command()
@click.option("--app", required=True, help="Application name")
@click.option("--run", default=None, help="Specific run ID (default: last run)")
@click.option("--excel", is_flag=True, help="Generate and open the Excel report instead of HTML")
def report(app: str, run: str, excel: bool):
    """Open the HTML or Excel report for the last test run."""
    asyncio.run(_report(app, run, excel))


async def _report(app_name: str, run_id: str | None, excel: bool):
    db = _get_db()
    await db.initialize()

    app = await db.get_application_by_name(app_name)
    if not app:
        console.print(f"[red]❌ App '{app_name}' not found.[/red]")
        return

    if not run_id:
        last_run = await db.get_last_run(app.id)
        if not last_run:
            console.print(f"[yellow]⚠ No test runs found for '{app_name}'.[/yellow]")
            return
        run_id = last_run["id"]

    reports_dir = os.environ.get("REPORTS_DIR", "./reports")
    
    if excel:
        # Check if an excel report exists for this app
        excel_files = list(Path(reports_dir).glob(f"{app_name}_*.xlsx"))
        if not excel_files:
            # Generate the Excel report from database
            console.print("[yellow]⚠ Excel report not found. Generating from database...[/yellow]")
            results = await db.get_results_for_run(run_id)
            timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            excel_path = str(Path(reports_dir) / f"{app_name}_{timestamp}.xlsx")
            from reporting.excel_reporter import generate_excel_report
            await generate_excel_report(run_id, results, db, excel_path, app_name)
            
            # Save a copy to the root workspace
            root_excel_path = f"./{app_name}_test_report.xlsx"
            try:
                import shutil
                shutil.copy2(excel_path, root_excel_path)
            except Exception:
                pass
            
            target_report = excel_path
        else:
            target_report = str(max(excel_files, key=lambda f: f.stat().st_mtime))
    else:
        # HTML report
        report_files = list(Path(reports_dir).glob(f"{app_name}_*.html"))
        if not report_files:
            console.print(f"[yellow]⚠ No HTML report found. Run 'test' first.[/yellow]")
            return
        target_report = str(max(report_files, key=lambda f: f.stat().st_mtime))

    console.print(f"[bold]Opening:[/bold] {target_report}")

    # Open in default browser/viewer
    import subprocess
    if sys.platform == "win32":
        os.startfile(target_report)
    elif sys.platform == "darwin":
        subprocess.run(["open", target_report])
    else:
        subprocess.run(["xdg-open", target_report])


# ─── diff ────────────────────────────────────────────────────────────────────

@cli.command()
@click.option("--app", required=True, help="Application name")
@click.option("--v1", required=True, help="First version label")
@click.option("--v2", required=True, help="Second version label")
def diff(app: str, v1: str, v2: str):
    """Compare two application versions."""
    asyncio.run(_diff(app, v1, v2))


async def _diff(app_name: str, v1_label: str, v2_label: str):
    from storage.diff import diff_models

    db = _get_db()
    await db.initialize()

    app = await db.get_application_by_name(app_name)
    if not app:
        console.print(f"[red]❌ App '{app_name}' not found.[/red]")
        return

    versions = await db.list_versions(app.id)
    v1 = next((v for v in versions if v.label == v1_label), None)
    v2 = next((v for v in versions if v.label == v2_label), None)

    if not v1:
        console.print(f"[red]❌ Version '{v1_label}' not found.[/red]")
        return
    if not v2:
        console.print(f"[red]❌ Version '{v2_label}' not found.[/red]")
        return

    # Load both full app models
    # Temporarily set active to load each version
    await db.set_active_version(app.id, v1.id)
    app_v1 = await db.get_application(app.id)

    await db.set_active_version(app.id, v2.id)
    app_v2 = await db.get_application(app.id)

    result = diff_models(app_v1, app_v2)

    diff_table = Table(
        title=f"📊 Diff: {v1_label} → {v2_label}",
        border_style="bright_black"
    )
    diff_table.add_column("Change Type", style="dim")
    diff_table.add_column("Item")
    diff_table.add_column("Details", style="dim")

    for p in result["new_pages"]:
        diff_table.add_row("[green]+ New Page[/green]", p["url_pattern"], "")
    for p in result["removed_pages"]:
        diff_table.add_row("[red]- Removed Page[/red]", p["url_pattern"], "")
    for e in result["new_elements"]:
        diff_table.add_row("[green]+ New Element[/green]", e["label"], f"on {e['page']}")
    for e in result["removed_elements"]:
        diff_table.add_row("[red]- Removed Element[/red]", e["label"], "")
    for e in result["changed_elements"]:
        diff_table.add_row("[yellow]~ Changed[/yellow]", e["label"], "; ".join(e["changes"]))

    if diff_table.row_count == 0:
        diff_table.add_row("[dim]No changes detected[/dim]", "", "")

    console.print(diff_table)


# ─── status ──────────────────────────────────────────────────────────────────

@cli.command()
def status():
    """List all registered applications and their status."""
    asyncio.run(_status())


async def _status():
    db = _get_db()
    await db.initialize()

    apps = await db.list_applications()

    if not apps:
        console.print("[yellow]No applications registered yet. Run 'learn' to get started.[/yellow]")
        return

    table = Table(title="🤖 Registered Applications", border_style="bright_black")
    table.add_column("Name", style="bold")
    table.add_column("Base URL", style="dim")
    table.add_column("Active Version", style="cyan")
    table.add_column("Last Run")
    table.add_column("Pass Rate", style="green")

    for app_row in apps:
        app_id = app_row["id"]

        # Get active version
        version = await db.get_active_version(app_id)
        version_label = version.label if version else "—"

        # Get last run
        last_run = await db.get_last_run(app_id)
        if last_run:
            started = last_run.get("started_at", "")
            if started:
                try:
                    dt = datetime.fromisoformat(started)
                    last_run_str = dt.strftime("%Y-%m-%d %H:%M")
                except Exception:
                    last_run_str = started[:16]
            else:
                last_run_str = "—"

            total = last_run.get("total", 0)
            passed = last_run.get("passed", 0)
            if total > 0:
                pass_rate = f"{passed}/{total} ({passed/total*100:.0f}%)"
            else:
                pass_rate = "—"
        else:
            last_run_str = "Never"
            pass_rate = "—"

        table.add_row(
            app_row["name"],
            app_row.get("base_url", "")[:40],
            version_label,
            last_run_str,
            pass_rate
        )

    console.print(table)


if __name__ == "__main__":
    cli()
