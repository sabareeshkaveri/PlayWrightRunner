// clean-html-reporter.ts
import type { Reporter, FullConfig, FullResult, TestCase, TestError, TestResult, TestStep } from '@playwright/test/reporter';
import * as fs from 'fs';
import * as path from 'path';

interface AnnotationSummary {
  type: string;
  description?: string;
}

interface AttachmentSummary {
  name: string;
  contentType: string;
  href?: string;
  size?: number;
}

interface StepSummary {
  title: string;
  subtitle?: string;
  category: string;
  duration: number;
  params?: string;
  comparison?: { expected: string; actual: string };
  error?: string;
  depth: number;
}

interface AttemptSummary {
  retry: number;
  status: string;
  duration: number;
  startedAt: string;
  workerIndex: number;
  parallelIndex: number;
  errors: string[];
  attachments: AttachmentSummary[];
  stdout: string;
  stderr: string;
  steps: StepSummary[];
}

interface TestSummary {
  testCase: TestCase;
  id: string;
  title: string;
  file: string;
  project: string;
  browser: string;
  baseURL: string;
  headless: string;
  viewport: string;
  expectedStatus: string;
  actualStatus: string;
  outcome: string;
  timeout: number;
  retries: number;
  tags: string[];
  annotations: AnnotationSummary[];
  attempts: AttemptSummary[];
}

interface RunSettings {
  projects: { name: string; browser: string; baseURL: string; headless: string; viewport: string }[];
  workers: number;
  retries: number;
  timeout: number;
  fullyParallel: boolean;
  nodeVersion: string;
  platform: string;
  architecture: string;
  ci: boolean;
}

const escapeHtml = (value: string) => value.replace(/[&<>"']/g, character => ({
  '&': '&amp;',
  '<': '&lt;',
  '>': '&gt;',
  '"': '&quot;',
  "'": '&#39;'
}[character] ?? character));

const formatDuration = (duration: number) => duration >= 1000
  ? `${(duration / 1000).toFixed(2)}s`
  : `${duration}ms`;

const safeName = (value: string) => value.replace(/[^a-z0-9._-]+/gi, '_').replace(/^\.+/, '').slice(0, 80) || 'attachment';

const stringify = (value: unknown) => {
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
};

