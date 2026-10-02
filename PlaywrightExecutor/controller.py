from __future__ import annotations

import ast
from contextlib import contextmanager
import json
import os
import re
import signal
import sqlite3
import subprocess
import tempfile
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generator
from urllib.parse import urlsplit

from .constants import (
    BOOKMARK_DIRECTORY_NAME,
    BOOKMARK_FILENAME,
    BASE_URL_ENVIRONMENT_VARIABLE,
    DATABASE_DIRECTORY_NAME,
    DATABASE_FILENAME,
    DEFAULT_SETTINGS,
    LEGACY_PROXY_ENVIRONMENT_VARIABLE,
    HEADLESS_ENVIRONMENT_VARIABLE,
    DEFAULT_PROJECT_NAME,
    FAILED_STATUSES,
    LEGACY_BATCH_FILENAME,
    LEGACY_BOOKMARK_FILENAME,
    LEGACY_DATABASE_FILENAME,
    LEGACY_SETTINGS_FILENAME,
    MAX_RETRIES,
    MAX_WORKERS,
    MIN_RETRIES,
    MIN_WORKERS,
    PREVIOUS_DATABASE_FILENAME,
    PREVIOUS_SETTINGS_FILENAME,
    PREVIOUS_PROXY_ENVIRONMENT_VARIABLE,
    PROXY_ENVIRONMENT_VARIABLE,
    PROJECT_CONFIG_FILENAME,
    PRODUCT_NAME,
    PROJECT_PATTERN,
    REPORTERS,
    REPORTS_DIRECTORY_NAME,
    SETTINGS_FILENAME,
    SKIP_DIRECTORIES,
    TAG_PATTERN,
    TEST_CALL,
    TEST_DIR_PATTERN,
    TEST_FILE_EXTENSIONS,
    TESTS_DIRECTORY_NAME,
    TERMINAL_STATUSES,
)
from .playwright_executor import PlaywrightExecutor


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ProjectController:
    def __init__(self, project_root: Path, executor: PlaywrightExecutor | None = None) -> None:
        self.project_root = project_root.resolve()
        self.reports_directory = self.project_root / REPORTS_DIRECTORY_NAME
        self.database_directory = self.project_root / DATABASE_DIRECTORY_NAME
        self.store_path = self.database_directory / DATABASE_FILENAME
        self.settings_path = self.database_directory / SETTINGS_FILENAME
        self.previous_local_database_path = self.database_directory / PREVIOUS_DATABASE_FILENAME
        self.previous_settings_path = self.database_directory / PREVIOUS_SETTINGS_FILENAME
        self.legacy_local_database_path = self.database_directory / LEGACY_DATABASE_FILENAME
        self.legacy_settings_path = self.database_directory / LEGACY_SETTINGS_FILENAME
        self.legacy_database_path = self.reports_directory / LEGACY_DATABASE_FILENAME
        self.bookmark_directory = self.project_root / BOOKMARK_DIRECTORY_NAME
        self.bookmark_store_path = self.bookmark_directory / BOOKMARK_FILENAME
        self.legacy_bookmark_store_path = self.reports_directory / LEGACY_BOOKMARK_FILENAME
        self.legacy_batch_store_path = self.reports_directory / LEGACY_BATCH_FILENAME
        self.executor = executor or PlaywrightExecutor(self.project_root)
        self._lock = threading.RLock()
        self._batches: list[dict[str, Any]] = []
        self._threads: dict[str, threading.Thread] = {}
        self._settings: dict[str, Any] = {}
        self._recording: dict[str, Any] | None = None
        self._last_recording_status: dict[str, Any] = {
            "running": False,
            "saved": False,
            "path": None,
            "message": "Ready to record a Playwright test.",
        }
        self._migrate_database_location()
        self._initialize_database()
        self._load_settings()
        self._load_bookmark_sets()
        self._load_batches()

    def _migrate_database_location(self) -> None:
        self.database_directory.mkdir(parents=True, exist_ok=True)
        if self.store_path.exists():
            return
        source_path = next(
            (
                path for path in (
                    self.previous_local_database_path,
                    self.legacy_local_database_path,
                    self.legacy_database_path,
                )
                if path.is_file()
            ),
            None,
        )
        if source_path is None:
            return
        source = sqlite3.connect(source_path)
        destination = sqlite3.connect(self.store_path)
        try:
            source.backup(destination)
        except sqlite3.Error as error:
            raise RuntimeError(
                f"Could not migrate {PRODUCT_NAME} database from {source_path} "
                f"to {self.store_path}: {error}"
            ) from error
        finally:
            source.close()
            destination.close()

    def discover_tests(self) -> dict[str, Any]:
        config_path = self.project_root / PROJECT_CONFIG_FILENAME
        projects = [DEFAULT_PROJECT_NAME]
        test_directory = self.project_root / TESTS_DIRECTORY_NAME
        if config_path.is_file():
            config_source = config_path.read_text(encoding="utf-8")
            configured_projects = PROJECT_PATTERN.findall(config_source)
            if configured_projects:
                projects = list(dict.fromkeys(configured_projects))
            configured_test_dir = TEST_DIR_PATTERN.search(config_source)
            if configured_test_dir:
                test_directory = (self.project_root / configured_test_dir.group(1)).resolve()
        if not test_directory.is_relative_to(self.project_root) or not test_directory.is_dir():
            return {"projects": projects, "tests": []}

        test_files = [
            path for path in test_directory.rglob("*")
            if path.is_file()
            and path.suffix in TEST_FILE_EXTENSIONS
            and (".spec." in path.name or ".test." in path.name)
            and not any(part in SKIP_DIRECTORIES for part in path.parts)
        ]
        cases: list[dict[str, Any]] = []
        for test_file in sorted(test_files):
            source = test_file.read_text(encoding="utf-8")
            tags = list(dict.fromkeys(tag for _, tag in TAG_PATTERN.findall(source)))
            for match in TEST_CALL.finditer(source):
                quote, raw_title = match.group(1), match.group(2)
                title = ast.literal_eval(f"{quote}{raw_title}{quote}") if quote != "`" else re.sub(
                    r"\\([\\`])", r"\1", raw_title
                )
                if "${" in title or not title.strip():
                    continue
                line = source.count("\n", 0, match.start()) + 1
                relative_file = test_file.relative_to(self.project_root).as_posix()
                cases.append({
                    "id": f"{relative_file}:{line}:{title}",
                    "title": title,
                    "file": relative_file,
                    "line": line,
                    "tags": tags,
                    "project": projects[0],
                })
        with self._lock, self._database() as connection:
            latest_status: dict[str, tuple[str, str]] = {}
            for row in connection.execute(
                "SELECT test_id, status, finished_at FROM executions ORDER BY finished_at DESC"
            ):
                status = "failed" if row[1] in {"timedOut", "interrupted"} else row[1]
                latest_status.setdefault(row[0], (status, row[2]))
            connection.executemany(
                """
                INSERT INTO project_tests (id, title, file, line, project, tags_json, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                  title=excluded.title, file=excluded.file, line=excluded.line,
                  project=excluded.project, tags_json=excluded.tags_json,
                  updated_at=excluded.updated_at
                """,
                [
                    (
                        case["id"], case["title"], case["file"], case["line"],
                        case["project"], json.dumps(case["tags"]), timestamp(),
                    )
                    for case in cases
                ],
            )
            bookmarked, favorite_ids = self._bookmark_memberships(cases)
            for case in cases:
                case["bookmarked"] = case["id"] in bookmarked
                case["favorite"] = case["id"] in favorite_ids
                case["last_status"] = latest_status.get(case["id"], ("neverRun", ""))[0]
        return {"projects": projects, "tests": cases}

    def list_batches(self) -> list[dict[str, Any]]:
        with self._lock:
            return json.loads(json.dumps(self._batches))

    def create_batch(self, name: str, test_ids: list[str]) -> dict[str, Any]:
        clean_name = name.strip()
        if not clean_name:
            raise ValueError("Enter a name for the batch.")
        if not test_ids:
            raise ValueError("Select at least one test case.")

        catalog = {case["id"]: case for case in self.discover_tests()["tests"]}
        unknown = [test_id for test_id in test_ids if test_id not in catalog]
        if unknown:
            raise ValueError("One or more selected tests are no longer available. Refresh the project list.")
        selected = list(dict.fromkeys(test_ids))
        batch = {
            "id": uuid.uuid4().hex[:12],
            "name": clean_name,
            "created_at": timestamp(),
            "status": "pending",
            "active_mode": None,
            "tests": [
                {**catalog[test_id], "status": "pending", "last_report": None, "history": []}
                for test_id in selected
            ],
            "executions": [],
        }
        with self._lock:
            self._batches.insert(0, batch)
            self._save_batches()
            return json.loads(json.dumps(batch))

    def get_batch(self, batch_id: str) -> dict[str, Any]:
        with self._lock:
            return json.loads(json.dumps(self._find_batch(batch_id)))

    def get_settings(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._settings)

    def update_settings(self, settings: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(settings, dict):
            raise ValueError("Settings must be a JSON object.")
        updated = self._validate_settings({**self._settings, **settings})
        self.database_directory.mkdir(parents=True, exist_ok=True)
        temporary_path: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.database_directory,
                prefix=".PlayRunner-settings-",
                suffix=".tmp",
                delete=False,
            ) as temporary_file:
                temporary_path = temporary_file.name
                json.dump(updated, temporary_file, indent=2)
                temporary_file.write("\n")
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
            os.replace(temporary_path, self.settings_path)
        except OSError:
            if temporary_path is not None:
                try:
                    os.unlink(temporary_path)
                except FileNotFoundError:
                    pass
            raise
        with self._lock:
            self._settings = updated
            return dict(self._settings)

    def _load_settings(self) -> None:
        source_path = self.settings_path
        if not source_path.is_file() and self.previous_settings_path.is_file():
            source_path = self.previous_settings_path
        if not source_path.is_file() and self.legacy_settings_path.is_file():
            source_path = self.legacy_settings_path
        if not source_path.is_file():
            self._settings = dict(DEFAULT_SETTINGS)
            self.update_settings(self._settings)
            return
        try:
            settings = json.loads(source_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise RuntimeError(f"Could not read {PRODUCT_NAME} settings from {source_path}: {error}") from error
        if not isinstance(settings, dict):
            raise RuntimeError(f"{PRODUCT_NAME} settings at {source_path} must be a JSON object.")
        try:
            self._settings = self._validate_settings(settings)
        except ValueError as error:
            raise RuntimeError(f"Invalid {PRODUCT_NAME} settings at {source_path}: {error}") from error
        if source_path != self.settings_path or settings != self._settings:
            self.update_settings(self._settings)

    @staticmethod
    def _validate_settings(settings: dict[str, Any]) -> dict[str, Any]:
        proxy = settings.get("proxy", DEFAULT_SETTINGS["proxy"])
        workers = settings.get("workers", DEFAULT_SETTINGS["workers"])
        retries = settings.get("retries", DEFAULT_SETTINGS["retries"])
        reporter = settings.get("reporter", DEFAULT_SETTINGS["reporter"])
        headless = settings.get("headless", DEFAULT_SETTINGS["headless"])
        base_url = settings.get("base_url", DEFAULT_SETTINGS["base_url"])
        if not isinstance(proxy, str):
            raise ValueError("Proxy must be a URL string.")
        if (
            isinstance(workers, bool)
            or not isinstance(workers, int)
            or not MIN_WORKERS <= workers <= MAX_WORKERS
        ):
            raise ValueError("Parallel workers must be a whole number between 1 and 8.")
        if (
            isinstance(retries, bool)
            or not isinstance(retries, int)
            or not MIN_RETRIES <= retries <= MAX_RETRIES
        ):
            raise ValueError("Retry count must be a whole number between 0 and 5.")
        if reporter not in REPORTERS:
            raise ValueError("Reporter must be custom or html.")
        if not isinstance(headless, bool):
            raise ValueError("Browser mode must be headless or headed.")
        if not isinstance(base_url, str):
            raise ValueError("Base URL must be an HTTP or HTTPS URL.")
        return {
            "proxy": PlaywrightExecutor.validate_proxy(proxy),
            "workers": workers,
            "retries": retries,
            "reporter": reporter,
            "headless": headless,
            "base_url": ProjectController._validate_base_url(base_url),
        }

    @staticmethod
    def _validate_base_url(base_url: str) -> str:
        normalized = base_url.strip()
        try:
            parsed = urlsplit(normalized)
            valid_port = parsed.port is None or 1 <= parsed.port <= 65535
        except ValueError as error:
            raise ValueError("Base URL must be a valid HTTP or HTTPS URL.") from error
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or not valid_port
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
        ):
            raise ValueError("Base URL must be a valid HTTP or HTTPS URL, such as https://example.com/.")
        return normalized

    def start_recording(self, test_name: str, location: str = TESTS_DIRECTORY_NAME) -> dict[str, Any]:
        clean_name = test_name.strip()
        if not clean_name or len(clean_name) > 120 or any(ord(character) < 32 for character in clean_name):
            raise ValueError("Enter a test name between 1 and 120 printable characters.")
        filename_stem = re.sub(r"[^A-Za-z0-9_-]+", "-", clean_name).strip("-_")
        if not filename_stem:
            raise ValueError("Test name must include at least one letter or number.")

        relative_location = Path(location.strip() or TESTS_DIRECTORY_NAME)
        if relative_location.is_absolute() or ".." in relative_location.parts:
            raise ValueError("Test location must be a folder inside the project.")
        target_directory = (self.project_root / relative_location).resolve()
        if not target_directory.is_relative_to(self.project_root):
            raise ValueError("Test location must be a folder inside the project.")
        reserved_directories = set(SKIP_DIRECTORIES) | {
            DATABASE_DIRECTORY_NAME,
            BOOKMARK_DIRECTORY_NAME,
            ".venv",
        }
        if any(part in reserved_directories for part in relative_location.parts):
            raise ValueError("Choose a project folder used for Playwright tests, not an app data or generated folder.")
        target_path = target_directory / f"{filename_stem}.spec.ts"

        with self._lock:
            self._refresh_recording_locked()
            if self._recording is not None:
                raise ValueError("A recorder is already running. Stop it before starting another recording.")
            if target_path.exists():
                raise ValueError(f"Test file already exists: {target_path.relative_to(self.project_root).as_posix()}")
            if not self.executor.playwright_cli.is_file():
                raise FileNotFoundError(
                    f"Playwright CLI not found at {self.executor.playwright_cli}. "
                    "Install project dependencies before recording."
                )

            target_directory.mkdir(parents=True, exist_ok=True)
            temporary_path = target_directory / f".PlayRunner-recording-{uuid.uuid4().hex}.spec.ts"
            environment = os.environ.copy()
            environment["PLAYWRIGHT_HTML_OPEN"] = "never"
            environment.pop(LEGACY_PROXY_ENVIRONMENT_VARIABLE, None)
            if self._settings["proxy"]:
                environment[PROXY_ENVIRONMENT_VARIABLE] = self._settings["proxy"]
                environment[PREVIOUS_PROXY_ENVIRONMENT_VARIABLE] = self._settings["proxy"]
            else:
                environment.pop(PROXY_ENVIRONMENT_VARIABLE, None)
                environment.pop(PREVIOUS_PROXY_ENVIRONMENT_VARIABLE, None)
            environment[HEADLESS_ENVIRONMENT_VARIABLE] = str(self._settings["headless"]).lower()
            environment[BASE_URL_ENVIRONMENT_VARIABLE] = self._settings["base_url"]
            command = [
                str(self.executor.playwright_cli),
                "codegen",
                "--target=playwright-test",
                f"--output={temporary_path}",
                self._settings["base_url"],
            ]
            try:
                process = subprocess.Popen(
                    command,
                    cwd=self.project_root,
                    env=environment,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except OSError as error:
                raise RuntimeError(f"Could not start the Playwright recorder: {error}") from error

            self._recording = {
                "process": process,
                "temporary_path": temporary_path,
                "target_path": target_path,
                "test_name": clean_name,
                "started_at": timestamp(),
                "stop_requested": False,
            }
            self._last_recording_status = {
                "running": True,
                "saved": False,
                "path": target_path.relative_to(self.project_root).as_posix(),
                "message": "Recorder started. Interact with the opened browser, then stop and save.",
            }
            return dict(self._last_recording_status)

    def recording_status(self) -> dict[str, Any]:
        with self._lock:
            self._refresh_recording_locked()
            return dict(self._last_recording_status)

    def stop_recording(self) -> dict[str, Any]:
        with self._lock:
            self._refresh_recording_locked()
            recording = self._recording
            if recording is None:
                return dict(self._last_recording_status)
            recording["stop_requested"] = True
            process = recording["process"]

        if process.poll() is None:
            try:
                process.send_signal(signal.SIGINT)
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            except ProcessLookupError:
                pass

        with self._lock:
            self._refresh_recording_locked()
            return dict(self._last_recording_status)

    def _refresh_recording_locked(self) -> None:
        recording = self._recording
        if recording is None:
            return
        process = recording["process"]
        return_code = process.poll()
        if return_code is None:
            self._last_recording_status = {
                "running": True,
                "saved": False,
                "path": recording["target_path"].relative_to(self.project_root).as_posix(),
                "message": "Recorder is running. Interact with the opened browser, then stop and save.",
            }
            return

        temporary_path: Path = recording["temporary_path"]
        target_path: Path = recording["target_path"]
        try:
            if return_code != 0 and not recording["stop_requested"]:
                raise RuntimeError(f"Playwright recorder exited with code {return_code}.")
            if target_path.exists():
                raise FileExistsError(
                    f"Test file already exists: {target_path.relative_to(self.project_root).as_posix()}"
                )
            source = temporary_path.read_text(encoding="utf-8")
            test_call = re.search(r"\btest\(\s*", source)
            if test_call is None:
                raise RuntimeError("Recorder did not produce a Playwright test. Record at least one interaction and try again.")
            title_start = test_call.end()
            title_match = re.match(r"""(?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*')""", source[title_start:])
            if title_match is None:
                raise RuntimeError("Recorded test code has an unexpected format and could not be saved.")
            source = (
                source[:title_start]
                + json.dumps(recording["test_name"], ensure_ascii=False)
                + source[title_start + title_match.end():]
            )
            temporary_path.write_text(source, encoding="utf-8")
            os.replace(temporary_path, target_path)
            self._last_recording_status = {
                "running": False,
                "saved": True,
                "path": target_path.relative_to(self.project_root).as_posix(),
                "message": "Recorded test saved. Refresh the project test list to see it.",
            }
        except (OSError, RuntimeError) as error:
            self._last_recording_status = {
                "running": False,
                "saved": False,
                "path": None,
                "message": str(error),
            }
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
        finally:
            self._recording = None

    def set_bookmark(self, test_id: str, bookmarked: bool) -> bool:
        catalog = {case["id"]: case for case in self.discover_tests()["tests"]}
        if test_id not in catalog:
            raise ValueError("Test case is no longer available. Refresh the project list.")
        with self._lock:
            updated = json.loads(json.dumps(self._bookmark_sets))
            favorites = next(
                (item for item in updated if item["name"].casefold() == "favorites"),
                None,
            )
            if favorites is None and bookmarked:
                favorites = {
                    "id": uuid.uuid4().hex[:12],
                    "name": "Favorites",
                    "created_at": timestamp(),
                    "tests": [],
                }
                updated.append(favorites)
            if favorites is not None:
                favorites["tests"] = [
                    reference for reference in favorites["tests"]
                    if reference.get("id") != test_id
                    and not self._reference_matches(reference, catalog[test_id])
                ]
                if bookmarked:
                    favorites["tests"].append(self._portable_case(catalog[test_id]))
            self._write_bookmark_sets(updated)
            self._bookmark_sets = updated
        return bookmarked

    def list_bookmark_sets(self) -> list[dict[str, Any]]:
        cases = self.discover_tests()["tests"]
        with self._lock:
            catalog = {case["id"]: case for case in cases}
            bookmark_sets = []
            for bookmark_set in self._bookmark_sets:
                test_ids: list[str] = []
                unavailable = 0
                for reference in bookmark_set["tests"]:
                    test_id = reference.get("id")
                    if test_id not in catalog or not self._reference_matches(reference, catalog[test_id]):
                        matches = [
                            case["id"] for case in cases
                            if self._reference_matches(reference, case)
                        ]
                        test_id = matches[0] if len(matches) == 1 else None
                    if test_id is None:
                        unavailable += 1
                    else:
                        test_ids.append(test_id)
                bookmark_sets.append({
                    "id": bookmark_set["id"],
                    "name": bookmark_set["name"],
                    "created_at": bookmark_set["created_at"],
                    "test_ids": list(dict.fromkeys(test_ids)),
                    "unavailable": unavailable,
                })
            return bookmark_sets

    def create_bookmark_set(self, name: str, test_ids: list[str]) -> dict[str, Any]:
        clean_name = name.strip()
        if not clean_name:
            raise ValueError("Enter a name for the bookmark set.")
        if len(clean_name) > 100:
            raise ValueError("Bookmark set names must be 100 characters or fewer.")
        if not test_ids:
            raise ValueError("Select at least one test case to bookmark.")
        catalog = {case["id"]: case for case in self.discover_tests()["tests"]}
        unique_ids = list(dict.fromkeys(test_ids))
        if any(test_id not in catalog for test_id in unique_ids):
            raise ValueError("One or more selected tests are no longer available. Refresh the project list.")

        with self._lock:
            if any(item["name"].casefold() == clean_name.casefold() for item in self._bookmark_sets):
                raise ValueError(f'A bookmark set named "{clean_name}" already exists.')
            bookmark_set = {
                "id": uuid.uuid4().hex[:12],
                "name": clean_name,
                "created_at": timestamp(),
                "tests": [self._portable_case(catalog[test_id]) for test_id in unique_ids],
            }
            updated = [*self._bookmark_sets, bookmark_set]
            self._write_bookmark_sets(updated)
            self._bookmark_sets = updated
        return {
            "id": bookmark_set["id"],
            "name": bookmark_set["name"],
            "created_at": bookmark_set["created_at"],
            "test_ids": unique_ids,
        }

    def delete_bookmark_set(self, bookmark_set_id: str) -> None:
        with self._lock:
            kept = [item for item in self._bookmark_sets if item["id"] != bookmark_set_id]
            if len(kept) == len(self._bookmark_sets):
                raise KeyError(f"Bookmark set {bookmark_set_id!r} does not exist.")
            self._write_bookmark_sets(kept)
            self._bookmark_sets = kept

    def export_bookmark_sets(self) -> dict[str, Any]:
        with self._lock:
            return json.loads(json.dumps({
                "version": 1,
                "bookmark_sets": self._bookmark_sets,
            }))

    def import_bookmark_sets(self, document: dict[str, Any]) -> list[dict[str, Any]]:
        if document.get("version") != 1 or not isinstance(document.get("bookmark_sets"), list):
            raise ValueError("Bookmark JSON must contain version 1 and a bookmark_sets list.")
        catalog = {case["id"]: case for case in self.discover_tests()["tests"]}
        by_reference: dict[tuple[str, str, str], list[str]] = {}
        for case in catalog.values():
            key = (case["file"], case["title"], case["project"])
            by_reference.setdefault(key, []).append(case["id"])

        prepared: list[tuple[str, list[str]]] = []
        for item in document["bookmark_sets"]:
            if not isinstance(item, dict) or not isinstance(item.get("name"), str):
                raise ValueError("Each imported bookmark set must have a name.")
            name = item["name"].strip()
            if not name or len(name) > 100:
                raise ValueError("Imported bookmark set names must be 1 to 100 characters.")
            references = item.get("tests")
            if not isinstance(references, list):
                raise ValueError(f'Bookmark set "{name}" must contain a tests list.')
            test_ids: list[str] = []
            for reference in references:
                if not isinstance(reference, dict) or not all(
                    isinstance(reference.get(key), str)
                    for key in ("file", "title", "project")
                ):
                    raise ValueError(f'Bookmark set "{name}" contains an invalid test reference.')
                key = (reference["file"], reference["title"], reference["project"])
                matches = by_reference.get(key, [])
                if len(matches) != 1:
                    reason = "ambiguous" if matches else "not found"
                    raise ValueError(
                        f'Cannot import "{name}": test "{reference["title"]}" in '
                        f'{reference["file"]} is {reason} in this project.'
                    )
                test_ids.append(matches[0])
            prepared.append((name, list(dict.fromkeys(test_ids))))

        imported: list[dict[str, Any]] = []
        with self._lock:
            updated = json.loads(json.dumps(self._bookmark_sets))
            for name, test_ids in prepared:
                bookmark_set = next(
                    (entry for entry in updated if entry["name"].casefold() == name.casefold()),
                    None,
                )
                if bookmark_set is None:
                    bookmark_set = {
                        "id": uuid.uuid4().hex[:12],
                        "name": name,
                        "created_at": timestamp(),
                        "tests": [],
                    }
                    updated.append(bookmark_set)
                existing = {reference.get("id") for reference in bookmark_set["tests"]}
                added = 0
                for test_id in test_ids:
                    if test_id not in existing:
                        bookmark_set["tests"].append(self._portable_case(catalog[test_id]))
                        existing.add(test_id)
                        added += 1
                imported.append({
                    "id": bookmark_set["id"],
                    "name": bookmark_set["name"],
                    "added": added,
                })
            self._write_bookmark_sets(updated)
            self._bookmark_sets = updated
        return imported

    def quick_run(self, test_id: str) -> dict[str, Any]:
        catalog = {case["id"]: case for case in self.discover_tests()["tests"]}
        if test_id not in catalog:
            raise ValueError("Test case is no longer available. Refresh the project list.")
        case = catalog[test_id]
        batch = self.create_batch(f"Quick run: {case['title']}", [test_id])
        self._start(batch["id"], "quick", [test_id])
        return batch

    def start_test(self, batch_id: str, test_id: str) -> None:
        self._start(batch_id, "selected", [test_id])

    def rerun_batch(self, batch_id: str) -> None:
        with self._lock:
            batch = self._find_batch(batch_id)
            test_ids = [test["id"] for test in batch["tests"]]
        self._start(batch_id, "batch", test_ids)

    def rerun_failed(self, batch_id: str) -> None:
        with self._lock:
            batch = self._find_batch(batch_id)
            test_ids = [
                test["id"] for test in batch["tests"]
                if test["history"] and test["history"][-1]["status"] in {"failed", "timedOut", "interrupted"}
            ]
        if not test_ids:
            raise ValueError("There are no failed test cases to re-run.")
        self._start(batch_id, "failed", test_ids)

    def _start(self, batch_id: str, mode: str, test_ids: list[str]) -> None:
        with self._lock:
            batch = self._find_batch(batch_id)
            if batch["status"] == "running":
                raise ValueError("This batch is already running.")
            test_map = {test["id"]: test for test in batch["tests"]}
            if not test_ids or any(test_id not in test_map for test_id in test_ids):
                raise ValueError("The selected test case is not part of this batch.")
            batch["status"] = "running"
            batch["active_mode"] = mode
            for test_id in test_ids:
                test_map[test_id]["status"] = "queued"
            self._save_batches()
            settings = dict(self._settings)
            worker = threading.Thread(
                target=self._run_jobs,
                args=(batch_id, mode, list(test_ids), settings),
                name=f"playwright-batch-{batch_id}",
                daemon=True,
            )
            self._threads[batch_id] = worker
            worker.start()

    def _run_jobs(
        self,
        batch_id: str,
        mode: str,
        test_ids: list[str],
        settings: dict[str, Any],
    ) -> None:
        with self._lock:
            batch = self._find_batch(batch_id)
            case_map = {test["id"]: test for test in batch["tests"]}
        with ThreadPoolExecutor(max_workers=settings["workers"]) as executor:
            futures = {
                executor.submit(self._run_one, batch_id, case_map[test_id], mode, settings): test_id
                for test_id in test_ids
            }
            for future in as_completed(futures):
                future.result()

        with self._lock:
            batch = self._find_batch(batch_id)
            statuses = [test["status"] for test in batch["tests"]]
            if all(status in TERMINAL_STATUSES for status in statuses):
                batch["status"] = (
                    "failed" if any(status in FAILED_STATUSES for status in statuses)
                    else "flaky" if "flaky" in statuses
                    else "passed"
                )
            else:
                batch["status"] = "pending"
            batch["active_mode"] = None
            self._threads.pop(batch_id, None)
            self._save_batches()

    def _run_one(
        self,
        batch_id: str,
        case: dict[str, Any],
        mode: str,
        settings: dict[str, Any],
    ) -> None:
        test_id = case["id"]
        started = timestamp()
        with self._lock:
            batch = self._find_batch(batch_id)
            next(test for test in batch["tests"] if test["id"] == test_id)["status"] = "running"
            self._save_batches()

        try:
            result = self.executor.run(case, settings)
            status = result.status if result.status in TERMINAL_STATUSES else "failed"
            output = result.output
            report = result.report
            exit_code = result.exit_code
            duration = result.duration_seconds
        except Exception as error:
            status = "failed"
            output = f"{type(error).__name__}: {error}"
            report = None
            exit_code = -1
            duration = 0.0

        record = {
            "id": uuid.uuid4().hex[:12],
            "test_id": test_id,
            "test_title": case["title"],
            "file": case["file"],
            "project": case["project"],
            "mode": mode,
            "status": status,
            "started_at": started,
            "finished_at": timestamp(),
            "duration_seconds": duration,
            "exit_code": exit_code,
            "report": report,
            "output": output[-12000:],
            "settings": {
                "workers": settings["workers"],
                "retries": settings["retries"],
                "reporter": settings["reporter"],
                "proxy_enabled": bool(settings["proxy"]),
            },
        }
        with self._lock:
            batch = self._find_batch(batch_id)
            test = next(test for test in batch["tests"] if test["id"] == test_id)
            test["status"] = status
            test["last_report"] = report
            test["history"] = [record]
            batch["executions"] = [
                execution for execution in batch["executions"]
                if execution["test_id"] != test_id
            ]
            batch["executions"].insert(0, record)
            self._save_batches()

    def _find_batch(self, batch_id: str) -> dict[str, Any]:
        batch = next((item for item in self._batches if item["id"] == batch_id), None)
        if batch is None:
            raise KeyError(f"Batch {batch_id!r} does not exist.")
        return batch

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.store_path, timeout=10)
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @staticmethod
    def _reference_matches(reference: dict[str, Any], test_case: dict[str, Any]) -> bool:
        return (
            reference.get("file") == test_case["file"]
            and reference.get("title") == test_case["title"]
            and reference.get("project") == test_case["project"]
        )

    def _bookmark_memberships(
        self,
        cases: list[dict[str, Any]],
    ) -> tuple[set[str], set[str]]:
        catalog = {case["id"]: case for case in cases}
        bookmarked: set[str] = set()
        favorites: set[str] = set()
        for bookmark_set in self._bookmark_sets:
            for reference in bookmark_set["tests"]:
                test_id = reference.get("id")
                if test_id not in catalog or not self._reference_matches(reference, catalog[test_id]):
                    matches = [
                        case["id"] for case in cases
                        if self._reference_matches(reference, case)
                    ]
                    test_id = matches[0] if len(matches) == 1 else None
                if test_id is not None:
                    bookmarked.add(test_id)
                    if bookmark_set["name"].casefold() == "favorites":
                        favorites.add(test_id)
        return bookmarked, favorites

    @staticmethod
    def _portable_case(test_case: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": test_case["id"],
            "file": test_case["file"],
            "title": test_case["title"],
            "project": test_case["project"],
            "tags": list(test_case.get("tags", [])),
        }

    def _write_bookmark_sets(
        self,
        bookmark_sets: list[dict[str, Any]] | None = None,
    ) -> None:
        self.bookmark_directory.mkdir(parents=True, exist_ok=True)
        temporary_path: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.reports_directory,
                prefix=".PlayRunner-bookmarks-",
                suffix=".tmp",
                delete=False,
            ) as temporary_file:
                temporary_path = temporary_file.name
                json.dump(
                    {
                        "version": 1,
                        "bookmark_sets": self._bookmark_sets if bookmark_sets is None else bookmark_sets,
                    },
                    temporary_file,
                    ensure_ascii=False,
                    indent=2,
                )
                temporary_file.write("\n")
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
            os.replace(temporary_path, self.bookmark_store_path)
        except OSError:
            if temporary_path is not None:
                try:
                    os.unlink(temporary_path)
                except FileNotFoundError:
                    pass
            raise

    def _load_bookmark_sets(self) -> None:
        self._bookmark_sets = self._read_bookmark_sets(self.bookmark_store_path)
        legacy_sets = self._read_bookmark_sets(self.legacy_bookmark_store_path)
        legacy_sets.extend(self._read_legacy_bookmark_sets())
        self._merge_legacy_bookmark_sets(legacy_sets)
        self._write_bookmark_sets()
        self._clear_legacy_bookmarks()

    @staticmethod
    def _read_bookmark_sets(path: Path) -> list[dict[str, Any]]:
        if not path.is_file() or path.stat().st_size == 0:
            return []
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise RuntimeError(f"Could not read bookmarks from {path}: {error}") from error
        if (
            not isinstance(document, dict)
            or document.get("version") != 1
            or not isinstance(document.get("bookmark_sets"), list)
        ):
            raise RuntimeError(f"Bookmark data at {path} must use JSON version 1.")

        names: set[str] = set()
        for bookmark_set in document["bookmark_sets"]:
            if (
                not isinstance(bookmark_set, dict)
                or not isinstance(bookmark_set.get("id"), str)
                or not isinstance(bookmark_set.get("name"), str)
                or not bookmark_set["name"].strip()
                or len(bookmark_set["name"]) > 100
                or not isinstance(bookmark_set.get("created_at"), str)
                or not isinstance(bookmark_set.get("tests"), list)
                or any(
                    not isinstance(case, dict)
                    or not all(isinstance(case.get(key), str) for key in ("file", "title", "project"))
                    or ("id" in case and not isinstance(case["id"], str))
                    or not isinstance(case.get("tags", []), list)
                    or not all(isinstance(tag, str) for tag in case.get("tags", []))
                    for case in bookmark_set["tests"]
                )
            ):
                raise RuntimeError(f"Bookmark data at {path} contains an invalid set.")
            normalized_name = bookmark_set["name"].casefold()
            if normalized_name in names:
                raise RuntimeError(f"Bookmark data at {path} contains duplicate set names.")
            names.add(normalized_name)
        return document["bookmark_sets"]

    def _read_legacy_bookmark_sets(self) -> list[dict[str, Any]]:
        migrated: dict[str, dict[str, Any]] = {}
        with self._database() as connection:
            rows = connection.execute(
                """
                SELECT bookmark_sets.id, bookmark_sets.name, bookmark_sets.created_at,
                       project_tests.id, project_tests.file, project_tests.title,
                       project_tests.project, project_tests.tags_json
                FROM bookmark_sets
                JOIN bookmark_items ON bookmark_items.bookmark_set_id = bookmark_sets.id
                JOIN project_tests ON project_tests.id = bookmark_items.test_id
                ORDER BY bookmark_sets.name COLLATE NOCASE, bookmark_items.rowid
                """
            )
            for set_id, name, created_at, test_id, file, title, project, tags_json in rows:
                bookmark_set = migrated.setdefault(
                    set_id,
                    {"id": set_id, "name": name, "created_at": created_at, "tests": []},
                )
                bookmark_set["tests"].append({
                    "id": test_id,
                    "file": file,
                    "title": title,
                    "project": project,
                    "tags": json.loads(tags_json),
                })
        return list(migrated.values())

    def _merge_legacy_bookmark_sets(self, legacy_sets: list[dict[str, Any]]) -> None:
        if not legacy_sets:
            return
        updated = json.loads(json.dumps(self._bookmark_sets))
        for legacy_set in legacy_sets:
            existing = next(
                (item for item in updated if item["name"].casefold() == legacy_set["name"].casefold()),
                None,
            )
            if existing is None:
                updated.append(legacy_set)
                continue
            existing_references = {
                (reference.get("file"), reference.get("title"), reference.get("project"))
                for reference in existing["tests"]
            }
            for reference in legacy_set["tests"]:
                key = (reference["file"], reference["title"], reference["project"])
                if key not in existing_references:
                    existing["tests"].append(reference)
                    existing_references.add(key)
        self._write_bookmark_sets(updated)
        self._bookmark_sets = updated
        self._clear_legacy_bookmarks()

    def _clear_legacy_bookmarks(self) -> None:
        with self._database() as connection:
            connection.execute("DELETE FROM bookmark_items")
            connection.execute("DELETE FROM bookmark_sets")
            connection.execute("DELETE FROM bookmarks")

    @contextmanager
    def _database(self) -> Generator[sqlite3.Connection, None, None]:
        connection = self._connect()
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize_database(self) -> None:
        self.reports_directory.mkdir(parents=True, exist_ok=True)
        with self._database() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS project_tests (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    file TEXT NOT NULL,
                    line INTEGER NOT NULL,
                    project TEXT NOT NULL,
                    tags_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS bookmarks (
                    test_id TEXT PRIMARY KEY REFERENCES project_tests(id) ON DELETE CASCADE,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS bookmark_sets (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL COLLATE NOCASE UNIQUE,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS bookmark_items (
                    bookmark_set_id TEXT NOT NULL REFERENCES bookmark_sets(id) ON DELETE CASCADE,
                    test_id TEXT NOT NULL REFERENCES project_tests(id) ON DELETE CASCADE,
                    PRIMARY KEY (bookmark_set_id, test_id)
                );
                CREATE TABLE IF NOT EXISTS batches (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    active_mode TEXT
                );
                CREATE TABLE IF NOT EXISTS batch_tests (
                    batch_id TEXT NOT NULL REFERENCES batches(id) ON DELETE CASCADE,
                    test_id TEXT NOT NULL,
                    position INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    last_report TEXT,
                    case_json TEXT NOT NULL,
                    PRIMARY KEY (batch_id, test_id)
                );
                CREATE TABLE IF NOT EXISTS executions (
                    id TEXT PRIMARY KEY,
                    batch_id TEXT NOT NULL REFERENCES batches(id) ON DELETE CASCADE,
                    test_id TEXT NOT NULL,
                    position INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    finished_at TEXT NOT NULL,
                    report TEXT,
                    record_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS executions_test_recent
                  ON executions (test_id, finished_at DESC);
                """
            )
            legacy_bookmark = connection.execute(
                "SELECT 1 FROM bookmarks LIMIT 1"
            ).fetchone()
            if legacy_bookmark:
                favorites = connection.execute(
                    "SELECT id FROM bookmark_sets WHERE name = 'Favorites'"
                ).fetchone()
                if favorites is None:
                    favorites_id = uuid.uuid4().hex[:12]
                    connection.execute(
                        "INSERT INTO bookmark_sets (id, name, created_at) VALUES (?, 'Favorites', ?)",
                        (favorites_id, timestamp()),
                    )
                else:
                    favorites_id = favorites[0]
                connection.execute(
                    """
                    INSERT OR IGNORE INTO bookmark_items (bookmark_set_id, test_id)
                    SELECT ?, test_id FROM bookmarks
                    """,
                    (favorites_id,),
                )
                connection.execute("DELETE FROM bookmarks")

    def _load_batches(self) -> None:
        changed = False
        with self._database() as connection:
            rows = connection.execute(
                "SELECT id, name, created_at, status, active_mode FROM batches ORDER BY rowid DESC"
            ).fetchall()
            self._batches = []
            for batch_id, name, created_at, status, active_mode in rows:
                tests = []
                for case_json, test_status, last_report in connection.execute(
                    """
                    SELECT case_json, status, last_report FROM batch_tests
                    WHERE batch_id = ? ORDER BY position
                    """,
                    (batch_id,),
                ):
                    test = json.loads(case_json)
                    test.update(status=test_status, last_report=last_report, history=[])
                    tests.append(test)
                executions = [
                    json.loads(record_json)
                    for (record_json,) in connection.execute(
                        "SELECT record_json FROM executions WHERE batch_id = ? ORDER BY position DESC",
                        (batch_id,),
                    )
                ]
                history_by_test: dict[str, list[dict[str, Any]]] = {}
                for execution in executions:
                    history_by_test.setdefault(execution["test_id"], []).insert(0, execution)
                for test in tests:
                    test["history"] = history_by_test.get(test["id"], [])
                batch = {
                    "id": batch_id,
                    "name": name,
                    "created_at": created_at,
                    "status": status,
                    "active_mode": active_mode,
                    "tests": tests,
                    "executions": executions,
                }
                if self._keep_latest_execution_per_test(batch):
                    changed = True
                self._batches.append(batch)

        if not self._batches and self.legacy_batch_store_path.is_file():
            try:
                stored = json.loads(self.legacy_batch_store_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as error:
                raise RuntimeError(
                    f"Could not migrate saved batches from {self.legacy_batch_store_path}: {error}"
                ) from error
            if not isinstance(stored, list):
                raise RuntimeError(f"Legacy batch data at {self.legacy_batch_store_path} must be a JSON list.")
            self._batches = stored
            changed = True

        for batch in self._batches:
            if self._keep_latest_execution_per_test(batch):
                changed = True
            if batch.get("status") == "running":
                batch["status"] = "interrupted"
                batch["active_mode"] = None
                for test in batch.get("tests", []):
                    if test.get("status") in {"queued", "running"}:
                        test["status"] = "interrupted"
                changed = True
        if changed:
            self._save_batches()
        elif self._batches:
            self._save_batches()

    @staticmethod
    def _keep_latest_execution_per_test(batch: dict[str, Any]) -> bool:
        latest_by_test: dict[str, dict[str, Any]] = {}
        for execution in batch.get("executions", []):
            latest_by_test.setdefault(execution["test_id"], execution)
        latest_executions = sorted(
            latest_by_test.values(),
            key=lambda execution: (
                execution.get("finished_at", ""),
                execution.get("id", ""),
            ),
            reverse=True,
        )
        changed = len(latest_executions) != len(batch.get("executions", []))
        batch["executions"] = latest_executions
        history_by_test = {
            execution["test_id"]: [execution]
            for execution in latest_executions
        }
        for test in batch.get("tests", []):
            latest_history = history_by_test.get(test["id"], [])
            if test.get("history", []) != latest_history:
                changed = True
            test["history"] = latest_history
            if latest_history:
                latest = latest_history[0]
                if test.get("status") != latest["status"] or test.get("last_report") != latest.get("report"):
                    changed = True
                test["status"] = latest["status"]
                test["last_report"] = latest.get("report")
        return changed

    def _save_batches(self) -> None:
        with self._database() as connection:
            connection.execute("DELETE FROM executions")
            connection.execute("DELETE FROM batch_tests")
            connection.execute("DELETE FROM batches")
            for batch in reversed(self._batches):
                connection.execute(
                    "INSERT INTO batches (id, name, created_at, status, active_mode) VALUES (?, ?, ?, ?, ?)",
                    (
                        batch["id"], batch["name"], batch["created_at"],
                        batch["status"], batch.get("active_mode"),
                    ),
                )
                for position, test in enumerate(batch["tests"]):
                    case = {key: value for key, value in test.items() if key not in {"status", "last_report", "history"}}
                    connection.execute(
                        """
                        INSERT INTO batch_tests
                          (batch_id, test_id, position, status, last_report, case_json)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            batch["id"], test["id"], position, test["status"],
                            test.get("last_report"), json.dumps(case),
                        ),
                    )
                for position, execution in enumerate(reversed(batch["executions"])):
                    connection.execute(
                        """
                        INSERT INTO executions
                          (id, batch_id, test_id, position, status, finished_at, report, record_json)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            execution["id"], batch["id"], execution["test_id"], position,
                            execution["status"], execution["finished_at"],
                            execution.get("report"), json.dumps(execution),
                        ),
                    )
