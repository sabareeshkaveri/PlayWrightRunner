// custom-reporter.ts
import { Reporter, TestCase, TestResult, FullResult, FullConfig } from '@playwright/test/reporter';

export default class CustomConsoleReporter implements Reporter {
  private startTime: number = 0;
  private passed = 0;
  private failed = 0;
  private skipped = 0;
  private timedOut = 0;

  // ANSI Color Helpers
  private reset = '\x1b[0m';
  private bold = '\x1b[1m';
  private dim = '\x1b[2m';
  private green = '\x1b[32m';
  private red = '\x1b[31m';
  private yellow = '\x1b[33m';
  private cyan = '\x1b[36m';
  private bgRed = '\x1b[41m';
  private bgGreen = '\x1b[42m';

  onBegin(config: FullConfig, suite: any) {
    this.startTime = Date.now();
    const totalTests = suite.allTests().length;

    console.log(`\n${this.bold}${this.cyan}🚀 PLAYWRIGHT TEST SUITE STARTED${this.reset}`);
    console.log(`${this.dim}--------------------------------------------------${this.reset}`);
    console.log(`📦 Total Specs Queued: ${this.bold}${totalTests}${this.reset}\n`);
  }

  onTestEnd(test: TestCase, result: TestResult) {
    const duration = `${(result.duration / 1000).toFixed(2)}s`;
    const titlePath = test.titlePath().slice(1).join(' > ');

    switch (result.status) {
      case 'passed':
        this.passed++;
        console.log(` ${this.green}✔ PASS${this.reset}  ${titlePath} ${this.dim}(${duration})${this.reset}`);
        break;

      case 'failed':
        this.failed++;
        console.log(` ${this.red}✖ FAIL${this.reset}  ${titlePath} ${this.dim}(${duration})${this.reset}`);
        this.logFailureDetails(result);
        break;

      case 'timedOut':
        this.timedOut++;
        console.log(` ${this.yellow}⏳ TIMEOUT${this.reset} ${titlePath} ${this.dim}(${duration})${this.reset}`);
        this.logFailureDetails(result);
        break;

      case 'skipped':
        this.skipped++;
        console.log(` ${this.yellow}⚪ SKIP${this.reset}  ${titlePath}`);
        break;
    }
  }

  private logFailureDetails(result: TestResult) {
    if (result.error) {
      console.log(`   ${this.red}┌─ Error Details:${this.reset}`);
      const cleanMessage = result.error.message?.replace(/\x1b\[[0-9;]*m/g, '') || 'Unknown error';
      cleanMessage.split('\n').slice(0, 5).forEach((line) => {
        console.log(`   ${this.red}│${this.reset} ${line}`);
      });
      console.log(`   ${this.red}└────────────────${this.reset}\n`);
    }
  }

  onEnd(result: FullResult) {
    const totalDuration = ((Date.now() - this.startTime) / 1000).toFixed(2);
    const total = this.passed + this.failed + this.skipped + this.timedOut;

    console.log(`\n${this.dim}--------------------------------------------------${this.reset}`);
    console.log(`${this.bold}${this.cyan}📊 TEST EXECUTION SUMMARY${this.reset}`);
    console.log(`${this.dim}--------------------------------------------------${this.reset}`);
    
    console.log(` Total Tests: ${this.bold}${total}${this.reset}`);
    console.log(` ${this.green}✔ Passed:     ${this.passed}${this.reset}`);
    console.log(` ${this.red}✖ Failed:     ${this.failed}${this.reset}`);
    console.log(` ${this.yellow}⏳ Timed Out:  ${this.timedOut}${this.reset}`);
    console.log(` ${this.yellow}⚪ Skipped:    ${this.skipped}${this.reset}`);
    console.log(` ⏱  Duration:   ${totalDuration}s`);

    const badge = result.status === 'passed' 
      ? `${this.bgGreen}${this.bold} PASS ${this.reset}` 
      : `${this.bgRed}${this.bold} FAIL ${this.reset}`;

    console.log(`\n Result: ${badge}\n`);
  }
}