const stripAnsi = (value: string) => value.replace(/\u001b\[[0-?]*[ -/]*[@-~]/g, '');

const errorDetails = (error: TestError) => [
  error.message ? stripAnsi(error.message) : undefined,
  error.location ? `Location: ${error.location.file}:${error.location.line}:${error.location.column}` : undefined,
  error.snippet ? stripAnsi(error.snippet) : undefined,
  error.stack ? stripAnsi(error.stack) : undefined
].filter(Boolean).join('\n');

const getTestEnvironment = (test: TestCase) => {
  const project = test.parent.project();
  const use = project?.use as Record<string, unknown> | undefined;
  const viewport = use?.viewport;
  let baseURL = typeof use?.baseURL === 'string' ? use.baseURL : 'not configured';
  try {
    if (/^https?:\/\//i.test(baseURL)) baseURL = new URL(baseURL).origin;
  } catch {
    baseURL = 'configured';
  }

  return {
    browser: typeof use?.browserName === 'string' ? use.browserName : project?.name ?? 'default',
    baseURL,
    headless: typeof use?.headless === 'boolean' ? String(use.headless) : 'default',
    viewport: viewport && typeof viewport === 'object' ? stringify(viewport) : 'default'
  };
};

const formatFailure = (error: string) => {
  const expected = error.match(/^\s*Expected(?:\s+[^:]+)?:\s*(.+)$/m)?.[1];
  const actual = error.match(/^\s*Received(?:\s+[^:]+)?:\s*(.+)$/m)?.[1];
  const summary = error.split('\n').map(line => line.trim()).find(Boolean) ?? 'Test failed';
  const highlightedError = error.split('\n').map(line => {
    const trimmed = line.trimStart();
    if (/^(Expected|Received|Error|Timeout|Call log):/i.test(trimmed)) {
      return `<span class="failure-line">${escapeHtml(line)}</span>`;
    }
    return escapeHtml(line);
  }).join('\n');

  return `<section class="failure-detail">
    <div class="failure-heading"><span class="failure-icon">!</span><div><strong>${escapeHtml(summary)}</strong><small>Assertion or test failure</small></div></div>
    ${expected || actual ? `<div class="failure-comparison">${expected ? `<div><b>Expected</b><code>${escapeHtml(expected)}</code></div>` : ''}${actual ? `<div><b>Actual</b><code>${escapeHtml(actual)}</code></div>` : ''}</div>` : ''}
    <details class="failure-more"><summary>Full failure message, source and stack</summary><pre>${highlightedError}</pre></details>
  </section>`;
};

const assertionComparison = (step: TestStep) => {
  const message = step.error?.message ? stripAnsi(step.error.message) : '';
  const expectedFromError = message.match(/^\s*Expected(?:\s+[^:]+)?:\s*(.+)$/m)?.[1];
  const actualFromError = message.match(/^\s*Received(?:\s+[^:]+)?:\s*(.+)$/m)?.[1];
  const params = step.params as Record<string, unknown> | undefined;
  const expectedFromParams = params && ['expected', 'expectedText', 'expectedValue']
    .find(key => params[key] !== undefined);
  const actualFromParams = params?.actual;
  if (step.category !== 'expect' && (!expectedFromParams || actualFromParams === undefined)) return undefined;
  if (step.category === 'expect' && !step.error && actualFromParams === undefined) return undefined;
  const expected = expectedFromError ?? (expectedFromParams ? stringify(params?.[expectedFromParams]) : undefined);

  if (!expected) return undefined;
  return {
    expected,
    actual: actualFromError ?? (actualFromParams !== undefined
      ? stringify(actualFromParams)
      : step.error ? 'See assertion error details' : 'Assertion passed; Playwright does not expose the received value on success')
  };
};

const flattenSteps = (steps: TestStep[], depth = 0): StepSummary[] => steps.flatMap(step => {
  const comparison = assertionComparison(step);
  const nestedSteps = comparison && step.category !== 'expect'
    ? step.steps.filter(child => !(child.category === 'expect' && child.title === step.title))
    : step.steps;

  return [{
    title: step.title,
    subtitle: step.titlePath().length > 1 ? step.titlePath().slice(0, -1).join(' / ') : undefined,
    category: step.category,
    duration: step.duration,
    params: step.params ? stringify(step.params) : undefined,
    comparison,
    error: step.error?.message ? stripAnsi(step.error.message) : undefined,
    depth
  }, ...flattenSteps(nestedSteps, depth + 1)];
});

export default class CleanHtmlReporter implements Reporter {
  private testResults = new Map<string, TestSummary>();
  private runErrors: string[] = [];
  private globalOutput: string[] = [];
  private runStartedAt = new Date();
  private runId = '';
  private reportDirectory = '';
  private assetDirectory = '';
  private settings: RunSettings = { projects: [], workers: 0, retries: 0, timeout: 0, fullyParallel: false, nodeVersion: process.version, platform: process.platform, architecture: process.arch, ci: Boolean(process.env.CI) };

  onBegin(config: FullConfig, suite: { allTests(): TestCase[] }) {
    this.runStartedAt = new Date();
    this.runId = this.runStartedAt.toISOString().replace(/\.\d{3}Z$/, '').replace('T', '_').replace(/:/g, '-');
    const testNames = [...new Set(suite.allTests().map(test => test.title))];
    const caseFolder = testNames.length === 1
      ? safeName(testNames[0])
      : testNames.length === 0 ? 'no-tests' : 'multiple-tests';
    this.reportDirectory = path.join(process.cwd(), 'reports', caseFolder, this.runId);
    this.assetDirectory = path.join(this.reportDirectory, 'assets');
    fs.mkdirSync(this.assetDirectory, { recursive: true });
    process.env.PLAYWRIGHT_HTML_OUTPUT_DIR = path.join(this.reportDirectory, 'playwright-report');
    this.settings = {
      projects: config.projects.map(project => {
        const use = project.use as Record<string, unknown>;
        const baseURL = typeof use.baseURL === 'string' && /^https?:\/\//i.test(use.baseURL)
          ? new URL(use.baseURL).origin
          : typeof use.baseURL === 'string' ? 'configured' : 'not configured';
        return {
          name: project.name,
          browser: typeof use.browserName === 'string' ? use.browserName : project.name || 'default',
          baseURL,
          headless: typeof use.headless === 'boolean' ? String(use.headless) : 'default',
          viewport: use.viewport && typeof use.viewport === 'object' ? stringify(use.viewport) : 'default'
        };
      }),
      workers: config.workers,
      retries: config.projects.reduce((maximum, project) => Math.max(maximum, project.retries), 0),
      timeout: config.projects.reduce((maximum, project) => Math.max(maximum, project.timeout), 0),
      fullyParallel: config.fullyParallel,
      nodeVersion: process.version,
      platform: process.platform,
      architecture: process.arch,
      ci: Boolean(process.env.CI)
    };

    for (const test of suite.allTests()) {
      this.testResults.set(test.id, {
        testCase: test,
        id: test.id,
        title: test.title,
        file: `${path.relative(process.cwd(), test.location.file)}:${test.location.line}`,
        project: test.parent.project()?.name ?? 'default',
        ...getTestEnvironment(test),
        expectedStatus: test.expectedStatus,
        actualStatus: test.expectedStatus === 'skipped' ? 'skipped' : 'notRun',
        outcome: test.expectedStatus === 'skipped' ? 'skipped' : 'notRun',
        timeout: test.timeout,
        retries: test.retries,
        tags: [...test.tags],
        annotations: test.annotations.map(annotation => ({ type: annotation.type, description: annotation.description })),
        attempts: []
      });
    }
  }

  onTestEnd(test: TestCase, result: TestResult) {
    let summary = this.testResults.get(test.id);
    if (!summary) {
      summary = {
        testCase: test,
        id: test.id,
        title: test.title,
        file: `${path.relative(process.cwd(), test.location.file)}:${test.location.line}`,
        project: test.parent.project()?.name ?? 'default',
        ...getTestEnvironment(test),
        expectedStatus: test.expectedStatus,
        actualStatus: result.status,
        outcome: test.outcome(),
        timeout: test.timeout,
        retries: test.retries,
        tags: [...test.tags],
        annotations: [],
        attempts: []
      };
      this.testResults.set(test.id, summary);
    }

    summary.actualStatus = result.status;
    summary.outcome = test.outcome();
    summary.annotations = result.annotations.map(annotation => ({ type: annotation.type, description: annotation.description }));
    summary.attempts.push({
      retry: result.retry,
      status: result.status,
      duration: result.duration,
      startedAt: result.startTime.toISOString(),
      workerIndex: result.workerIndex,
      parallelIndex: result.parallelIndex,
      errors: result.errors.map(errorDetails).filter(Boolean),
      attachments: this.copyAttachments(test.id, result.retry, result.attachments),
      stdout: result.stdout.map(chunk => String(chunk)).join(''),
      stderr: result.stderr.map(chunk => String(chunk)).join(''),
      steps: flattenSteps(result.steps)
    });
  }

  onError(error: TestError) {
    this.runErrors.push(errorDetails(error) || 'Unspecified Playwright runner error');
  }

  onStdOut(chunk: string | Buffer, test: void | TestCase) {
    if (!test) this.globalOutput.push(String(chunk));
  }

  onStdErr(chunk: string | Buffer, test: void | TestCase) {
    if (!test) this.globalOutput.push(String(chunk));
  }

  private copyAttachments(testId: string, retry: number, attachments: TestResult['attachments']): AttachmentSummary[] {
    if (!attachments.length) return [];

    return attachments.map((attachment, index) => {
      const originalPath = attachment.path;
      const extension = path.extname(originalPath ?? attachment.name);
      const baseName = safeName(path.basename(attachment.name, path.extname(attachment.name)));
      const fileName = `${safeName(testId)}-retry-${retry}-${index + 1}-${baseName}${extension}`;
      const destination = path.join(this.assetDirectory, fileName);

      try {
        if (originalPath && fs.existsSync(originalPath)) {
          fs.copyFileSync(originalPath, destination);
        } else if (attachment.body) {
          fs.writeFileSync(destination, attachment.body);
        } else {
          return { name: attachment.name, contentType: attachment.contentType };
        }
        return {
          name: attachment.name,
          contentType: attachment.contentType,
          href: `assets/${encodeURIComponent(fileName)}`,
          size: fs.statSync(destination).size
        };
      } catch {
        return { name: attachment.name, contentType: attachment.contentType };
      }
    });
  }

  private renderAttachment(attachment: AttachmentSummary) {
    const name = escapeHtml(attachment.name);
    if (!attachment.href) return `<div class="artifact unavailable"><span>${name}</span><small>Attachment file unavailable</small></div>`;

    const href = escapeHtml(attachment.href);
    const size = attachment.size === undefined ? '' : `<small>${(attachment.size / 1024).toFixed(0)} KB</small>`;
    if (attachment.contentType.startsWith('image/')) {
      return `<a class="artifact image-artifact" href="${href}" target="_blank" rel="noreferrer"><img src="${href}" alt="${name}" loading="lazy"><span>${name}</span>${size}</a>`;
    }
    if (attachment.contentType.startsWith('video/')) {
      return `<div class="artifact video-artifact"><video controls preload="metadata"><source src="${href}" type="${escapeHtml(attachment.contentType)}"></video><a href="${href}" download>${name}</a>${size}</div>`;
    }
    return `<a class="artifact file-artifact" href="${href}" download><span class="file-icon">↓</span><span>${name}</span>${size}</a>`;
  }

  private renderAttempt(attempt: AttemptSummary, index: number) {
    const status = escapeHtml(attempt.status);
    const statusClass = status.toLowerCase();
    const steps = attempt.steps.map(step => {
      const content = [
        step.subtitle ? `<small>${escapeHtml(step.subtitle)}</small>` : '',
        step.params && !step.comparison ? `<code class="step-params">${escapeHtml(step.params)}</code>` : '',
        step.comparison ? `<span class="comparison"><span><b>Expected</b><code>${escapeHtml(step.comparison.expected)}</code></span><span><b>Actual</b><code>${escapeHtml(step.comparison.actual)}</code></span></span>` : '',
        step.error ? `<pre class="step-error">${escapeHtml(step.error)}</pre>` : ''
      ].filter(Boolean).join('');

      return `<li class="step-entry" style="--depth:${step.depth}">
        <details class="step-disclosure" ${step.error ? 'open' : ''}>
          <summary class="step-row">
            <span class="step-marker ${step.error ? 'has-error' : ''}" aria-hidden="true"></span>
            <span class="step-toggle" aria-hidden="true"></span>
            <span class="step-copy"><strong>${escapeHtml(step.title)}</strong></span>
            <span class="step-category">${escapeHtml(step.category)}</span>
            <span class="step-time">${formatDuration(step.duration)}</span>
          </summary>
          <div class="step-content">${content || '<span class="quiet">No additional step details.</span>'}</div>
        </details>
      </li>`;
    }).join('');
    const artifacts = attempt.attachments.length
      ? `<div class="artifact-grid">${attempt.attachments.map(attachment => this.renderAttachment(attachment)).join('')}</div>`
      : '<p class="quiet">No attachments were produced for this attempt.</p>';
    const errors = attempt.errors.length
      ? `<div class="error-list">${attempt.errors.map(formatFailure).join('')}</div>`
      : '';
    const streams = [
      attempt.stdout ? `<details class="log-details"><summary>Standard output</summary><pre>${escapeHtml(attempt.stdout)}</pre></details>` : '',
      attempt.stderr ? `<details class="log-details"><summary>Standard error</summary><pre>${escapeHtml(attempt.stderr)}</pre></details>` : ''
    ].join('');

    return `<details class="attempt" ${statusClass !== 'passed' ? 'open' : ''}>
      <summary><span>Attempt ${index + 1}${attempt.retry ? ` · retry ${attempt.retry}` : ''}</span><span class="status-label ${statusClass}">${escapeHtml(status)}</span><span class="test-duration">${formatDuration(attempt.duration)}</span></summary>
      <div class="attempt-body">
        <div class="attempt-meta"><span>Started ${escapeHtml(new Date(attempt.startedAt).toLocaleString())}</span><span>Retry index ${attempt.retry}</span><span>Worker ${attempt.workerIndex}</span><span>Parallel slot ${attempt.parallelIndex}</span></div>
        ${errors}
        ${attempt.steps.length ? `<h3>Steps (${attempt.steps.length})</h3><ol class="steps-list">${steps}</ol>` : ''}
        <h3>Attachments (${attempt.attachments.length})</h3>${artifacts}
        ${streams ? `<h3>Worker output</h3>${streams}` : ''}
      </div>
    </details>`;
  }

  onEnd(result: FullResult) {
    const tests = [...this.testResults.values()];
    for (const test of tests) test.outcome = test.testCase.outcome();

    const total = tests.length;
    const passed = tests.filter(test => test.outcome === 'expected' && test.expectedStatus === 'passed').length;
    const failed = tests.filter(test => test.outcome === 'unexpected').length;
    const flaky = tests.filter(test => test.outcome === 'flaky').length;
    const skipped = tests.filter(test => test.expectedStatus === 'skipped' || test.actualStatus === 'skipped').length;
    const duration = (result.duration / 1000).toFixed(2);
    const runStatus = escapeHtml(result.status);
    const statusCounts = { all: total, passed, failed, flaky, skipped };

    const testMarkup = tests.map(test => {
      const actualStatus = escapeHtml(test.actualStatus);
      const expectedStatus = escapeHtml(test.expectedStatus);
      const outcome = escapeHtml(test.outcome);
      const statusClass = actualStatus.toLowerCase();
      const attemptMarkup = test.attempts.map((attempt, index) => this.renderAttempt(attempt, index)).join('');
      const annotations = test.annotations.map(annotation => `<span class="annotation"><strong>${escapeHtml(annotation.type)}</strong>${annotation.description ? ` ${escapeHtml(annotation.description)}` : ''}</span>`).join('');
      const tags = test.tags.map(tag => `<span class="tag">${escapeHtml(tag)}</span>`).join('');
      const outcomeLabel = test.outcome === 'unexpected'
        ? test.actualStatus === 'timedOut' ? 'Timed out' : test.actualStatus === 'interrupted' ? 'Interrupted' : 'Failed'
        : test.outcome === 'expected'
          ? test.expectedStatus === 'failed' ? 'Expected failure' : 'Passed'
          : test.outcome === 'flaky' ? 'Flaky' : test.outcome === 'skipped' ? 'Skipped' : test.outcome;
      const filterStatus = test.outcome === 'unexpected'
        ? 'failed'
        : test.outcome === 'flaky'
          ? 'flaky'
          : test.outcome === 'skipped' || test.actualStatus === 'skipped'
            ? 'skipped'
            : test.expectedStatus === 'passed'
              ? 'passed'
              : 'expected-failure';
      const details = test.attempts.length
        ? `<details class="test-details" ${filterStatus === 'failed' ? 'open' : ''}>
            <summary>Inspect ${test.attempts.length} ${test.attempts.length === 1 ? 'attempt' : 'attempts'} · steps, artifacts, logs</summary>
            <div class="expectation-grid">
              <div><span>Expected</span><strong>${expectedStatus}</strong></div>
              <div><span>Actual</span><strong class="${statusClass}">${actualStatus}</strong></div>
              <div><span>Outcome</span><strong class="outcome-${outcome}">${escapeHtml(outcomeLabel)}</strong></div>
              <div><span>Timeout</span><strong>${formatDuration(test.timeout)}</strong></div>
              <div><span>Retries allowed</span><strong>${test.retries}</strong></div>
            </div>
            <div class="metadata test-metadata">
              <span class="tag">ID ${escapeHtml(test.id)}</span>
              <span class="tag">Browser ${escapeHtml(test.browser)}</span>
              <span class="tag">Headless ${escapeHtml(test.headless)}</span>
              <span class="tag">Viewport ${escapeHtml(test.viewport)}</span>
              <span class="tag">Base URL ${escapeHtml(test.baseURL)}</span>
            </div>
            ${tags || annotations ? `<div class="metadata">${tags}${annotations}</div>` : ''}
            ${attemptMarkup}
          </details>`
        : `<details class="test-details"><summary>Expected ${expectedStatus} · no result recorded</summary><p class="quiet">The test was discovered but did not start before the run ended.</p></details>`;

      return `<article class="test-row" data-status="${escapeHtml(filterStatus)}" data-search="${escapeHtml(`${test.title} ${test.file} ${test.project} ${test.tags.join(' ')}`.toLowerCase())}">
        <div class="test-main">
          <span class="status-mark ${statusClass}" aria-hidden="true"></span>
          <div class="test-copy"><h2>${escapeHtml(test.title)}</h2><p>${escapeHtml(test.file)}</p></div>
          <span class="project-label">${escapeHtml(test.project)}</span>
          <span class="test-duration">${test.attempts.length ? formatDuration(test.attempts.reduce((sum, attempt) => sum + attempt.duration, 0)) : '—'}</span>
          <span class="status-label outcome-${escapeHtml(outcome)}">${escapeHtml(outcomeLabel)}</span>
        </div>
        ${details}
      </article>`;
    }).join('');

    const runErrors = this.runErrors.length
      ? `<section class="run-errors"><h2>Runner errors</h2>${this.runErrors.map(error => `<pre class="error-box">${escapeHtml(error)}</pre>`).join('')}</section>`
      : '';
    const globalOutput = this.globalOutput.length
      ? `<details class="global-output"><summary>Unassigned runner output (${this.globalOutput.length} chunks)</summary><pre>${escapeHtml(this.globalOutput.join(''))}</pre></details>`
      : '';

    const htmlContent = `
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta name="color-scheme" content="light dark">
  <title>Playwright run | Execution report</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500&display=swap" rel="stylesheet">
  <style>
    :root { color-scheme:light; --paper:#f4f6fc; --surface:#fff; --surface-subtle:#f8f9ff; --ink:#20243a; --muted:#727992; --line:#e2e5f0; --line-soft:#edf0f7; --green:#16835f; --green-soft:#e5f6ee; --red:#d34b60; --red-soft:#fff0f1; --amber:#ad6c12; --amber-soft:#fff4df; --blue:#3978d5; --purple:#6657d9; --glow-one:#e9eaff; --glow-two:#e1f5f3; --annotation-bg:#fff4df; --annotation-ink:#76500e; --shadow:0 14px 38px #303b6812; --mono:'IBM Plex Mono',monospace; --sans:'DM Sans',sans-serif; }
    * { box-sizing:border-box; }
    body { margin:0; background:radial-gradient(ellipse at 12% 0%,var(--glow-one) 0,transparent 34%),radial-gradient(ellipse at 92% 8%,var(--glow-two) 0,transparent 29%),var(--paper); color:var(--ink); font-family:var(--sans); line-height:1.5; }
    .topbar { height:6px; background:linear-gradient(90deg,#6657d9,#547fe1 38%,#23a58d 70%,#f0ad4e); }
    .page { width:min(1160px,calc(100% - 48px)); margin:0 auto; padding:38px 0 72px; }
    :root[data-theme="dark"] { color-scheme:dark; --paper:#111522; --surface:#191e2e; --surface-subtle:#20263a; --ink:#f1f3fc; --muted:#a1a9c0; --line:#30374d; --line-soft:#282f43; --green:#53d4a0; --green-soft:#173a34; --red:#ff8190; --red-soft:#3c252f; --amber:#f3c66e; --amber-soft:#3d3423; --blue:#82adff; --purple:#a89dff; --glow-one:#22223c; --glow-two:#183031; --annotation-bg:#40351e; --annotation-ink:#f2d693; --shadow:0 16px 42px #0004; }
    @media (prefers-color-scheme:dark) { :root:not([data-theme="light"]) { color-scheme:dark; --paper:#111522; --surface:#191e2e; --surface-subtle:#20263a; --ink:#f1f3fc; --muted:#a1a9c0; --line:#30374d; --line-soft:#282f43; --green:#53d4a0; --green-soft:#173a34; --red:#ff8190; --red-soft:#3c252f; --amber:#f3c66e; --amber-soft:#3d3423; --blue:#82adff; --purple:#a89dff; --glow-one:#22223c; --glow-two:#183031; --annotation-bg:#40351e; --annotation-ink:#f2d693; --shadow:0 16px 42px #0004; } }
    .masthead { display:flex; justify-content:space-between; align-items:flex-start; gap:24px; margin-bottom:24px; padding:26px 30px; border:1px solid #ffffffb8; border-radius:20px; background:linear-gradient(120deg,#ffffffed,#f5f5ffed 56%,#edfbf8ed); box-shadow:var(--shadow); }
    :root[data-theme="dark"] .masthead { border-color:var(--line); background:linear-gradient(120deg,#191e2e,#20243b 56%,#192d34); }
    @media (prefers-color-scheme:dark) { :root:not([data-theme="light"]) .masthead { border-color:var(--line); background:linear-gradient(120deg,#191e2e,#20243b 56%,#192d34); } }
    .eyebrow { margin:0 0 8px; color:var(--purple); font:600 11px var(--mono); letter-spacing:.09em; text-transform:uppercase; }
    h1 { margin:0; font-size:34px; letter-spacing:-.04em; line-height:1.15; }
    .subtitle { margin:8px 0 0; color:var(--muted); font-size:13px; }
    .run-meta { margin-top:10px; color:var(--muted); font:10px var(--mono); }
    .run-state { display:inline-flex; align-items:center; gap:9px; padding:8px 12px; border:1px solid #b9ead1; border-radius:999px; background:var(--green-soft); color:var(--green); font:600 11px var(--mono); text-transform:uppercase; }
    .run-state::before { width:8px; height:8px; border-radius:50%; background:currentColor; box-shadow:0 0 0 3px #16835f20; content:''; }
    .run-state.failed,.run-state.timedout,.run-state.interrupted { border-color:#f2c2ca; background:var(--red-soft); color:var(--red); }
    .run-state.failed::before,.run-state.timedout::before,.run-state.interrupted::before { background:currentColor; box-shadow:0 0 0 3px #d34b6020; }
    .header-tools { display:flex; align-items:center; gap:10px; }
    .theme-select { height:36px; padding:0 11px; border:1px solid var(--line); border-radius:9px; background:var(--surface); color:var(--ink); font:11px var(--sans); }
    .overview { display:grid; grid-template-columns:repeat(5,minmax(0,1fr)); border:1px solid var(--line); border-radius:16px; background:var(--surface); box-shadow:var(--shadow); overflow:hidden; }
    .metric { position:relative; padding:18px 20px; border-right:1px solid var(--line); }
    .metric::before { position:absolute; inset:0 0 auto; height:3px; background:var(--purple); content:''; }
    .metric:nth-child(2)::before { background:var(--green); }
    .metric:nth-child(3)::before { background:var(--red); }
    .metric:nth-child(4)::before { background:#e5a533; }
    .metric:nth-child(5)::before { background:var(--blue); }
    .metric:last-child { border-right:0; }
    .metric-label { display:block; color:var(--muted); font-size:11px; font-weight:600; }
    .metric-value { display:block; margin-top:5px; font-size:29px; letter-spacing:-.04em; line-height:1.2; }
    .metric-value.passed,.status-label.outcome-expected { color:var(--green); }
    .metric-value.failed,.status-label.outcome-unexpected { color:var(--red); }
    .metric-value.flaky,.status-label.outcome-flaky { color:var(--amber); }
    .metric-value.duration { padding-top:4px; font:500 18px var(--mono); }
    .pass-track { height:5px; margin-top:12px; border-radius:99px; background:var(--line-soft); overflow:hidden; }
    .pass-track span { display:block; height:100%; width:${total ? Math.round((passed / total) * 100) : 0}%; border-radius:inherit; background:linear-gradient(90deg,#18a778,#56c59c); }
    .config-line { display:flex; flex-wrap:wrap; gap:8px; padding:14px 2px 0; color:var(--muted); font:10px var(--mono); }
    .config-line span { padding:5px 8px; border:1px solid var(--line); border-radius:7px; background:var(--surface); }
    .results-head { display:flex; justify-content:space-between; align-items:end; gap:20px; margin:38px 0 14px; }
    .results-head h2,.run-errors h2 { margin:0; font-size:21px; letter-spacing:-.025em; }
    .results-count { display:block; margin-top:3px; color:var(--muted); font-size:12px; }
    .search { width:min(300px,100%); height:40px; padding:0 13px; border:1px solid var(--line); border-radius:10px; background:var(--surface); color:var(--ink); font:13px var(--sans); outline:none; }
    .search:focus { border-color:var(--purple); box-shadow:0 0 0 3px #6657d926; }
    :root[data-theme="dark"] .search:focus { box-shadow:0 0 0 3px #a89dff30; }
    .filters { display:flex; flex-wrap:wrap; gap:8px; margin-bottom:14px; }
    .filter { padding:7px 11px; border:1px solid transparent; border-radius:9px; background:transparent; color:var(--muted); font:600 12px var(--sans); cursor:pointer; transition:background .18s ease,color .18s ease,border-color .18s ease; }
    .filter span { margin-left:4px; opacity:.7; font:10px var(--mono); }
    .filter:hover { color:var(--purple); background:#eeedff; }
    .filter.active { border-color:#d9d5ff; background:#eeedff; color:#5143c4; }
    :root[data-theme="dark"] .filter:hover,:root[data-theme="dark"] .filter.active { border-color:#48416f; background:#302c4b; color:var(--purple); }
    @media (prefers-color-scheme:dark) { :root:not([data-theme="light"]) .filter:hover,:root:not([data-theme="light"]) .filter.active { border-color:#48416f; background:#302c4b; color:var(--purple); } }
    .test-list { display:grid; gap:9px; }
    .test-row { border:1px solid var(--line); border-radius:12px; background:var(--surface); box-shadow:0 3px 12px #303b6808; animation:reveal .25s ease both; transition:border-color .18s ease,box-shadow .18s ease,transform .18s ease; }
    .test-row:hover { border-color:#c9c6f2; box-shadow:var(--shadow); transform:translateY(-1px); }
    :root[data-theme="dark"] .test-row:hover { border-color:#514a7a; }
    @media (prefers-color-scheme:dark) { :root:not([data-theme="light"]) .test-row:hover { border-color:#514a7a; } }
    .test-row[hidden] { display:none; }
    .test-main { min-height:76px; display:grid; grid-template-columns:12px minmax(0,1fr) auto auto auto; align-items:center; gap:13px; padding:14px 15px; }
    .status-mark { width:10px; height:10px; border-radius:50%; background:var(--green); box-shadow:0 0 0 4px var(--green-soft); }
    .status-mark.failed,.status-mark.timedout,.status-mark.interrupted { background:var(--red); box-shadow:0 0 0 4px var(--red-soft); }
    .status-mark.flaky { background:#e5a533; box-shadow:0 0 0 4px var(--amber-soft); }
    .status-mark.skipped { background:#9098ad; box-shadow:0 0 0 4px #9098ad20; }
    .test-copy { min-width:0; }
    .test-copy h2 { margin:0; overflow-wrap:anywhere; font-size:14px; font-weight:600; }
    .test-copy p { margin:3px 0 0; color:var(--muted); font:10px var(--mono); }
    .project-label,.tag { padding:4px 8px; border:1px solid #dce3f1; border-radius:6px; background:var(--surface-subtle); color:var(--blue); font:10px var(--mono); }
    .test-duration { color:var(--muted); font:11px var(--mono); white-space:nowrap; }
    .status-label { min-width:80px; text-align:right; font-size:10px; font-weight:700; text-transform:uppercase; }
    .test-main .status-label { padding:5px 8px; border-radius:999px; background:var(--green-soft); }
    .status-label.outcome-unexpected,.status-label.failed,.status-label.timedout,.status-label.interrupted { color:var(--red); }
    .test-main .status-label.outcome-unexpected { background:var(--red-soft); }
    .test-main .status-label.outcome-flaky { background:var(--amber-soft); }
    .status-label.outcome-skipped { color:var(--muted); }
    .test-main .status-label.outcome-skipped { background:var(--surface-subtle); }
    .test-details { margin:0 15px 15px 40px; padding-top:11px; border-top:1px solid var(--line-soft); color:var(--muted); }
    .test-details>summary { width:fit-content; color:var(--purple); font-size:12px; font-weight:600; cursor:pointer; }
    .expectation-grid { display:grid; grid-template-columns:repeat(5,minmax(90px,1fr)); margin:14px 0; border:1px solid var(--line); border-radius:10px; background:var(--surface-subtle); overflow:hidden; }
    .expectation-grid>div { padding:10px 12px; border-right:1px solid var(--line); }
    .expectation-grid>div:last-child { border:0; }
    .expectation-grid span,.expectation-grid strong { display:block; }
    .expectation-grid span { color:var(--muted); font-size:10px; }
    .expectation-grid strong { margin-top:3px; color:var(--ink); font:500 11px var(--mono); overflow-wrap:anywhere; }
    .expectation-grid strong.failed,.expectation-grid strong.timedout,.expectation-grid strong.interrupted { color:var(--red); }
    .metadata { display:flex; flex-wrap:wrap; align-items:center; gap:6px; margin:12px 0; }
    .annotation { padding:4px 8px; border-left:2px solid #d58c34; background:var(--annotation-bg); color:var(--annotation-ink); font-size:11px; }
      .failure-detail { margin:10px 0 14px; border:1px solid #e6a6a1; border-left:4px solid var(--red); border-radius:5px; background:var(--red-soft); overflow:hidden; }
      .failure-heading { display:flex; align-items:center; gap:10px; padding:12px; color:var(--red); }
      .failure-heading>div { min-width:0; }
      .failure-heading strong { display:block; overflow-wrap:anywhere; font-size:12px; }
      .failure-heading small { display:block; margin-top:2px; color:var(--muted); font-size:10px; }
      .failure-icon { display:grid; width:22px; height:22px; flex:none; place-items:center; border-radius:50%; background:var(--red); color:#fff; font-weight:700; }
      .failure-comparison { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:8px; padding:0 12px 12px; }
      .failure-comparison>div { min-width:0; padding:9px; border:1px solid #e6a6a1; border-radius:4px; background:var(--surface); }
      .failure-comparison b { display:block; margin-bottom:4px; color:var(--red); font-size:9px; text-transform:uppercase; }
      .failure-comparison code { display:block; color:var(--ink); font:11px/1.5 var(--mono); white-space:pre-wrap; overflow-wrap:anywhere; }
      .failure-more { border-top:1px solid #e6a6a1; }
      .failure-more summary { padding:8px 12px; color:var(--red); font-size:10px; font-weight:600; cursor:pointer; }
      .failure-more pre { max-height:520px; margin:0; padding:12px; overflow:auto; border-top:1px solid #e6a6a1; color:var(--ink); font:10px/1.6 var(--mono); white-space:pre-wrap; overflow-wrap:anywhere; }
      .failure-line { display:block; margin:0 -4px; padding:1px 4px; border-radius:2px; background:#ffd9d6; color:#7d1813; font-weight:700; }
      :root[data-theme="dark"] .failure-line { background:#662d29; color:#fff; }
      .config-line { display:flex; flex-wrap:wrap; gap:7px 16px; padding:12px 2px 0; color:var(--muted); font:10px var(--mono); }
    .attempt { margin:12px 0; border:1px solid var(--line); border-radius:10px; background:var(--surface); overflow:hidden; }
    .attempt>summary { display:flex; align-items:center; gap:15px; padding:10px 12px; cursor:pointer; list-style-position:inside; font-size:12px; font-weight:600; }
    .attempt>summary .status-label { margin-left:auto; }
    .attempt-body { padding:0 14px 14px; }
    .attempt-meta { display:flex; flex-wrap:wrap; gap:8px 16px; color:var(--muted); font:10px var(--mono); }
    .attempt-body h3 { margin:16px 0 8px; font-size:12px; }
    .steps-list { margin:8px 0 0; padding:0; list-style:none; border-top:1px solid var(--line); }
    .step-entry { margin-left:calc(var(--depth) * 14px); border-bottom:1px solid var(--line-soft); }
    .step-disclosure>summary { cursor:pointer; list-style:none; }
    .step-disclosure>summary::-webkit-details-marker { display:none; }
    .step-row { min-height:40px; display:flex; align-items:center; gap:9px; padding:7px 8px; font-size:11px; }
    .step-toggle { display:grid; width:22px; height:22px; flex:none; place-items:center; border:1px solid var(--line); border-radius:6px; background:var(--surface-subtle); color:var(--purple); font:500 15px/1 var(--mono); }
    .step-toggle::before { content:'+'; }
    .step-disclosure[open] .step-toggle { border-color:var(--red); background:var(--red-soft); color:var(--red); }
    .step-disclosure[open] .step-toggle::before { content:'−'; }
    .step-marker { width:6px; height:6px; border:1px solid #8aa395; border-radius:50%; }
    .step-marker.has-error { border-color:var(--red); background:var(--red); }
    .step-copy { min-width:0; flex:1; color:var(--ink); overflow-wrap:anywhere; }
    .step-copy strong { display:block; font-weight:500; }
    .step-copy small,.step-copy code { display:block; margin-top:2px; color:var(--muted); font:10px var(--mono); overflow-wrap:anywhere; }
    .step-content { padding:0 12px 12px 34px; color:var(--muted); font-size:11px; }
    .step-content>small { display:block; margin-bottom:6px; font:10px var(--mono); }
    .step-content>.step-params { padding:6px 8px; border:1px solid var(--line); border-radius:4px; background:var(--surface-subtle); white-space:pre-wrap; }
    .comparison { display:flex; flex-wrap:wrap; gap:6px; margin-top:6px; }
    .comparison>span { min-width:140px; padding:6px 8px; border:1px solid var(--line); border-radius:4px; background:var(--surface-subtle); }
    .comparison b { display:block; margin-bottom:2px; color:var(--muted); font:600 9px var(--sans); text-transform:uppercase; }
    .comparison code { margin:0; color:var(--ink); white-space:pre-wrap; }
    .step-category,.step-time { color:var(--muted); font:10px var(--mono); white-space:nowrap; }
    .step-error { margin:4px 0; color:var(--red); white-space:pre-wrap; }
    .artifact-grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(180px,1fr)); gap:10px; margin-top:8px; }
    .artifact { min-width:0; display:flex; flex-direction:column; gap:5px; padding:8px; border:1px solid var(--line); border-radius:5px; background:var(--surface-subtle); color:var(--ink); text-decoration:none; font-size:11px; overflow-wrap:anywhere; }
    .artifact small { color:var(--muted); font:9px var(--mono); }
    .image-artifact img { width:100%; height:130px; object-fit:cover; border:1px solid var(--line); background:#edf0ed; }
    .video-artifact video { display:block; width:100%; max-height:220px; background:#17231f; }
    .file-artifact { flex-direction:row; align-items:center; }
    .file-icon { display:grid; width:25px; aspect-ratio:1; place-items:center; border-radius:4px; background:#e4f3e9; color:var(--green); font-size:17px; }
    .unavailable { color:var(--muted); }
    .error-box,.log-details pre,.global-output pre { max-height:360px; margin:8px 0; padding:12px; overflow:auto; border-left:3px solid var(--red); background:var(--red-soft); color:var(--ink); font:10px/1.55 var(--mono); white-space:pre-wrap; overflow-wrap:anywhere; }
    .log-details,.global-output { margin:8px 0; }
    .log-details summary,.global-output summary { color:var(--blue); font-size:11px; font-weight:600; cursor:pointer; }
    .quiet,.empty-state { padding:12px 0; color:var(--muted); font-size:12px; }
    .run-errors { margin:18px 0; }
    @keyframes reveal { from { opacity:0; transform:translateY(4px); } to { opacity:1; transform:translateY(0); } }
    @media (prefers-reduced-motion:reduce) { *,*::before,*::after { animation-duration:.01ms!important; scroll-behavior:auto!important; } }
    @media (max-width:760px) {
      .page { width:min(100% - 32px,560px); padding-top:22px; }
      .masthead { flex-direction:column; gap:14px; padding:21px; }
      h1 { font-size:28px; }
      .header-tools { width:100%; justify-content:space-between; }
      .overview { grid-template-columns:repeat(2,minmax(0,1fr)); }
      .metric { padding:15px; border-bottom:1px solid var(--line); }
      .metric:nth-child(2n) { border-right:0; }
      .metric:last-child { border-bottom:0; }
      .results-head { align-items:stretch; flex-direction:column; gap:12px; margin-top:28px; }
      .search { width:100%; }
      .test-main { grid-template-columns:10px minmax(0,1fr) auto; gap:9px; padding:13px; }
      .project-label { grid-column:2; grid-row:2; width:fit-content; }
      .test-duration { grid-column:3; grid-row:1; }
      .status-label { grid-column:3; grid-row:2; min-width:auto; }
      .test-details { margin-left:16px; }
      .expectation-grid { grid-template-columns:repeat(2,minmax(0,1fr)); }
      .expectation-grid>div:nth-child(2n) { border-right:0; }
      .expectation-grid>div:last-child { border-right:0; }
      .step-row { align-items:flex-start; }
      .step-category { display:none; }
    }
  </style>
</head>
<body>
  <div class="topbar"></div>
  <main class="page">
    <header class="masthead">
      <div>
        <p class="eyebrow">Playwright / Run report</p>
        <h1>Test execution</h1>
        <p class="subtitle">Outcomes, retries, step data, logs, and captured artifacts.</p>
        <p class="run-meta">Started ${escapeHtml(this.runStartedAt.toLocaleString())} · Run ${escapeHtml(this.runId)}</p>
      </div>
      <div class="header-tools">
        <select class="theme-select" id="theme" aria-label="Report theme">
          <option value="system">Default</option>
          <option value="light">Light</option>
          <option value="dark">Dark</option>
        </select>
        <span class="run-state ${runStatus.toLowerCase()}">${runStatus}</span>
      </div>
    </header>

    <section class="overview" aria-label="Run summary">
      <div class="metric"><span class="metric-label">Tests</span><strong class="metric-value">${total}</strong><div class="pass-track" aria-label="${total ? Math.round((passed / total) * 100) : 0}% passed"><span></span></div></div>
      <div class="metric"><span class="metric-label">Passed as expected</span><strong class="metric-value passed">${passed}</strong></div>
      <div class="metric"><span class="metric-label">Failed</span><strong class="metric-value failed">${failed}</strong></div>
      <div class="metric"><span class="metric-label">Flaky</span><strong class="metric-value flaky">${flaky}</strong></div>
      <div class="metric"><span class="metric-label">Duration</span><strong class="metric-value duration">${duration}s</strong></div>
    </section>
    <div class="config-line"><span>${tests.length - skipped} executed · ${skipped} skipped · ${tests.filter(test => test.attempts.length === 0 && test.expectedStatus !== 'skipped').length} not started</span><span>Workers ${this.settings.workers}</span><span>Retries ${this.settings.retries}</span><span>Timeout ${formatDuration(this.settings.timeout)}</span><span>${this.settings.fullyParallel ? 'Fully parallel' : 'Serial scheduling'}</span><span>Node ${escapeHtml(this.settings.nodeVersion)}</span><span>${escapeHtml(this.settings.platform)} / ${escapeHtml(this.settings.architecture)}</span><span>CI ${this.settings.ci ? 'yes' : 'no'}</span><span>Projects: ${escapeHtml(this.settings.projects.map(project => `${project.name} (${project.browser})`).join(', ') || 'none')}</span></div>

    ${runErrors}
    <section aria-labelledby="results-title">
      <div class="results-head">
        <div><h2 id="results-title">Test results</h2><span class="results-count">${total} ${total === 1 ? 'test' : 'tests'} · ${skipped} skipped · ${flaky} flaky</span></div>
        <input class="search" id="search" type="search" placeholder="Search tests, files, tags" aria-label="Search tests, files, and tags">
      </div>
      <nav class="filters" aria-label="Filter tests">
        <button class="filter active" data-filter="all" type="button">All <span>${statusCounts.all}</span></button>
        <button class="filter" data-filter="passed" type="button">Passed <span>${statusCounts.passed}</span></button>
        <button class="filter" data-filter="failed" type="button">Failed <span>${statusCounts.failed}</span></button>
        <button class="filter" data-filter="flaky" type="button">Flaky <span>${statusCounts.flaky}</span></button>
        <button class="filter" data-filter="skipped" type="button">Skipped <span>${statusCounts.skipped}</span></button>
      </nav>
      <div class="test-list" id="test-list">${testMarkup || '<p class="empty-state">No tests were discovered for this run.</p>'}</div>
      <p class="empty-state" id="no-matches" hidden>No tests match the current filters.</p>
      ${globalOutput}
    </section>
  </main>
  <script>
    const search = document.querySelector('#search');
    const theme = document.querySelector('#theme');
    const rows = [...document.querySelectorAll('.test-row')];
    const filters = [...document.querySelectorAll('.filter')];
    const noMatches = document.querySelector('#no-matches');
    let activeFilter = 'all';
    try { theme.value = localStorage.getItem('playwright-report-theme') || 'system'; } catch {}
    function applyTheme(value) {
      if (value === 'system') delete document.documentElement.dataset.theme;
      else document.documentElement.dataset.theme = value;
      try { localStorage.setItem('playwright-report-theme', value); } catch {}
    }
    applyTheme(theme.value);
    theme.addEventListener('change', () => applyTheme(theme.value));
    function updateRows() {
      const query = search.value.trim().toLowerCase();
      let visible = 0;
      rows.forEach(row => {
        const matchesFilter = activeFilter === 'all' || row.dataset.status === activeFilter;
        row.hidden = !(matchesFilter && row.dataset.search.includes(query));
        if (!row.hidden) visible++;
      });
      noMatches.hidden = visible > 0 || rows.length === 0;
    }
    search.addEventListener('input', updateRows);
    filters.forEach(button => button.addEventListener('click', () => {
      activeFilter = button.dataset.filter;
      filters.forEach(filter => filter.classList.toggle('active', filter === button));
      updateRows();
    }));
  </script>
</body>
</html>`;

    const reportPath = path.join(this.reportDirectory, 'results.html');
    const jsonPath = path.join(this.reportDirectory, 'results.json');
    const jsonResults = {
      run: { startedAt: this.runStartedAt.toISOString(), status: result.status, duration: result.duration, settings: this.settings },
      tests: tests.map(test => ({
        title: test.title,
        id: test.id,
        file: test.file,
        project: test.project,
        browser: test.browser,
        baseURL: test.baseURL,
        headless: test.headless,
        viewport: test.viewport,
        expectedStatus: test.expectedStatus,
        actualStatus: test.actualStatus,
        outcome: test.outcome === 'unexpected'
          ? test.actualStatus === 'timedOut' ? 'timedOut' : test.actualStatus === 'interrupted' ? 'interrupted' : 'failed'
          : test.outcome === 'expected' && test.expectedStatus === 'passed' ? 'passed' : test.outcome,
        playwrightOutcome: test.outcome,
        timeout: test.timeout,
        retriesAllowed: test.retries,
        tags: test.tags,
        annotations: test.annotations,
        attempts: test.attempts
      }))
    };
    fs.writeFileSync(reportPath, htmlContent, 'utf8');
    fs.writeFileSync(jsonPath, JSON.stringify(jsonResults, null, 2), 'utf8');
    console.log(`\nCustom report generated: ${reportPath}`);
    console.log(`Playwright report generated: ${path.join(this.reportDirectory, 'playwright-report', 'index.html')}`);
    console.log(`Structured results generated: ${jsonPath}\n`);
  }
}