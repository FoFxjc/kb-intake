import type { Page, Route } from '@playwright/test';
import { expect, seededJob, test } from './fixtures';
import { approvedJob, waitStatus } from './flow';

// What the pages do when a request fails or the server answers something unexpected: no request storm, no polling
// that stops for good, no action offered that the server must refuse (review findings of the U tasks).

const rows = (page: Page) => page.getByTestId('region-row');
const FAILED_LOAD = /Failed to load resource|ERR_CONNECTION_REFUSED/;

/** Answers GET /v1/jobs/{id} with the job as `patch` makes it. */
async function fulfillJob(route: Route, patch: (job: Record<string, unknown>) => void) {
  const r = await route.fetch();
  const body = (await r.json()) as { job: Record<string, unknown> };
  patch(body.job);
  await route.fulfill({ response: r, json: body });
}

const extracting = (job: Record<string, unknown>) => {
  job.status = 'approved';
  job.running_op = 'extract';
  job.last_error = null;
};

test.describe('region list paging', () => {
  test('a failed next page stops loading more; the list shows the error and Retry loads the page', async ({ page, request, guard }) => {
    guard.allowConsole(FAILED_LOAD);
    const jobId = await seededJob(request);
    let more = 0;
    let fail = true;
    await page.route(/\/regions\?/, async (route) => {
      const url = new URL(route.request().url());
      const r = await route.fetch({ url: url.searchParams.get('cursor') ? url.toString().replace(/[?&]cursor=[^&]*/, '') : undefined });
      const body = (await r.json()) as { items: { region_id: string }[]; next_cursor: string | null };
      if (!url.searchParams.get('cursor')) return route.fulfill({ response: r, json: { ...body, next_cursor: 'c1' } });
      more++;
      if (fail) return route.abort('connectionrefused');
      // the next page: one more row
      const extra = { ...body.items[0], region_id: `${body.items[0].region_id}-more` };
      return route.fulfill({ response: r, json: { ...body, items: [extra], next_cursor: null } });
    });
    await page.goto(`./#/job/${jobId}/labels`);
    await expect(rows(page)).toHaveCount(8);
    await page.waitForTimeout(3000);
    expect(more, 'next-page requests while it fails').toBeLessThanOrEqual(1);
    const list = page.getByTestId('region-list');
    await expect(list.getByTestId('more-error')).toBeVisible();

    fail = false;
    await list.getByRole('button', { name: 'Retry' }).click();
    await expect(page.locator('[data-testid="region-row"][data-region$="-more"]')).toHaveCount(1);
    await expect(list.getByTestId('more-error')).toHaveCount(0);
    expect(more).toBe(2);
  });
});

test.describe('polling survives a failed poll', () => {
  test('one 503 from GET /v1/jobs/{id} during extraction: polling goes on and the page reaches in_review', async ({ page, request, guard }) => {
    guard.allowConsole(FAILED_LOAD);
    const jobId = await seededJob(request);
    let n = 0;
    await page.route(new RegExp(`/v1/jobs/${jobId}$`), async (route) => {
      n++;
      if (n === 2) return route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ detail: 'restarting' }) });
      if (n <= 3) return fulfillJob(route, extracting);
      return route.fallback();
    });
    await page.goto(`./#/job/${jobId}/labels`);
    await expect(page.getByTestId('busy')).toContainText('Extracting');
    await expect(page.getByTestId('busy')).toHaveCount(0, { timeout: 15_000 });
    await expect(page.getByTestId('error-banner')).toHaveCount(0);
    expect(n).toBeGreaterThanOrEqual(4);
  });

  test('one 502 from /events: the events are still polled', async ({ page, request, guard }) => {
    guard.allowConsole(FAILED_LOAD);
    const jobId = await seededJob(request);
    await page.route(new RegExp(`/v1/jobs/${jobId}$`), (route) => fulfillJob(route, extracting));
    let ev = 0;
    await page.route(/\/events\?/, async (route) => {
      ev++;
      if (ev === 2) return route.fulfill({ status: 502, contentType: 'application/json', body: JSON.stringify({ detail: 'bad gateway' }) });
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ events: [] }) });
    });
    await page.goto(`./#/job/${jobId}/progress`);
    await expect.poll(() => ev, { timeout: 8_000 }).toBeGreaterThanOrEqual(4);
    await expect(page.getByTestId('error-banner')).toHaveCount(0);
  });

  test('one 503 from GET /v1/jobs while a job runs: the job list is still polled', async ({ page, request, guard }) => {
    guard.allowConsole(FAILED_LOAD);
    await seededJob(request);
    let n = 0;
    await page.route(/\/v1\/jobs$/, async (route) => {
      n++;
      if (n === 2) return route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ detail: 'restarting' }) });
      const r = await route.fetch();
      const body = (await r.json()) as { jobs: Record<string, unknown>[] };
      body.jobs[0].running_op = 'extract';
      return route.fulfill({ response: r, json: body });
    });
    await page.goto('./#/jobs');
    await expect.poll(() => n, { timeout: 10_000 }).toBeGreaterThanOrEqual(4);
  });
});

