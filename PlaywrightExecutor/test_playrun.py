from __future__ import annotations

import json
import sqlite3
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from urllib.request import urlopen
from unittest.mock import patch

from .controller import ProjectController
from .playwright_executor import ExecutionResult, PlaywrightExecutor
from .view import create_server


class FakeExecutor:
    def __init__(self) -> None:
        self.statuses = ["failed", "passed", "passed"]
        self.calls: list[str] = []
        self.settings_seen: list[dict[str, object] | None] = []
        self.delay = 0.0
        self.active = 0
        self.max_active = 0
        self.lock = threading.Lock()

    def run(
        self,
        test_case: dict[str, str],
        settings: dict[str, object] | None = None,
    ) -> ExecutionResult:
        with self.lock:
            self.calls.append(test_case["id"])
            self.settings_seen.append(settings)
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            status = self.statuses.pop(0)
        if self.delay:
            time.sleep(self.delay)
        with self.lock:
            self.active -= 1
        return ExecutionResult(status, 0 if status == "passed" else 1, 0.25, "", None)


class PlayRunnerControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        (self.root / "tests" / "ui").mkdir(parents=True)
        (self.root / "playwright.config.ts").write_text(
            "projects: [{ name: 'chromium', use: {} }]", encoding="utf-8"
        )
        (self.root / "tests" / "ui" / "login.spec.ts").write_text(
            """import { test } from '@playwright/test';
test.describe('Login suite', { tag: ['@smoke'] }, () => {
  test('opens login', async () => {});
  test.step('not a test case', async () => {});
});
test('second case', async () => {});
""",
            encoding="utf-8",
        )
        self.executor = FakeExecutor()
        self.controller = ProjectController(self.root, self.executor)  # type: ignore[arg-type]

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_discovers_test_cases_without_treating_steps_as_tests(self) -> None:
        catalog = self.controller.discover_tests()
        self.assertEqual(catalog["projects"], ["chromium"])
        self.assertEqual([case["title"] for case in catalog["tests"]], ["opens login", "second case"])
        self.assertEqual(catalog["tests"][0]["tags"], ["@smoke"])

    def test_batch_is_persisted_and_failed_tests_can_be_retried(self) -> None:
        test_cases = self.controller.discover_tests()["tests"]
        batch = self.controller.create_batch("Smoke checks", [case["id"] for case in test_cases])
        self.controller.rerun_batch(batch["id"])

        self._wait_for_status(batch["id"], {"failed"})
        self.controller.rerun_failed(batch["id"])
        self._wait_for_status(batch["id"], {"passed"})

        updated = self.controller.get_batch(batch["id"])
        self.assertEqual(len(updated["executions"]), 2)
        self.assertEqual(
            {execution["test_id"] for execution in updated["executions"]},
            {case["id"] for case in test_cases},
        )
        self.assertTrue(all(len(test["history"]) == 1 for test in updated["tests"]))
        self.assertEqual(self.executor.calls, [case["id"] for case in test_cases] + [test_cases[0]["id"]])
        self.assertTrue(self.controller.store_path.is_file())

    def test_bookmarks_are_persisted_in_shareable_json(self) -> None:
        test_case = self.controller.discover_tests()["tests"][0]
        self.assertFalse(test_case["bookmarked"])
        self.controller.set_bookmark(test_case["id"], True)
        self.assertTrue(self.controller.discover_tests()["tests"][0]["bookmarked"])

        reopened = ProjectController(self.root, self.executor)  # type: ignore[arg-type]
        self.assertTrue(reopened.discover_tests()["tests"][0]["bookmarked"])
        self.assertTrue(reopened.bookmark_store_path.is_file())
        document = json.loads(reopened.bookmark_store_path.read_text(encoding="utf-8"))
        self.assertEqual(document["version"], 1)
        self.assertEqual(document["bookmark_sets"][0]["name"], "Favorites")
        self.assertEqual(
            reopened.bookmark_store_path.relative_to(reopened.project_root).as_posix(),
            "bookmark/bookmark.json",
        )

    def test_named_bookmark_set_populates_a_batch_and_is_exportable(self) -> None:
        test_cases = self.controller.discover_tests()["tests"]
        bookmark_set = self.controller.create_bookmark_set(
            "Nightly smoke",
            [test_cases[0]["id"], test_cases[1]["id"], test_cases[0]["id"]],
        )
        self.assertEqual(bookmark_set["test_ids"], [test_cases[0]["id"], test_cases[1]["id"]])
        self.assertEqual(self.controller.list_bookmark_sets()[0]["name"], "Nightly smoke")

        exported = self.controller.export_bookmark_sets()
        self.assertEqual(exported["version"], 1)
        self.assertEqual(exported["bookmark_sets"][0]["tests"][0]["file"], test_cases[0]["file"])
        batch = self.controller.create_batch("Nightly batch", bookmark_set["test_ids"])
        self.assertEqual([case["id"] for case in batch["tests"]], bookmark_set["test_ids"])
        self.controller.delete_bookmark_set(bookmark_set["id"])
        self.assertEqual(self.controller.list_bookmark_sets(), [])

    def test_imported_bookmark_json_maps_cases_when_source_line_numbers_change(self) -> None:
        test_cases = self.controller.discover_tests()["tests"]
        self.controller.create_bookmark_set("Shared smoke", [test_cases[0]["id"]])
        document = self.controller.export_bookmark_sets()

        other_temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(other_temp_dir.cleanup)
        other_root = Path(other_temp_dir.name)
        (other_root / "tests" / "ui").mkdir(parents=True)
        (other_root / "playwright.config.ts").write_text(
            "projects: [{ name: 'chromium', use: {} }]", encoding="utf-8"
        )
        (other_root / "tests" / "ui" / "login.spec.ts").write_text(
            "\n" + (self.root / "tests" / "ui" / "login.spec.ts").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        other = ProjectController(other_root, FakeExecutor())  # type: ignore[arg-type]
        imported = other.import_bookmark_sets(document)
        imported_cases = other.list_bookmark_sets()[0]["test_ids"]

        self.assertEqual(imported[0]["name"], "Shared smoke")
        self.assertEqual(imported[0]["added"], 1)
        self.assertEqual(len(imported_cases), 1)
        self.assertNotEqual(imported_cases[0], test_cases[0]["id"])
        self.assertEqual(other.export_bookmark_sets()["bookmark_sets"][0]["tests"][0]["title"], "opens login")

    def test_import_rejects_unknown_test_references_without_partial_changes(self) -> None:
        document = {
            "version": 1,
            "bookmark_sets": [{
                "name": "Unresolvable",
                "tests": [{"file": "tests/missing.spec.ts", "title": "missing", "project": "chromium"}],
            }],
        }
        with self.assertRaisesRegex(ValueError, "not found"):
            self.controller.import_bookmark_sets(document)
        self.assertEqual(self.controller.list_bookmark_sets(), [])

    def test_shared_favorites_match_and_unbookmark_after_test_line_shifts(self) -> None:
        test_case = self.controller.discover_tests()["tests"][0]
        self.controller.set_bookmark(test_case["id"], True)
        document = self.controller.export_bookmark_sets()

        other_temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(other_temp_dir.cleanup)
        other_root = Path(other_temp_dir.name)
        (other_root / "tests" / "ui").mkdir(parents=True)
        (other_root / "playwright.config.ts").write_text(
            "projects: [{ name: 'chromium', use: {} }]", encoding="utf-8"
        )
        (other_root / "tests" / "ui" / "login.spec.ts").write_text(
            "\n" + (self.root / "tests" / "ui" / "login.spec.ts").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        reports = other_root / "reports"
        reports.mkdir()
        (other_root / "bookmark").mkdir()
        (other_root / "bookmark" / "bookmark.json").write_text(
            json.dumps(document), encoding="utf-8"
        )
        other = ProjectController(other_root, FakeExecutor())  # type: ignore[arg-type]
        shifted_case = other.discover_tests()["tests"][0]

        self.assertNotEqual(shifted_case["id"], test_case["id"])
        self.assertTrue(shifted_case["favorite"])
        other.set_bookmark(shifted_case["id"], False)
        self.assertFalse(other.discover_tests()["tests"][0]["favorite"])

    def test_legacy_starred_tests_migrate_into_the_favorites_set(self) -> None:
        test_case = self.controller.discover_tests()["tests"][0]
        with self.controller._database() as connection:
            connection.execute(
                "INSERT INTO bookmarks (test_id, created_at) VALUES (?, ?)",
                (test_case["id"], "2026-01-01T00:00:00+00:00"),
            )

        migrated = ProjectController(self.root, self.executor)  # type: ignore[arg-type]
        self.assertTrue(migrated.discover_tests()["tests"][0]["bookmarked"])
        favorites = migrated.list_bookmark_sets()
        self.assertEqual(len(favorites), 1)
        self.assertEqual(favorites[0]["name"], "Favorites")
        self.assertEqual(favorites[0]["test_ids"], [test_case["id"]])

    def test_quick_run_creates_and_starts_a_single_test_batch(self) -> None:
        self.executor.statuses = ["passed"]
        test_case = self.controller.discover_tests()["tests"][0]

        batch = self.controller.quick_run(test_case["id"])
        self._wait_for_status(batch["id"], {"passed"})

        completed = self.controller.get_batch(batch["id"])
        self.assertTrue(completed["name"].startswith("Quick run:"))
        self.assertEqual(completed["active_mode"], None)
        self.assertEqual(completed["executions"][0]["mode"], "quick")
        self.assertEqual([case["id"] for case in completed["tests"]], [test_case["id"]])

    def test_saved_settings_configure_retries_reporter_proxy_and_parallelism(self) -> None:
        test_cases = self.controller.discover_tests()["tests"]
        self.executor.statuses = ["passed", "passed"]
        self.executor.delay = 0.03
        settings = self.controller.update_settings({
            "proxy": "http://proxy.example:8080",
            "workers": 2,
            "retries": 3,
            "reporter": "html",
            "headless": True,
            "base_url": "https://shop.example.test/catalog/",
        })
        batch = self.controller.create_batch("Configured batch", [case["id"] for case in test_cases])
        self.controller.rerun_batch(batch["id"])
        self._wait_for_status(batch["id"], {"passed"})

        reopened = ProjectController(self.root, self.executor)  # type: ignore[arg-type]
        self.assertEqual(reopened.get_settings(), settings)
        self.assertEqual(
            reopened.settings_path.relative_to(reopened.project_root).as_posix(),
            "db/PlayRunner-settings.json",
        )
        self.assertEqual(self.executor.max_active, 2)
        self.assertTrue(all(run["settings"]["workers"] == 2 for run in reopened.get_batch(batch["id"])["executions"]))
        self.assertTrue(all(run["settings"]["reporter"] == "html" for run in reopened.get_batch(batch["id"])["executions"]))
        self.assertTrue(reopened.get_settings()["headless"])
        self.assertEqual(reopened.get_settings()["base_url"], "https://shop.example.test/catalog/")

    def test_settings_reject_invalid_proxy_and_out_of_range_values(self) -> None:
        for settings in (
            {"proxy": "file:///etc/passwd"},
            {"workers": 0},
            {"workers": 9},
            {"retries": -1},
            {"retries": 6},
            {"reporter": "unknown"},
            {"headless": "true"},
            {"base_url": "javascript:alert(1)"},
            {"base_url": "https:///missing-host"},
            {"base_url": "https://user:password@example.test"},
        ):
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                self.controller.update_settings(settings)

    def test_reopening_batch_keeps_only_latest_execution_result_per_test(self) -> None:
        self.executor.statuses = ["failed", "passed"]
        test_case = self.controller.discover_tests()["tests"][0]
        batch = self.controller.create_batch("Latest result only", [test_case["id"]])
        self.controller.rerun_batch(batch["id"])
        self._wait_for_status(batch["id"], {"failed"})
        self.controller.rerun_batch(batch["id"])
        self._wait_for_status(batch["id"], {"passed"})

        reopened = ProjectController(self.root, self.executor)  # type: ignore[arg-type]
        latest = reopened.get_batch(batch["id"])
        with sqlite3.connect(reopened.store_path) as database:
            persisted_count = database.execute(
                "SELECT count(*) FROM executions WHERE batch_id = ?",
                (batch["id"],),
            ).fetchone()[0]

        self.assertEqual(persisted_count, 1)
        self.assertEqual(len(latest["executions"]), 1)
        self.assertEqual(latest["executions"][0]["status"], "passed")
        self.assertEqual(len(latest["tests"][0]["history"]), 1)
        self.assertEqual(latest["tests"][0]["history"][0]["status"], "passed")

    def test_existing_json_batches_are_migrated_without_losing_history(self) -> None:
        test_case = self.controller.discover_tests()["tests"][0]
        execution = {
            "id": "execution-1",
            "test_id": test_case["id"],
            "test_title": test_case["title"],
            "file": test_case["file"],
            "project": test_case["project"],
            "mode": "batch",
            "status": "failed",
            "started_at": "2026-01-01T00:00:00+00:00",
            "finished_at": "2026-01-01T00:00:01+00:00",
            "duration_seconds": 1.0,
            "exit_code": 1,
            "report": "sample/results.html",
            "output": "failed",
        }
        legacy_batch = {
            "id": "legacy-batch",
            "name": "Existing batch",
            "created_at": "2026-01-01T00:00:00+00:00",
            "status": "failed",
            "active_mode": None,
            "tests": [{
                **test_case,
                "status": "failed",
                "last_report": execution["report"],
                "history": [execution],
            }],
            "executions": [execution],
        }
        self.controller.legacy_batch_store_path.write_text(json.dumps([legacy_batch]), encoding="utf-8")

        migrated = ProjectController(self.root, self.executor)  # type: ignore[arg-type]
        loaded = migrated.get_batch("legacy-batch")
        self.assertEqual(loaded["executions"][0]["id"], "execution-1")
        self.assertEqual(loaded["tests"][0]["history"][0]["report"], "sample/results.html")
        self.assertTrue(migrated.store_path.is_file())
        self.assertTrue(migrated.legacy_batch_store_path.is_file())

    def test_existing_reports_database_migrates_to_db_directory_with_batch_history(self) -> None:
        test_case = self.controller.discover_tests()["tests"][0]
        batch = self.controller.create_batch("Stored batch", [test_case["id"]])
        self.controller.legacy_database_path.parent.mkdir(parents=True, exist_ok=True)
        source = self.controller._connect()
        destination = sqlite3.connect(self.controller.legacy_database_path)
        try:
            source.backup(destination)
        finally:
            source.close()
            destination.close()
        self.controller.store_path.unlink()

        migrated = ProjectController(self.root, self.executor)  # type: ignore[arg-type]

        self.assertEqual(
            migrated.store_path.relative_to(migrated.project_root).as_posix(),
            "db/PlayRunner.sqlite3",
        )
        self.assertEqual(migrated.get_batch(batch["id"])["name"], "Stored batch")
        self.assertTrue(migrated.legacy_database_path.is_file())

    def test_dashboard_database_migrates_to_playrunner_filename(self) -> None:
        test_case = self.controller.discover_tests()["tests"][0]
        batch = self.controller.create_batch("Existing local database", [test_case["id"]])
        source = self.controller._connect()
        destination = sqlite3.connect(self.controller.legacy_local_database_path)
        try:
            source.backup(destination)
        finally:
            source.close()
            destination.close()
        self.controller.store_path.unlink()

        migrated = ProjectController(self.root, self.executor)  # type: ignore[arg-type]

        self.assertEqual(migrated.get_batch(batch["id"])["name"], "Existing local database")
        self.assertTrue(migrated.legacy_local_database_path.is_file())
        self.assertTrue(migrated.store_path.is_file())

    def test_dashboard_settings_migrate_to_playrunner_settings_file(self) -> None:
        legacy_settings = {"proxy": "", "workers": 5, "retries": 1, "reporter": "custom"}
        self.controller.settings_path.unlink()
        self.controller.legacy_settings_path.write_text(json.dumps(legacy_settings), encoding="utf-8")

        migrated = ProjectController(self.root, self.executor)  # type: ignore[arg-type]

        self.assertEqual(migrated.get_settings(), {
            **legacy_settings,
            "headless": False,
            "base_url": "http://rahulshettyacademy.com/",
        })
        self.assertTrue(migrated.settings_path.is_file())
        self.assertTrue(migrated.legacy_settings_path.is_file())

    def test_previous_playrun_database_and_settings_migrate_without_removing_sources(self) -> None:
        test_case = self.controller.discover_tests()["tests"][0]
        batch = self.controller.create_batch("Previous brand batch", [test_case["id"]])
        source_database = self.controller._connect()
        old_database = sqlite3.connect(self.controller.previous_local_database_path)
        try:
            source_database.backup(old_database)
        finally:
            source_database.close()
            old_database.close()
        self.controller.update_settings({
            "proxy": "",
            "workers": 3,
            "retries": 1,
            "reporter": "custom",
            "headless": True,
            "base_url": "https://previous.example.test/",
        })
        self.controller.previous_settings_path.write_text(
            self.controller.settings_path.read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        self.controller.store_path.unlink()
        self.controller.settings_path.unlink()

        migrated = ProjectController(self.root, self.executor)  # type: ignore[arg-type]

        self.assertEqual(migrated.get_batch(batch["id"])["name"], "Previous brand batch")
        self.assertEqual(migrated.get_settings()["workers"], 3)
        self.assertTrue(migrated.get_settings()["headless"])
        self.assertEqual(migrated.get_settings()["base_url"], "https://previous.example.test/")
        self.assertTrue(migrated.previous_local_database_path.is_file())
        self.assertTrue(migrated.previous_settings_path.is_file())
        self.assertTrue(migrated.store_path.is_file())
        self.assertTrue(migrated.settings_path.is_file())

    def test_legacy_settings_gain_headless_and_base_url_defaults(self) -> None:
        legacy_settings = {"proxy": "", "workers": 5, "retries": 0, "reporter": "custom"}
        self.controller.settings_path.write_text(json.dumps(legacy_settings), encoding="utf-8")

        reopened = ProjectController(self.root, self.executor)  # type: ignore[arg-type]

        self.assertEqual(reopened.get_settings()["headless"], False)
        self.assertEqual(reopened.get_settings()["base_url"], "http://rahulshettyacademy.com/")
        saved = json.loads(reopened.settings_path.read_text(encoding="utf-8"))
        self.assertIn("headless", saved)
        self.assertIn("base_url", saved)

    def test_recording_writes_named_test_to_custom_project_location(self) -> None:
        cli = self.root / "node_modules" / ".bin" / "playwright"
        cli.parent.mkdir(parents=True)
        cli.touch()
        executor = PlaywrightExecutor(self.root)
        controller = ProjectController(self.root, executor)
        with patch("PlaywrightExecutor.controller.subprocess.Popen") as popen:
            process = popen.return_value
            process.poll.return_value = None
            started = controller.start_recording("Customer can sign in", "tests/GUI/auth")

            command = popen.call_args.args[0]
            self.assertEqual(command[1:3], ["codegen", "--target=playwright-test"])
            self.assertEqual(command[-1], controller.get_settings()["base_url"])
            self.assertIn("--output=", command[3])
            self.assertEqual(started["path"], "tests/GUI/auth/Customer-can-sign-in.spec.ts")
            with self.assertRaisesRegex(ValueError, "already running"):
                controller.start_recording("Another test", "tests")

            temporary_path = Path(command[3].removeprefix("--output="))
            temporary_path.write_text(
                "import { test, expect } from '@playwright/test';\n"
                "test('test', async ({ page }) => {\n"
                "  await page.goto('https://example.test');\n"
                "});\n",
                encoding="utf-8",
            )
            process.poll.return_value = 0
            completed = controller.recording_status()

        self.assertTrue(completed["saved"])
        saved_path = self.root / completed["path"]
        self.assertTrue(saved_path.is_file())
        self.assertIn('test("Customer can sign in"', saved_path.read_text(encoding="utf-8"))
        discovered = controller.discover_tests()["tests"]
        self.assertIn("Customer can sign in", [test["title"] for test in discovered])

    def test_recording_rejects_project_escape_and_existing_target(self) -> None:
        cli = self.root / "node_modules" / ".bin" / "playwright"
        cli.parent.mkdir(parents=True)
        cli.touch()
        controller = ProjectController(self.root, PlaywrightExecutor(self.root))
        with self.assertRaisesRegex(ValueError, "inside the project"):
            controller.start_recording("Unsafe test", "../outside")
        with self.assertRaisesRegex(ValueError, "generated folder"):
            controller.start_recording("Generated test", "reports")

        existing = self.root / "tests" / "Already-here.spec.ts"
        existing.write_text("", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "already exists"):
            controller.start_recording("Already here", "tests")

    def test_playrunner_and_previous_api_routes_are_available(self) -> None:
        server = create_server(self.root, port=0)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        base_url = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            for path in ("/api/playRunner/tests", "/api/playrun/tests", "/api/tests"):
                with urlopen(f"{base_url}{path}", timeout=3) as response:
                    self.assertEqual(response.status, 200)
                    self.assertEqual(len(json.loads(response.read())["tests"]), 2)
            with urlopen(f"{base_url}/api/playRunner/settings", timeout=3) as response:
                settings = json.loads(response.read())["settings"]
                self.assertEqual(settings["headless"], False)
                self.assertEqual(settings["base_url"], "http://rahulshettyacademy.com/")
            with urlopen(f"{base_url}/api/playRunner/recording", timeout=3) as response:
                self.assertFalse(json.loads(response.read())["recording"]["running"])
            with urlopen(f"{base_url}/api/playrun/settings", timeout=3) as response:
                self.assertEqual(json.loads(response.read())["settings"]["headless"], False)
            with urlopen(f"{base_url}/", timeout=3) as response:
                page = response.read().decode("utf-8")
                self.assertIn("<title>PlayRunner", page)
                self.assertIn("<h1>PlayRunner</h1>", page)
                self.assertIn("mailto:sabareesh.kaveri@gmail.com", page)
                self.assertIn('data-tab="record"', page)
                self.assertIn('id="setting-base-url"', page)
                self.assertIn('id="setting-browser-mode"', page)
        finally:
            server.shutdown()
            worker.join(timeout=3)
            server.server_close()

    def test_executor_builds_a_file_scoped_custom_report_command(self) -> None:
        cli = self.root / "node_modules" / ".bin" / "playwright"
        cli.parent.mkdir(parents=True)
        cli.touch()
        test_case = {
            "file": "tests/ui/login.spec.ts",
            "title": "opens login [smoke]",
            "project": "chromium",
        }
        command = PlaywrightExecutor(self.root).command_for(test_case)
        self.assertEqual(command[0], str(cli.resolve()))
        self.assertIn(test_case["file"], command)
        self.assertEqual(command[command.index("--grep") + 1], r"opens\ login\ \[smoke\]")
        self.assertIn("--reporter=./fixtures/html-reporter.ts", command)
        self.assertIn("--retries=0", command)
        self.assertIn("--project=chromium", command)

    def test_executor_selects_playwright_html_report_and_retry_count(self) -> None:
        cli = self.root / "node_modules" / ".bin" / "playwright"
        cli.parent.mkdir(parents=True)
        cli.touch()
        command = PlaywrightExecutor(self.root).command_for(
            {"file": "tests/ui/login.spec.ts", "title": "opens login", "project": "chromium"},
            {"reporter": "html", "retries": 2},
        )
        self.assertIn("--reporter=html", command)
        self.assertIn("--retries=2", command)
        self.assertNotIn("--reporter=./fixtures/html-reporter.ts", command)

    def test_executor_applies_proxy_and_html_output_environment(self) -> None:
        cli = self.root / "node_modules" / ".bin" / "playwright"
        cli.parent.mkdir(parents=True)
        cli.touch()
        executor = PlaywrightExecutor(self.root)
        test_case = {
            "file": "tests/ui/login.spec.ts",
            "title": "opens login",
            "project": "chromium",
        }
        with patch(
            "PlaywrightExecutor.playwright_executor.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, "done", ""),
        ) as run:
            result = executor.run(test_case, {
                "proxy": "http://proxy.example:8080",
                "workers": 4,
                "retries": 2,
                "reporter": "html",
                "headless": True,
                "base_url": "https://shop.example.test/catalog/",
            })

        command = run.call_args.args[0]
        environment = run.call_args.kwargs["env"]
        self.assertEqual(result.status, "passed")
        self.assertEqual(environment["PLAYRUNNER_PROXY_SERVER"], "http://proxy.example:8080")
        self.assertEqual(environment["PLAYRUN_PROXY_SERVER"], "http://proxy.example:8080")
        self.assertNotIn("DASHBOARD_PROXY_SERVER", environment)
        self.assertEqual(environment["PLAYWRIGHT_HTML_OPEN"], "never")
        self.assertEqual(environment["PLAYRUNNER_HEADLESS"], "true")
        self.assertEqual(environment["PLAYRUNNER_BASE_URL"], "https://shop.example.test/catalog/")
        self.assertEqual(environment["PLAYRUN_HEADLESS"], "true")
        self.assertEqual(environment["PLAYRUN_BASE_URL"], "https://shop.example.test/catalog/")
        html_output = Path(environment["PLAYWRIGHT_HTML_OUTPUT_DIR"])
        self.assertTrue(
            html_output.is_relative_to(executor.project_root / "reports" / "PlayRunner")
        )
        self.assertIn("--reporter=html", command)
        self.assertIn("--workers=1", command)
    def test_executor_extracts_custom_report_path_and_result_facts(self) -> None:
        reports = self.root / "reports" / "sample"
        reports.mkdir(parents=True)
        report = reports / "results.html"
        report.write_text("<html></html>", encoding="utf-8")
        (reports / "results.json").write_text(
            '{"run":{"duration":1250},"tests":[{"outcome":"failed"}]}',
            encoding="utf-8",
        )
        executor = PlaywrightExecutor(self.root)
        report_line = f"Custom report generated: {report}\n"
        self.assertEqual(executor._report_path(report_line), "sample/results.html")
        self.assertEqual(executor._read_report_facts("sample/results.html"), ("failed", 1.25))
        self.assertIsNone(executor._report_path(f"Custom report generated: /tmp/results.html\n"))

    def _wait_for_status(self, batch_id: str, statuses: set[str]) -> None:
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if self.controller.get_batch(batch_id)["status"] in statuses:
                return
            time.sleep(0.01)
        self.fail(f"Batch did not reach any of {statuses}")


if __name__ == "__main__":
    unittest.main()
