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
