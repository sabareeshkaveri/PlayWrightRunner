from __future__ import annotations

import json
import mimetypes
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from .constants import (
    BOOKMARK_EXPORT_FILENAME,
    API_PREFIX,
    DEFAULT_HOST,
    DEFAULT_PORT,
    LEGACY_API_PREFIX,
    PREVIOUS_API_PREFIX,
    PRODUCT_NAME,
    REPORTS_DIRECTORY_NAME,
    SUPPORT_EMAIL,
)
from .controller import ProjectController
from .widgets import error_page


class PlayRunnerHandler(BaseHTTPRequestHandler):
    controller: ProjectController
    project_root: Path
    app_directory: Path
    server_version = "PlayRunner/1.0"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        route_path = self._normalize_api_path(parsed.path)
        if parsed.path == "/":
            page_path = self.app_directory / "index.html"
            if page_path.is_file():
                self._send_file(page_path, {"{{SUPPORT_EMAIL}}": SUPPORT_EMAIL})
            else:
                content = error_page(f"{PRODUCT_NAME} view is missing: {page_path}")
                self.send_response(500)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
        elif route_path == "/api/tests":
            self._send_json(200, self.controller.discover_tests())
        elif route_path == "/api/batches":
            self._send_json(200, {"batches": self.controller.list_batches()})
        elif route_path == "/api/settings":
            self._send_json(200, {"settings": self.controller.get_settings()})
        elif route_path == "/api/recording":
            self._send_json(200, {"recording": self.controller.recording_status()})
        elif route_path == "/api/bookmark-sets":
            self._send_json(200, {"bookmark_sets": self.controller.list_bookmark_sets()})
        elif route_path == "/api/bookmark-sets/export":
            self._send_json(
                200,
                self.controller.export_bookmark_sets(),
                download_name=BOOKMARK_EXPORT_FILENAME,
            )
        elif route_path.startswith("/api/batches/"):
            batch_id = unquote(route_path.removeprefix("/api/batches/"))
            try:
                self._send_json(200, {"batch": self.controller.get_batch(batch_id)})
            except KeyError as error:
                self._send_json(404, {"error": str(error)})
        elif parsed.path.startswith("/reports/"):
            self._serve_report(unquote(parsed.path.removeprefix("/reports/")))
        elif parsed.path.startswith("/source/"):
            self._serve_source(unquote(parsed.path.removeprefix("/source/")))
        else:
            self._send_json(404, {"error": "Not found."})

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        route_path = self._normalize_api_path(parsed.path)
        try:
            payload = self._read_json()
            if route_path == "/api/batches":
                batch = self.controller.create_batch(
                    str(payload.get("name", "")),
                    payload.get("test_ids", []),
                )
                self._send_json(201, {"batch": batch})
                return
            if route_path == "/api/settings":
                settings = self.controller.update_settings(payload)
                self._send_json(200, {"settings": settings})
                return
            if route_path == "/api/recording":
                recording = self.controller.start_recording(
                    str(payload.get("test_name", "")),
                    str(payload.get("location", "")),
                )
                self._send_json(202, {"recording": recording})
                return
            if route_path == "/api/bookmarks":
                test_id = str(payload.get("test_id", ""))
                bookmarked = payload.get("bookmarked")
                if not isinstance(bookmarked, bool):
                    raise ValueError("Bookmark state must be true or false.")
                self.controller.set_bookmark(test_id, bookmarked)
                self._send_json(200, {"test_id": test_id, "bookmarked": bookmarked})
                return
            if route_path == "/api/bookmark-sets":
                test_ids = payload.get("test_ids", [])
                if not isinstance(test_ids, list) or not all(isinstance(item, str) for item in test_ids):
                    raise ValueError("Bookmark test_ids must be a list of test case IDs.")
                bookmark_set = self.controller.create_bookmark_set(
                    str(payload.get("name", "")),
                    test_ids,
                )
                self._send_json(201, {"bookmark_set": bookmark_set})
                return
            if route_path == "/api/bookmark-sets/import":
                imported = self.controller.import_bookmark_sets(payload)
                self._send_json(200, {"bookmark_sets": imported})
                return
            if route_path == "/api/quick-run":
                batch = self.controller.quick_run(str(payload.get("test_id", "")))
                self._send_json(202, {"batch": batch})
                return
            parts = route_path.strip("/").split("/")
            if len(parts) == 4 and parts[:2] == ["api", "batches"]:
                batch_id, action = parts[2], parts[3]
                if action == "start":
                    self.controller.start_test(batch_id, str(payload.get("test_id", "")))
                elif action == "rerun":
                    self.controller.rerun_batch(batch_id)
                elif action == "rerun-failed":
                    self.controller.rerun_failed(batch_id)
                else:
                    self._send_json(404, {"error": "Unknown batch action."})
                    return
                self._send_json(202, {"batch": self.controller.get_batch(batch_id)})
                return
            self._send_json(404, {"error": "Not found."})
        except sqlite3.IntegrityError as error:
            self._send_json(409, {"error": str(error)})
        except (ValueError, TypeError) as error:
            self._send_json(400, {"error": str(error)})
        except RuntimeError as error:
            self._send_json(400, {"error": str(error)})
        except KeyError as error:
            self._send_json(404, {"error": str(error)})
        except (OSError, json.JSONDecodeError) as error:
            self._send_json(400, {"error": str(error)})

    def do_DELETE(self) -> None:
        route_path = self._normalize_api_path(urlparse(self.path).path)
        if route_path == "/api/recording":
            try:
                self._send_json(200, {"recording": self.controller.stop_recording()})
            except (OSError, RuntimeError, ValueError) as error:
                self._send_json(400, {"error": str(error)})
            return
        parts = route_path.strip("/").split("/")
        if len(parts) != 3 or parts[:2] != ["api", "bookmark-sets"]:
            self._send_json(404, {"error": "Not found."})
            return
        try:
            self.controller.delete_bookmark_set(unquote(parts[2]))
            self._send_json(200, {"deleted": True})
        except KeyError as error:
            self._send_json(404, {"error": str(error)})
        except sqlite3.Error as error:
            self._send_json(500, {"error": f"Could not delete bookmark set: {error}"})
        except OSError as error:
            self._send_json(500, {"error": f"Could not update bookmark JSON: {error}"})

    def log_message(self, format: str, *args: Any) -> None:
        print(f"[{PRODUCT_NAME}] {self.address_string()} - {format % args}")

    @staticmethod
    def _normalize_api_path(path: str) -> str:
        if path == API_PREFIX:
            return LEGACY_API_PREFIX
        if path.startswith(f"{API_PREFIX}/"):
            return f"{LEGACY_API_PREFIX}{path[len(API_PREFIX):]}"
        if path == PREVIOUS_API_PREFIX:
            return LEGACY_API_PREFIX
        if path.startswith(f"{PREVIOUS_API_PREFIX}/"):
            return f"{LEGACY_API_PREFIX}{path[len(PREVIOUS_API_PREFIX):]}"
        return path

    def _read_json(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length > 1_000_000:
                raise ValueError("Request payload is too large.")
            value = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError(f"Invalid JSON request: {error}") from error
        if not isinstance(value, dict):
            raise ValueError("JSON request must be an object.")
        return value

    def _serve_report(self, relative_path: str) -> None:
        reports_root = (self.project_root / REPORTS_DIRECTORY_NAME).resolve()
        target = (reports_root / relative_path).resolve()
        if not target.is_relative_to(reports_root) or not target.is_file():
            self._send_json(404, {"error": "Report file not found."})
            return
        self._send_file(target)

    def _serve_source(self, relative_path: str) -> None:
        tests_root = (self.project_root / "tests").resolve()
        target = (self.project_root / relative_path).resolve()
        if (
            not target.is_relative_to(tests_root)
            or target.suffix not in {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"}
            or not target.is_file()
        ):
            self._send_json(404, {"error": "Test source file not found."})
            return
        try:
            content = target.read_bytes()
        except OSError:
            self._send_json(404, {"error": "Test source file could not be read."})
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(content)

    def _send_file(self, path: Path, replacements: dict[str, str] | None = None) -> None:
        try:
            content = path.read_bytes()
        except OSError:
            self._send_json(404, {"error": "Requested file was not found."})
            return
        for placeholder, replacement in (replacements or {}).items():
            content = content.replace(placeholder.encode("utf-8"), replacement.encode("utf-8"))
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if path.suffix == ".html":
            content_type = "text/html; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(content)

    def _send_json(
        self,
        status: int,
        value: Any,
        download_name: str | None = None,
    ) -> None:
        content = json.dumps(value).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        if download_name is not None:
            self.send_header("Content-Disposition", f'attachment; filename="{download_name}"')
        self.end_headers()
        self.wfile.write(content)
PlayRunHandler = PlayRunnerHandler
DashboardHandler = PlayRunnerHandler


def create_server(
    project_root: Path,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
) -> ThreadingHTTPServer:
    app_directory = Path(__file__).resolve().parent
    controller = ProjectController(project_root)
    handler = type(
        "ConfiguredPlayRunnerHandler",
        (PlayRunnerHandler,),
        {
            "controller": controller,
            "project_root": project_root.resolve(),
            "app_directory": app_directory,
        },
    )
    return ThreadingHTTPServer((host, port), handler)
