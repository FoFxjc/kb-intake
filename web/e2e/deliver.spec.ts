import { expect, shot, test } from './fixtures';
import { approvedJob, job, LLM_BASE, llmPaths, trackPaths, waitStatus } from './flow';

interface Gap {
  code: string;
  resolved?: unknown;
}

/** Acks the first open queue item through the API (a human action that resolves a gap). */
async function ackOne(request: import('@playwright/test').APIRequestContext, jobId: string): Promise<string> {
  const q = (await (await request.get(`/v1/jobs/${jobId}/queue`)).json()) as {
    groups: { items: { item_id: string; status: string; kind: string }[] }[];
  };
  const item = q.groups.flatMap((g) => g.items).find((i) => i.status !== 'resolved' && i.kind === 'gap');
  expect(item, 'an open gap item').toBeTruthy();
  const r = await request.post(`/v1/jobs/${jobId}/review`, {
    data: { by: 'E2E Reviewer', ops: [{ action: 'ack', item_id: item!.item_id, verdict: 'ok' }] },
  });
  expect(r.status(), await r.text()).toBe(200);
  return item!.item_id;
}

test.describe('LLM disabled', () => {
  test('deliver page counts match the API; confirm delivers and shows the package check', async ({ page, request }) => {
    const paths = trackPaths(page);
    const jobId = await approvedJob(request);
    await ackOne(request, jobId);

    const facets = (await (await request.get(`/v1/jobs/${jobId}/facets`)).json()) as { total: number; type: Record<string, number> };
    const gaps = ((await (await request.get(`/v1/jobs/${jobId}/gaps`)).json()) as { gaps: Gap[] }).gaps;
    const open = gaps.filter((g) => !g.resolved);
    expect(gaps.length - open.length, 'the ack resolved a gap').toBe(1);

    await page.goto(`./#/job/${jobId}/deliver`);
    await expect(page.getByTestId('kpi-regions')).toContainText(String(facets.total));
    await expect(page.getByTestId('kpi-tables').locator('.kpi-value')).toHaveText(String(facets.type.table ?? 0));
    await expect(page.getByTestId('kpi-open').locator('.kpi-value')).toHaveText(String(open.length));
    await expect(page.getByTestId('kpi-check').locator('.kpi-value')).toHaveText('—');
    await expect(page.getByTestId('kpi-llm')).toHaveCount(0);

    // open / resolved per gap code
    const codes = [...new Set(gaps.map((g) => g.code))];
    await expect(page.getByTestId('gap-row')).toHaveCount(codes.length);
    for (const code of codes) {
      const row = page.locator(`[data-testid="gap-row"][data-code="${code}"]`);
      const o = gaps.filter((g) => g.code === code && !g.resolved).length;
      const r = gaps.filter((g) => g.code === code && g.resolved).length;
      await expect(row.getByTestId('gap-open')).toHaveText(String(o));
      await expect(row.getByTestId('gap-resolved')).toHaveText(r ? String(r) : '—');
    }

    // the human actions and the package files (no llm.jsonl without LLM outputs)
    await expect(page.getByTestId('op-row')).toHaveCount(1);
    await expect(page.getByTestId('op-row')).toContainText('ack');
    await expect(page.getByTestId('op-row')).toContainText('E2E Reviewer');
    await expect(page.getByTestId('op-confirm')).toContainText('(pending)');
    const files = page.getByTestId('package-file').locator('.name');
    await expect(files).toContainText(['source.pdf', 'manifest.json', 'pages.jsonl', 'regions.jsonl', 'tables.jsonl']);
    await expect(files.filter({ hasText: 'llm.jsonl' })).toHaveCount(0);
    await expect(page.getByTestId('package-file').filter({ hasText: 'pages.jsonl' })).toContainText('2 rows');
    await shot(page, 'deliver');

    await page.getByLabel('Confirmed by').fill('E2E Tester');
    const confirm = page.waitForRequest((r) => r.method() === 'POST' && r.url().endsWith('/confirm'));
    await page.getByRole('button', { name: 'Confirm and deliver' }).click();
    expect((await confirm).postDataJSON()).toEqual({ by: 'E2E Tester' });

    const delivered = page.getByTestId('delivered');
    await expect(delivered).toBeVisible({ timeout: 45_000 });
    await expect(page.getByTestId('package-check')).toContainText('check passed');
    await expect(page.getByTestId('kpi-check').locator('.kpi-value')).toHaveText('OK');
    await expect(page.getByTestId('op-confirm')).toContainText('Confirmed for delivery');
    const j = await job(request, jobId);
    expect(j.status).toBe('delivered');
    await expect(page.getByTestId('package-name')).toHaveText(`delivery/${j.package}/`);
    const pkgs = (await (await request.get('/v1/packages')).json()) as {
      packages: { package: string; check: { ok: boolean; counts: Record<string, number> } }[];
    };
    const pkg = pkgs.packages.find((p) => p.package === j.package);
    expect(pkg?.check.ok).toBe(true);
    await expect(page.getByTestId('package-file').filter({ hasText: 'regions.jsonl' })).toContainText(
      `${pkg!.check.counts['regions.jsonl']} row`,
    );
    await expect(page.getByTestId('confirm-form')).toHaveCount(0);
    // the step bar of a delivered job: Confirm done, Deliver is the page shown
    const flow = page.getByRole('navigation', { name: 'Flow' });
    await expect(flow.locator('[aria-current="step"]')).toHaveText(/Deliver$/);
    await expect(flow.locator('.step', { hasText: 'Confirm' })).toHaveAttribute('data-state', 'done');
    await shot(page, 'deliver-delivered');
    expect(llmPaths(paths), 'LLM requests').toEqual([]);
  });
});

test.describe('LLM enabled (fake model on 127.0.0.1)', () => {
  test.use({ baseURL: LLM_BASE });

  test('LLM outputs are counted and llm.jsonl is listed and delivered', async ({ page, request }) => {
    const jobId = await approvedJob(request);
    // the summary runs after labelling, in the background: wait for it so the count below is final
    await expect
      .poll(async () => ((await (await request.get(`/v1/jobs/${jobId}/summary`)).json()) as { status: string }).status, { timeout: 30_000 })
      .not.toBe('pending');
    const llm = (await (await request.get(`/v1/jobs/${jobId}/llm`)).json()) as { items: { kind: string }[] };
    expect(llm.items.length).toBeGreaterThan(0);

    await page.goto(`./#/job/${jobId}/deliver`);
    await expect(page.getByTestId('kpi-llm').locator('.kpi-value')).toHaveText(String(llm.items.length));
    const describes = llm.items.filter((i) => i.kind === 'describe').length;
    await expect(page.getByTestId('kpi-llm')).toContainText(`descriptions ${describes}`);
    await expect(page.getByTestId('package-file').locator('.name').filter({ hasText: 'llm.jsonl' })).toHaveCount(1);
    await shot(page, 'deliver-llm');

    await page.getByLabel('Confirmed by').fill('E2E Tester');
    await page.getByRole('button', { name: 'Confirm and deliver' }).click();
    await expect(page.getByTestId('package-check')).toContainText('check passed', { timeout: 45_000 });
    await waitStatus(request, jobId, 'delivered');
    await expect(page.getByTestId('package-file').filter({ hasText: 'llm.jsonl' })).toContainText(`${llm.items.length} row`);
  });
});
