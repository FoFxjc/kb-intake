import { defineConfig, devices } from '@playwright/test';

// The OCBC walk-through (intake-ui-llm-design-v1 §6 "UI"): a real `kb-intake serve` with the pinned models, started by
// hand (OCBC_URL, default http://127.0.0.1:8731), and the OCBC 2025 annual report (OCBC_PDF). Not run in CI.
//   OCBC_URL=... OCBC_PDF=... [OCBC_LLM=1] E2E_SHOTS=dir npx playwright test --config e2e/ocbc.config.ts
export default defineConfig({
  testDir: '.',
  testMatch: ['ocbc.spec.ts'],
  workers: 1,
  retries: 0,
  timeout: 20 * 60_000,
  expect: { timeout: 30_000 },
  reporter: [['list']],
  use: {
    baseURL: `${process.env.OCBC_URL ?? 'http://127.0.0.1:8731'}/ui/`,
    trace: 'off',
    viewport: { width: 1440, height: 940 },
    launchOptions: process.env.PW_CHROMIUM_PATH ? { executablePath: process.env.PW_CHROMIUM_PATH } : {},
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 940 } } }],
});
