from __future__ import annotations

import re
from typing import Any

PROJECT_CONFIG_FILENAME = "playwright.config.ts"
TESTS_DIRECTORY_NAME = "tests"
REPORTS_DIRECTORY_NAME = "reports"
DATABASE_DIRECTORY_NAME = "db"
DATABASE_FILENAME = "PlayRunner.sqlite3"
SETTINGS_FILENAME = "PlayRunner-settings.json"
BOOKMARK_DIRECTORY_NAME = "bookmark"
BOOKMARK_FILENAME = "bookmark.json"
BOOKMARK_EXPORT_FILENAME = "PlayRunner-bookmarks.json"
PREVIOUS_DATABASE_FILENAME = "PlayRun.sqlite3"
PREVIOUS_SETTINGS_FILENAME = "PlayRun-settings.json"
LEGACY_DATABASE_FILENAME = "dashboard.sqlite3"
LEGACY_SETTINGS_FILENAME = "dashboard-settings.json"
LEGACY_BOOKMARK_FILENAME = "dashboard-bookmarks.json"
LEGACY_BATCH_FILENAME = "dashboard-batches.json"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_PROJECT_NAME = "chromium"
PRODUCT_NAME = "PlayRunner"
SUPPORT_EMAIL = "sabareesh.kaveri@gmail.com"
API_PREFIX = "/api/playRunner"
PREVIOUS_API_PREFIX = "/api/playrun"
LEGACY_API_PREFIX = "/api"
PROXY_ENVIRONMENT_VARIABLE = "PLAYRUNNER_PROXY_SERVER"
PREVIOUS_PROXY_ENVIRONMENT_VARIABLE = "PLAYRUN_PROXY_SERVER"
LEGACY_PROXY_ENVIRONMENT_VARIABLE = "DASHBOARD_PROXY_SERVER"
HEADLESS_ENVIRONMENT_VARIABLE = "PLAYRUNNER_HEADLESS"
BASE_URL_ENVIRONMENT_VARIABLE = "PLAYRUNNER_BASE_URL"
PREVIOUS_HEADLESS_ENVIRONMENT_VARIABLE = "PLAYRUN_HEADLESS"
PREVIOUS_BASE_URL_ENVIRONMENT_VARIABLE = "PLAYRUN_BASE_URL"

REPORTER_CUSTOM = "custom"
REPORTER_HTML = "html"
REPORTERS = frozenset({REPORTER_CUSTOM, REPORTER_HTML})
DEFAULT_SETTINGS: dict[str, Any] = {
    "proxy": "",
    "workers": 1,
    "retries": 0,
    "reporter": REPORTER_CUSTOM,
    "headless": False,
    "base_url": "http://rahulshettyacademy.com/",
}
MIN_WORKERS = 1
MAX_WORKERS = 8
MIN_RETRIES = 0
MAX_RETRIES = 5

TEST_FILE_EXTENSIONS = frozenset({".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"})
SKIP_DIRECTORIES = frozenset(
    {"node_modules", REPORTS_DIRECTORY_NAME, "test-results", "playwright-report", ".git"}
)
TERMINAL_STATUSES = frozenset(
    {"passed", "failed", "timedOut", "skipped", "flaky", "interrupted"}
)
FAILED_STATUSES = frozenset({"failed", "timedOut", "interrupted"})

TEST_CALL = re.compile(
    r"\b(?:test|it)(?:\.(?:only|skip|fixme|fail|slow))?\s*"
    r"\(\s*([\"'`])((?:\\.|(?!\1).)*)\1",
    re.DOTALL,
)
TAG_PATTERN = re.compile(r"([\"'`])(@[A-Za-z0-9_.-]+)\1")
PROJECT_PATTERN = re.compile(r"\bname\s*:\s*['\"]([^'\"]+)['\"]")
TEST_DIR_PATTERN = re.compile(r"\btestDir\s*:\s*['\"]([^'\"]+)['\"]")
REPORT_LINE = re.compile(
    r"Custom report generated:\s*(.+?results\.html)\s*$",
    re.MULTILINE,
)
