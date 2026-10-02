# PlayRunner

**GitHub description:** A local Playwright test management app for discovering,
filtering, recording, batching, running, and reporting tests.

PlayRunner is a local web application for discovering and filtering Playwright
test cases in nested, expandable folders that follow the project's test-file
structure. It includes named shareable bookmarks, batch execution controls,
reruns, and per-test HTML reports.

## Author and support

- **Author:** Sabareesh Kaveri
- **Support:** [sabareesh.kaveri@gmail.com](mailto:sabareesh.kaveri@gmail.com)

## Requirements

- **Operating system:** macOS, Windows, or Linux.
- **Python:** 3.10 or newer. The PlayRunner application itself uses Python's
  standard library and does not require Python packages from PyPI.
- **Node.js:** 20 or newer, with npm. The locked Playwright dependencies require
  Node.js 20+.
- **Playwright browser:** Chromium, installed using the Playwright command below.
- **Project files:** Run PlayRunner from the Playwright project root, where
  `package.json`, `package-lock.json`, and `playwright.config.ts` are located.
- **Browser UI:** A current desktop browser to open PlayRunner. Headless test runs
  do not need a desktop session. Headed runs and interactive recordings do.
- **Network access:** Needed to install Node packages and the browser on a fresh
  machine. Test targets may have their own network requirements.

## Fresh installation

### 1. Install Git, Python, and Node.js

Install Git, Python 3.10+, and Node.js 20+ (npm is included with Node.js) using
the official installers for your operating system:

- [Git downloads](https://git-scm.com/downloads)
- [Python downloads](https://www.python.org/downloads/)
- [Node.js downloads](https://nodejs.org/en/download)

Open a new terminal and verify the tools:

```sh
git --version
python3 --version
node --version
npm --version
```

On Windows, use `py` in place of `python3` in the Python commands below if that
is the command installed on your system.

### 2. Get the project

Clone the repository using its Git URL, or download and extract its source
archive. Change into the project root: the directory containing `package.json`,
`package-lock.json`, `playwright.config.ts`, and this `Readme.md`.

For example, after cloning:

```sh
git clone https://github.com/sabareeshkaveri/PlayWrightRunner.git
cd PlayWrightRunner
```

Replace both uppercase placeholders with the repository URL and directory name
supplied for your copy of PlayRunner.

### 3. Create an isolated Python environment (recommended)

PlayRunner uses Python's standard library and has no `pip` requirements to install.
An environment keeps its Python interpreter isolated from other projects:

```sh
python3 -m venv .venv
```

Activate it on macOS/Linux:

```sh
source .venv/bin/activate
```

Activate it in Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
```

If activation is unavailable or not desired, run PlayRunner with the system
interpreter instead. On Windows, use `py` in place of `python3` where necessary.

### 4. Install JavaScript dependencies

```sh
npm ci
```

The project declares `@playwright/test` and `@types/node` in `package.json`.
`npm ci` installs these along with the locked Playwright packages.

### 5. Install the Playwright browser

Install Chromium and its Playwright-managed browser files:

```sh
npx playwright install chromium
```

On Linux, install Chromium and its operating-system libraries with:

```sh
npx playwright install --with-deps chromium
```

This Linux command may ask for administrator permission. On macOS and Windows,
use `npx playwright install chromium` as shown above.

### 6. Start PlayRunner

From the project root:

```sh
npm run app
```

Open [http://127.0.0.1:8765](http://127.0.0.1:8765) in a browser. Stop the
server with **Ctrl+C** in the terminal.

Optional host/port/project-root arguments:

```sh
python3 -m PlaywrightExecutor.app --host 127.0.0.1 --port 8766
python3 -m PlaywrightExecutor.app --project-root /path/to/PlayWrightRunner --port 8766
```

`--project-root` must point to the directory containing `playwright.config.ts`.
For this checkout, that is `/Users/kaverisabareesh/Playwright/PlayWrightRunner`,
not its parent `/Users/kaverisabareesh/Playwright`.

On Windows, for example:

```powershell
py -m PlaywrightExecutor.app
```
If the `.venv` environment is active, use `python -m PlaywrightExecutor.app`
instead.

## Using PlayRunner

1. Browse tests in nested folder accordions following their project file paths.
   Filter by title, file, project, tag, bookmark, or last-run result; use the
   folder tree's select-visible checkbox to add every filtered case to the batch.
2. Bookmark selected cases in named sets. Use **Share JSON** and **Import JSON**
   to share these sets.
3. Add bookmarked or manually selected cases to the batch draft, name the batch,
   and choose **Create batch**.
4. Open **Batch monitor** and double-click a batch to see its test cases and
   execution results.
5. Right-click a case to start it, or use **Re-run batch** and **Re-run failed**.
   Double-click an execution to open its report.
6. Use **Options** to configure a proxy, base URL, headed or headless mode,
   parallel execution count (1–8), retry count (0–5), and the default reporter.
7. Open **Record test**, enter a test name and a project-relative folder (for
   example `tests/GUI/authentication`), then choose **Start recording**.
   Playwright Inspector opens a visible browser even when test runs are
   configured as headless. Interact with the site, then choose **Stop & save**;
   the generated `.spec.ts` file appears in the selected folder and in the
   refreshed test list. Existing files are not overwritten.

## Configuration and data

- The default HTTP address is `127.0.0.1:8765`; override it with `--host` and
  `--port`.
- Execution options are managed in **Options** and stored in
  `db/PlayRunner-settings.json`.
- **Base URL** must be an HTTP or HTTPS URL and is used by Playwright tests and
  the recorder. **Headless/Headed** affects future test runs; recording always
  opens a visible browser for interactive capture.
- Batch definitions and latest execution results per test are stored in
  `db/PlayRunner.sqlite3`.
- Named bookmark sets are stored separately in the shareable
  `bookmark/bookmark.json`.
- New reports are written under `reports/PlayRunner/` (reporter-specific output
  may include additional files).
- Existing `PlayRun.sqlite3`, `PlayRun-settings.json`, and dashboard-named data
  are migrated to the PlayRunner-named files on startup; source files are kept.
  Bookmark JSON and legacy batch data are also migrated when available.
- For proxy configuration, use an HTTP, HTTPS, or SOCKS5 proxy URL in **Options**.
  The proxy is passed to Playwright only for PlayRunner-started executions.
- PlayRunner's current API prefix is `/api/playRunner/`. The previous
  `/api/playrun/` and `/api/` routes remain available for compatibility.

## Running tests

Run PlayRunner's Python regression tests:

```sh
npm run test:python
```

Run the project's Playwright test suite, or list discovered tests without
running them:

```sh
npm test
npm run test:list
```

## Version and history

The current package version in `package.json` is **1.0.0**. PlayRunner does not
currently define a separate application version. The following history records
the feature set documented for the initial 1.0.0 release:

| Version | Status | Highlights |
| --- | --- | --- |
| 1.0.0 | Current package version | **Test discovery:** browse and filter cases by project, tags, file, text, and previous result. **Bookmarks:** create, share, and import named JSON bookmark sets. **Batches:** compose batches and monitor executions, start individual cases, re-run batches or failed cases, and open reports. **Execution options:** configure proxy, base URL, headed/headless mode, parallelism, retries, and custom or Playwright HTML reporter. **Recorder:** capture browser interactions with Playwright Inspector and save named tests in a project-relative folder. **Persistence:** SQLite batch/results store, JSON settings and bookmarks, latest-result retention, and migration from previous PlayRun and dashboard data. **Interface:** local PlayRunner web app with light-blue styling and automatic monitor refresh. |

Document future releases here and update the project package version when
publishing a new version.