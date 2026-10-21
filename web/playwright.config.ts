import { defineConfig, devices } from '@playwright/test';

// Browser tests against the fake-engine server (tests/e2e_server.py): no models, no network. A second server runs
// with the LLM enabled against the local fake model (--fake-llm); specs that need it use LLM_BASE_URL.
const PORT = Number(process.env.E2E_PORT ?? 8799);
const LLM_PORT = Number(process.env.E2E_LLM_PORT ?? 8798);

export default defineConfig({
  testDir: './e2e',
  testIgnore: ['**/ocbc.spec.ts'],
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 60_000,
  reporter: [['list']],
  use: {
    baseURL: `http://127.0.0.1:${PORT}/ui/`,
    trace: 'off',
    screenshot: 'off',
    viewport: { width: 1440, height: 940 },
    // a locally installed Chromium when the one of this Playwright version is not downloaded (CI installs it)
    launchOptions: process.env.PW_CHROMIUM_PATH ? { executablePath: process.env.PW_CHROMIUM_PATH } : {},
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 940 } } }],
  webServer: [
    {
      command: `uv run python tests/e2e_server.py --port ${PORT}`,
      cwd: '..',
      url: `http://127.0.0.1:${PORT}/v1/health`,
      reuseExistingServer: !process.env.CI,
      timeout: 180_000,
      stdout: 'pipe',
    },
    {
      command: `uv run python tests/e2e_server.py --port ${LLM_PORT} --fake-llm --fixtures web/e2e/.fixtures-llm`,
      cwd: '..',
      url: `http://127.0.0.1:${LLM_PORT}/v1/health`,
      reuseExistingServer: !process.env.CI,
      timeout: 180_000,
      stdout: 'pipe',
    },
  ],
});
