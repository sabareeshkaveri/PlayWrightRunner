from __future__ import annotations

import json
import os
import re
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .constants import (
    BASE_URL_ENVIRONMENT_VARIABLE,
    DEFAULT_SETTINGS,
    HEADLESS_ENVIRONMENT_VARIABLE,
    LEGACY_PROXY_ENVIRONMENT_VARIABLE,
    PREVIOUS_PROXY_ENVIRONMENT_VARIABLE,
    PREVIOUS_BASE_URL_ENVIRONMENT_VARIABLE,
    PREVIOUS_HEADLESS_ENVIRONMENT_VARIABLE,
    PRODUCT_NAME,
    PROXY_ENVIRONMENT_VARIABLE,
    PROJECT_CONFIG_FILENAME,
    REPORTER_CUSTOM,
    REPORTER_HTML,
    REPORTERS,
    REPORTS_DIRECTORY_NAME,
    REPORT_LINE,
)


@dataclass(frozen=True)
class ExecutionResult:
    status: str
    exit_code: int
    duration_seconds: float
    output: str
    report: str | None


class PlaywrightExecutor:
    def __init__(self, project_root: Path, timeout_seconds: int = 3600) -> None:
        self.project_root = project_root.resolve()
        self.timeout_seconds = timeout_seconds
        self.playwright_cli = self.project_root / "node_modules" / ".bin" / "playwright"

    def command_for(
        self,
        test_case: dict[str, str],
        settings: dict[str, Any] | None = None,
    ) -> list[str]:
        effective_settings = {**DEFAULT_SETTINGS, **(settings or {})}
        reporter = effective_settings["reporter"]
        if reporter not in REPORTERS:
            raise ValueError("Reporter must be either 'custom' or 'html'.")
        test_file = (self.project_root / test_case["file"]).resolve()
        if not test_file.is_relative_to(self.project_root):
            raise ValueError("Test file must be inside the project.")
        if not test_file.is_file():
            raise FileNotFoundError(f"Test file no longer exists: {test_case['file']}")
        if not self.playwright_cli.is_file():
            raise FileNotFoundError(
                f"Playwright CLI not found at {self.playwright_cli}. "
                "Install project dependencies before running tests."
            )

        command = [
            str(self.playwright_cli),
            "test",
            f"--config={PROJECT_CONFIG_FILENAME}",
            test_case["file"],
            "--grep",
            re.escape(test_case["title"]),
            f"--reporter={'./fixtures/html-reporter.ts' if reporter == REPORTER_CUSTOM else REPORTER_HTML}",
            "--workers=1",
            f"--retries={effective_settings['retries']}",
        ]
        project = test_case.get("project", "")
        if project:
            command.append(f"--project={project}")
        return command

    def run(
        self,
        test_case: dict[str, str],
        settings: dict[str, Any] | None = None,
    ) -> ExecutionResult:
        effective_settings = {**DEFAULT_SETTINGS, **(settings or {})}
        reporter = effective_settings["reporter"]
        report_directory = f"{PRODUCT_NAME}/{uuid.uuid4().hex}"
        command = self.command_for(test_case, effective_settings)
        environment = os.environ.copy()
        environment.pop(LEGACY_PROXY_ENVIRONMENT_VARIABLE, None)
        environment.pop(PREVIOUS_PROXY_ENVIRONMENT_VARIABLE, None)
        environment["PLAYWRIGHT_HTML_OPEN"] = "never"
        environment.pop("PLAYWRIGHT_HTML_OUTPUT_DIR", None)
        if effective_settings["proxy"]:
            environment[PROXY_ENVIRONMENT_VARIABLE] = effective_settings["proxy"]
            environment[PREVIOUS_PROXY_ENVIRONMENT_VARIABLE] = effective_settings["proxy"]
        else:
            environment.pop(PROXY_ENVIRONMENT_VARIABLE, None)
            environment.pop(PREVIOUS_PROXY_ENVIRONMENT_VARIABLE, None)
        environment[HEADLESS_ENVIRONMENT_VARIABLE] = str(effective_settings["headless"]).lower()
        environment[BASE_URL_ENVIRONMENT_VARIABLE] = effective_settings["base_url"]
        environment[PREVIOUS_HEADLESS_ENVIRONMENT_VARIABLE] = environment[HEADLESS_ENVIRONMENT_VARIABLE]
        environment[PREVIOUS_BASE_URL_ENVIRONMENT_VARIABLE] = environment[BASE_URL_ENVIRONMENT_VARIABLE]
        if reporter == REPORTER_HTML:
            environment["PLAYWRIGHT_HTML_OUTPUT_DIR"] = str(
                self.project_root / REPORTS_DIRECTORY_NAME / report_directory
            )
        try:
            completed = subprocess.run(
                command,
                cwd=self.project_root,
                env=environment,
                capture_output=True,
                text=True,
                errors="replace",
                timeout=self.timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            output = self._format_output(error.stdout, error.stderr)
            return ExecutionResult("timedOut", 124, self.timeout_seconds, output, None)

        output = self._format_output(completed.stdout, completed.stderr)
        report = self._report_path(output)
        if reporter == REPORTER_HTML:
            html_report = self.project_root / REPORTS_DIRECTORY_NAME / report_directory / "index.html"
            if html_report.is_file():
                report = html_report.relative_to(
                    self.project_root / REPORTS_DIRECTORY_NAME
                ).as_posix()
        status = "passed" if completed.returncode == 0 else "failed"
        duration_seconds = self._reported_duration(output)
        if report:
            report_status, report_duration = self._read_report_facts(report)
            if report_status:
                status = report_status
            if report_duration is not None:
                duration_seconds = report_duration
        return ExecutionResult(status, completed.returncode, duration_seconds, output, report)

    @staticmethod
    def _format_output(stdout: str | bytes | None, stderr: str | bytes | None) -> str:
        def decode(value: str | bytes | None) -> str:
            if isinstance(value, bytes):
                return value.decode("utf-8", errors="replace")
            return value or ""

        return "\n".join(part.strip() for part in (decode(stdout), decode(stderr)) if part.strip())

    def _report_path(self, output: str) -> str | None:
        match = REPORT_LINE.search(output)
        if not match:
            return None
        report = Path(match.group(1).strip()).resolve()
        reports_directory = (self.project_root / REPORTS_DIRECTORY_NAME).resolve()
        if report.is_relative_to(reports_directory) and report.is_file():
            return report.relative_to(reports_directory).as_posix()
        return None

    @staticmethod
    def validate_proxy(proxy: str) -> str:
        normalized = proxy.strip()
        if not normalized:
            return ""
        try:
            parsed = urlsplit(normalized)
            valid_port = parsed.port is None or 1 <= parsed.port <= 65535
        except ValueError as error:
            raise ValueError("Proxy must be a valid HTTP or SOCKS5 URL.") from error
        if (
            parsed.scheme not in {"http", "https", "socks5"}
            or not parsed.hostname
            or not valid_port
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Proxy must be a valid HTTP or SOCKS5 URL, such as http://proxy.example:8080.")
        return normalized

    @staticmethod
    def _reported_duration(output: str) -> float:
        match = re.search(r"Final result:\s*\w+\s+(\d+(?:\.\d+)?)s", output)
        return float(match.group(1)) if match else 0.0

    def _read_report_facts(self, report: str) -> tuple[str | None, float | None]:
        reports_root = (self.project_root / REPORTS_DIRECTORY_NAME).resolve()
        results_path = (reports_root / report).resolve()
        if not results_path.is_relative_to(reports_root):
            return None, None
        results_path = results_path.with_name("results.json")
        try:
            results = json.loads(results_path.read_text(encoding="utf-8"))
            tests = results.get("tests", [])
            outcomes = {test.get("outcome") for test in tests}
            if "failed" in outcomes:
                status = "failed"
            elif "flaky" in outcomes:
                status = "flaky"
            elif outcomes and outcomes <= {"skipped"}:
                status = "skipped"
            elif "passed" in outcomes or "expected" in outcomes:
                status = "passed"
            else:
                status = None
            run = results.get("run", {})
            duration = run.get("duration")
            return status, float(duration) / 1000 if isinstance(duration, (int, float)) else None
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return None, None
