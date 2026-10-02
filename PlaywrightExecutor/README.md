# PlayRunner

PlayRunner is a dependency-free local app that discovers Playwright test cases,
displays them in expandable nested folders matching their file paths, filters
by text, project, tag, file, bookmark, and last-run result, and groups selected
cases into named batches. Bookmark tests for later, open their source, or launch
a one-test quick run. Each execution uses the project's custom HTML reporter;
double-click its row to open the generated report.

## Run

From the Playwright project root:

```sh
python3 -m PlaywrightExecutor.app
```

Open <http://127.0.0.1:8765>. To use a different project directory, port, or
host:

```sh
python3 -m PlaywrightExecutor.app --project-root /Users/kaverisabareesh/Playwright/ --port 8766
```

The app requires the project's installed Playwright dependencies and
`playwright.config.ts`. It binds to `127.0.0.1` by default. Discovered test
metadata, batch definitions, latest execution results per test in each batch,
and report links are stored in `db/PlayRunner.sqlite3`. On first launch, existing
data is migrated from `db/PlayRun.sqlite3`, `db/dashboard.sqlite3`, or
`reports/dashboard.sqlite3`; source databases are retained. Named
bookmarks are saved separately in the shareable `bookmark/bookmark.json` file;
PlayRunner migrates existing bookmark sets and favorites from earlier JSON
and SQLite locations when creating that file. Re-running a test replaces its
previous execution result in that batch; older results are pruned on startup.
Use **Share JSON** to download the file or **Import JSON** to merge another
person's sets into the local collection. Matching test cases by file, title,
and project lets imports survive source line-number changes. Existing
`reports/dashboard-batches.json` data is migrated on first launch and left in
place as a backup. Execution preferences are stored in
`db/PlayRunner-settings.json`; existing `db/PlayRun-settings.json` and
`db/dashboard-settings.json` settings are migrated while their source files
are retained.
Use **Options** to configure an optional HTTP/HTTPS/SOCKS5 proxy, an HTTP or
HTTPS base URL, headed or headless execution, parallel execution count (1–8),
Playwright retries (0–5), and either **Custom reports** or the built-in
**Playwright HTML report**. Settings are captured when a run starts; changing
them does not alter an execution already in progress.

## Workflow

1. Filter project test cases by title, file, project, tag, bookmark, or last-run
   result. Use the star for the built-in **Favorites** set, or select tests and
   save them under a reusable bookmark-set name.
2. Share bookmark sets with **Share JSON** and bring them into another
   workspace with **Import JSON**. Importing merges cases into sets with matching
   names and adds new sets without replacing local bookmarks.
3. Select a saved bookmark set to add its available test cases to the separate
   **Batch contents** draft. Review or remove cases, give the batch a name, and
   choose **Create batch**. Manually selected test cases can be included in the
   same draft. Use **Open file** or **Run now** for an individual case.
4. Open **Batch monitor** and double-click a batch.
5. In the batch monitor, right-click a case and choose **Start this test case**,
   or use **Re-run batch** / **Re-run failed**.
6. Review each execution's status and captured output. Double-click an
   execution row to open its custom HTML report.
7. Open **Record test**, enter a test name and a project-relative folder such as
   `tests/GUI/authentication`, then select **Start recording**. Playwright
   Inspector opens a visible browser at the configured base URL. Interact with
   the site and select **Stop & save** to create a named `.spec.ts` file. The
   destination folder is created when needed, existing test files are not
   overwritten, and the test list refreshes after a successful save.

The configured headed/headless mode applies to test execution only. Recording
always opens a visible browser because Playwright Inspector requires
interactive browser input. New mode, base URL, proxy, and reporter settings are
stored in `db/PlayRunner-settings.json`.

The app uses `/api/playRunner/...` routes. The former `/api/playrun/...` and
`/api/...` routes remain available for existing integrations. New runs store
reports under `reports/PlayRunner/`; previous report links remain valid.

Run the focused tests with:

```sh
python3 -m unittest PlaywrightExecutor.test_playrun -v
```

For support, email [sabareesh.kaveri@gmail.com](mailto:sabareesh.kaveri@gmail.com).