test.describe('the inventory and the job state', () => {
  test('the inventory stays visible while extraction runs', async ({ page, request }) => {
    const jobId = await seededJob(request);
    await page.route(new RegExp(`/v1/jobs/${jobId}$`), (route) => fulfillJob(route, extracting));
    await page.goto(`./#/job/${jobId}/labels`);
    await expect(page.getByTestId('busy')).toContainText('Extracting');
    await expect(rows(page)).toHaveCount(8);
    await expect(page.getByTestId('list-total')).toHaveText('8 regions');
  });

  test('while labelling runs the list says so', async ({ page, request }) => {
    const jobId = await seededJob(request);
    await page.route(new RegExp(`/v1/jobs/${jobId}$`), (route) =>
      fulfillJob(route, (j) => {
        j.status = 'uploaded';
        j.running_op = 'label';
        j.last_error = null;
      }),
    );
    await page.goto(`./#/job/${jobId}/labels`);
    await expect(page.locator('.inv-empty')).toHaveText('Labelling is running; the regions appear when it is done.');
    await expect(rows(page)).toHaveCount(0);
  });
});

test.describe('review on a delivered job', () => {
  test('no action and no Exclude all is offered; a read-only note says why', async ({ page, request }) => {
    const jobId = await approvedJob(request);
    const c = await request.post(`/v1/jobs/${jobId}/confirm`, { data: { by: 'E2E Tester' } });
    expect(c.ok(), await c.text()).toBeTruthy();
    await waitStatus(request, jobId, 'delivered');
    await page.goto(`./#/job/${jobId}/review`);
    await expect(page.getByTestId('issue-title')).not.toHaveText('Loading…');
    await expect(page.getByTestId('cluster').first()).toBeVisible();
    await expect(page.getByTestId('review-closed')).toContainText('delivered');
    await expect(page.locator('[data-testid^="act-"]')).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'Exclude all' })).toHaveCount(0);
    await expect(page.getByLabel('Reviewer')).toHaveCount(0);
    await expect(page.getByLabel('Note')).toHaveCount(0);
  });
});

test.describe('deliver', () => {
  test('a reload right after Confirm that sees confirmed / idle keeps polling until delivered', async ({ page, request }) => {
    const jobId = await approvedJob(request);
    let posted = false;
    let gets = 0;
    await page.route(/\/confirm$/, async (route) => {
      const r = await route.fetch();
      posted = true;
      await route.fulfill({ response: r });
    });
    await page.route(new RegExp(`/v1/jobs/${jobId}$`), async (route) => {
      if (!posted) return route.fallback();
      gets++;
      if (gets > 1) return route.fallback();
      return fulfillJob(route, (j) => {
        j.status = 'confirmed';
        j.running_op = null;
        j.confirmed_by = 'E2E Tester';
        j.package = null;
      });
    });
    await page.goto(`./#/job/${jobId}/deliver`);
    await page.getByLabel('Confirmed by').fill('E2E Tester');
    await page.getByRole('button', { name: 'Confirm and deliver' }).click();
    await expect(page.getByTestId('delivered')).toBeVisible({ timeout: 30_000 });
    await expect(page.getByTestId('package-check')).toContainText('check passed');
    expect(gets).toBeGreaterThanOrEqual(2);
  });
});
