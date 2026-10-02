import { defineConfig, devices } from '@playwright/test';

const proxyServer = process.env.PLAYRUNNER_PROXY_SERVER || process.env.PLAYRUN_PROXY_SERVER || process.env.DASHBOARD_PROXY_SERVER;
const configuredBaseUrl = process.env.PLAYRUNNER_BASE_URL || process.env.PLAYRUN_BASE_URL;
const configuredHeadless = process.env.PLAYRUNNER_HEADLESS ?? process.env.PLAYRUN_HEADLESS;

const config = ({
  testDir: './tests',
  timeout: 30 * 1000,
  expect: {
    timeout: 5000
  },
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: [
    ['./fixtures/html-reporter.ts',
    ],
    ['html', { open: 'never' }]
  ],
  use: {
    actionTimeout: 0,
    baseURL: configuredBaseUrl || 'http://rahulshettyacademy.com/',
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
    headless: configuredHeadless === undefined ? Boolean(process.env.CI) : configuredHeadless === 'true',
    ...(proxyServer ? { proxy: { server: proxyServer } } : {}),
  },

  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    }
  ],
});

module.exports = config;