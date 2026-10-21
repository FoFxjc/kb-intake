import { expect, test as base, type APIRequestContext } from '@playwright/test';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';

// Every test checks that the page logged no console error and asked no host other than the server.
interface Guard {
  consoleErrors: string[];
  foreignRequests: string[];
  /** console errors a test provokes on purpose (e.g. the browser's own line for a 409 answer) */
  allowConsole: (pattern: RegExp) => void;
}

export const test = base.extend<{ guard: Guard }>({
  guard: [
    async ({ page }, use) => {
      const allowed: RegExp[] = [];
      const guard: Guard = { consoleErrors: [], foreignRequests: [], allowConsole: (p) => allowed.push(p) };
      page.on('console', (m) => {
        if (m.type() === 'error' && !allowed.some((p) => p.test(m.text()))) guard.consoleErrors.push(m.text());
      });
      page.on('pageerror', (e) => guard.consoleErrors.push(`pageerror: ${e.message}`));
      page.on('request', (r) => {
        const u = new URL(r.url());
        if (u.protocol === 'data:' || u.protocol === 'blob:') return;
        if (u.hostname !== '127.0.0.1') guard.foreignRequests.push(r.url());
      });
      await use(guard);
      expect(guard.consoleErrors, 'console errors').toEqual([]);
      expect(guard.foreignRequests, 'requests to other hosts').toEqual([]);
    },
    { auto: true },
  ],
});

export { expect };

/** The job tests/e2e_server.py seeded (in_review, source demo.pdf). */
export async function seededJob(request: APIRequestContext): Promise<string> {
  const r = await request.get('/v1/jobs');
  expect(r.ok()).toBeTruthy();
  const { jobs } = (await r.json()) as { jobs: { job_id: string; status: string; source_name: string }[] };
  const job = jobs.find((j) => j.source_name === 'demo.pdf' && j.status === 'in_review');
  expect(job, 'the seeded job').toBeTruthy();
  return job!.job_id;
}

/** Saves a screenshot when E2E_SHOTS names a directory (kept out of the repository). */
export async function shot(page: import('@playwright/test').Page, name: string) {
  const dir = process.env.E2E_SHOTS;
  if (!dir) return;
  mkdirSync(dir, { recursive: true });
  await page.screenshot({ path: join(dir, `${name}.png`), fullPage: true });
}
