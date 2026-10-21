import { expect, shot, test } from './fixtures';
import { job, LLM_BASE, llmPaths, plannedJob, trackPaths, waitStatus } from './flow';

const isPut = (r: import('@playwright/test').Request) => r.method() === 'PUT' && r.url().endsWith('/workflow');
const isApprove = (r: import('@playwright/test').Request) => r.method() === 'POST' && r.url().endsWith('/approve');

test.describe('LLM disabled', () => {
  test('edit a handler and excluded pages, approve, follow the extraction to review; no LLM element or request', async ({ page, request }) => {
    const paths = trackPaths(page);
    const jobId = await plannedJob(request);
    await page.goto(`./#/job/${jobId}/workflow`);
    await expect(page.getByRole('heading', { name: 'How to handle each region type' })).toBeVisible();

    const tables = page.getByTestId('wf-row-table');
    await expect(tables.getByRole('button', { name: 'Table structure' })).toHaveAttribute('aria-pressed', 'true');
    await expect(page.getByTestId('sum-tables')).toHaveText('1 table');
    // no LLM element: no panel, no describe option, no LLM rows in the summary
    await expect(page.getByTestId('llm-panel')).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'LLM describe' })).toHaveCount(0);
    await expect(page.getByTestId('sum-describe')).toHaveCount(0);
    await expect(page.getByTestId('wf-row-chart').getByRole('button')).toHaveText(['Text by box', 'Discard']);
    await shot(page, 'workflow-no-llm');

    await tables.getByRole('button', { name: 'Text by box' }).click();
    await expect(tables.getByRole('button', { name: 'Text by box' })).toHaveAttribute('aria-pressed', 'true');
    await expect(page.getByTestId('sum-tables')).toHaveText('0 tables');

    const exclude = page.getByLabel('Excluded pages');
    await exclude.fill('2-3');
    await expect(page.getByText('page 3 is beyond the last page (2)')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Approve and start extraction' })).toBeDisabled();
    await exclude.fill('2');

    await page.getByLabel('Approved by').fill('E2E Tester');
    const put = page.waitForRequest(isPut);
    const approve = page.waitForRequest(isApprove);
    await page.getByRole('button', { name: 'Approve and start extraction' }).click();
    const yaml = (await put).postData() ?? '';
    expect(yaml).toContain('\n  table: text\n');
    expect(yaml).toContain('\nexclude_pages: [2]\n');
    expect(yaml).not.toContain('llm:');
    expect((await put).headers()['content-type']).toContain('application/yaml');
    const body = (await approve).postDataJSON() as { by: string; version: number };
    expect(body.by).toBe('E2E Tester');
    expect(typeof body.version).toBe('number');

    // the progress page polls the events until the job is in review
    await expect(page).toHaveURL(new RegExp(`#/job/${jobId}/progress$`));
    await expect(page.getByTestId('go-review')).toBeVisible({ timeout: 45_000 });
    await expect(page.getByTestId('progress-status')).toHaveText('Extraction done');
    await expect(page.getByTestId('phase-docling')).toHaveAttribute('data-state', 'done');
    await expect(page.getByTestId('phase-regions')).toHaveAttribute('data-state', 'done');
    await expect(page.getByTestId('phase-llm')).toHaveCount(0);
    expect(paths.filter((p) => p.endsWith('/events')).length).toBeGreaterThan(0);
    await shot(page, 'progress-no-llm');

    // the server approved what the page sent
    expect((await job(request, jobId)).status).toBe('in_review');
    const wf = (await (await request.get(`/v1/jobs/${jobId}/workflow`)).json()) as {
      workflow: { handlers: Record<string, string>; exclude_pages: number[]; llm?: unknown };
    };
    expect(wf.workflow.handlers.table).toBe('text');
    expect(wf.workflow.exclude_pages).toEqual([2]);
    expect(wf.workflow.llm).toBeUndefined();

    await page.goto(`./#/job/${jobId}/workflow`);
    await expect(page.getByTestId('approved')).toContainText('Approved (E2E Tester');
    await expect(page.getByTestId('wf-row-table').getByRole('button', { name: 'Text by box' })).toBeDisabled();
    expect(llmPaths(paths), 'LLM requests').toEqual([]);
  });

  test('approving twice shows the server message of the 409', async ({ page, request, context, guard }) => {
    guard.allowConsole(/status of 409/);
    const jobId = await plannedJob(request);
    // another tab approves first
    const other = await context.newPage();
    await other.goto(`./#/job/${jobId}/workflow`);
    await other.getByLabel('Approved by').fill('First Approver');
    await page.goto(`./#/job/${jobId}/workflow`);
    await page.getByLabel('Approved by').fill('Second Approver');
    await other.getByRole('button', { name: 'Approve and start extraction' }).click();
    await expect(other).toHaveURL(/\/progress$/);
    await other.close();

    await page.getByRole('button', { name: 'Approve and start extraction' }).click();
    const banner = page.getByTestId('error-banner');
    await expect(banner).toContainText('409');
    await expect(banner).toContainText(jobId);
    await expect(banner.getByRole('button', { name: 'Retry' })).toBeVisible();
    await expect(page).toHaveURL(/\/workflow$/);
    // the page now shows the approval that won
    await expect(page.getByTestId('approved')).toContainText('First Approver');
    await shot(page, 'workflow-409');
    await waitStatus(request, jobId, 'in_review');
  });
});

test.describe('LLM enabled (fake model on 127.0.0.1)', () => {
  test.use({ baseURL: LLM_BASE });

  test('describe offered for charts, diagrams and pictures; the switches go into the YAML llm block; LLM progress', async ({ page, request }) => {
    const jobId = await plannedJob(request);
    await page.goto(`./#/job/${jobId}/workflow`);
    const panel = page.getByTestId('llm-panel');
    await expect(panel).toBeVisible();
    await expect(page.getByTestId('llm-notice')).toContainText(`sent to ${new URL(LLM_BASE).hostname}:`);
    await expect(panel.locator('.llm-chip')).toContainText('127.0.0.1:');   // the fake model's host, not 'remote'
    await expect(panel).not.toContainText('remote service');

    // the suggestion sets charts to describe; describe is offered for chart, diagram and picture only
    await expect(page.getByTestId('wf-row-chart').getByRole('button', { name: 'LLM describe' })).toHaveAttribute('aria-pressed', 'true');
    await expect(page.getByTestId('wf-row-diagram').getByRole('button', { name: 'LLM describe' })).toHaveCount(1);
    await expect(page.getByTestId('wf-row-picture').getByRole('button', { name: 'LLM describe' })).toHaveAttribute('aria-pressed', 'false');
    for (const row of ['table', 'icon', 'logo', 'signature', 'textual', 'furniture', 'unknown'])
      await expect(page.getByTestId(`wf-row-${row}`).getByRole('button', { name: 'LLM describe' })).toHaveCount(0);
    await expect(page.getByTestId('sum-describe')).toHaveText('1 region');
    await expect(page.getByTestId('sum-chart-table')).toHaveText('up to 1 chart (experimental)');

    const summary = panel.getByRole('button', { name: /Document summary/ });
    await expect(summary).toBeDisabled();
    const describe = panel.getByRole('button', { name: /Chart and picture descriptions/ });
    const chartTable = panel.getByRole('button', { name: /Chart to table/ });
    await expect(describe).toHaveAttribute('aria-pressed', 'true');
    await expect(chartTable).toHaveAttribute('aria-pressed', 'true');
    await describe.click();
    await expect(describe).toHaveAttribute('aria-pressed', 'false');
    await expect(page.getByTestId('sum-describe')).toHaveText('0 regions');
    await describe.click();
    await chartTable.click();
    await expect(chartTable).toHaveAttribute('aria-pressed', 'false');
    await expect(page.getByTestId('sum-chart-table')).toHaveText('Off');
    await shot(page, 'workflow-llm');

    await page.getByLabel('Approved by').fill('E2E Tester');
    const put = page.waitForRequest(isPut);
    await page.getByRole('button', { name: 'Approve and start extraction' }).click();
    const yaml = (await put).postData() ?? '';
    expect(yaml).toContain('\n  chart: describe\n');
    expect(yaml).toContain('\nllm: {describe: true, chart_table: false}\n');

    await expect(page).toHaveURL(/\/progress$/);
    await expect(page.getByTestId('go-review')).toBeVisible({ timeout: 45_000 });
    await expect(page.getByTestId('phase-llm')).toHaveAttribute('data-state', 'done');
    await expect(page.getByTestId('phase-llm')).toContainText('1 / 1');
    await shot(page, 'progress-llm');

    const wf = (await (await request.get(`/v1/jobs/${jobId}/workflow`)).json()) as { workflow: { llm?: unknown } };
    expect(wf.workflow.llm).toEqual({ describe: true, chart_table: false });
    const llm = (await (await request.get(`/v1/jobs/${jobId}/llm`)).json()) as { items: { kind: string }[] };
    expect(llm.items.filter((i) => i.kind === 'describe')).toHaveLength(1);
    expect(llm.items.filter((i) => i.kind === 'chart_table')).toHaveLength(0);
  });

  test('describe switched off in the configuration (kb-intake doctor: images fail) is not offered', async ({ page, request }) => {
    const jobId = await plannedJob(request);
    await page.route('**/v1/health', async (route) => {
      const r = await route.fetch();
      const j = await r.json();
      j.llm.features.describe = false;
      return route.fulfill({ response: r, json: j });
    });
    await page.goto(`./#/job/${jobId}/workflow`);
    await expect(page.getByTestId('llm-panel')).toBeVisible();
    const chart = page.getByTestId('wf-row-chart');
    // this server suggested describe before the switch went off; the current handler always stays visible, so
    // move the row to text first: describe is then no longer among the options
    await chart.getByRole('button', { name: 'Text by box' }).click();
    await expect(chart.getByRole('button', { name: 'Text by box' })).toHaveAttribute('aria-pressed', 'true');
    await expect(chart.getByRole('button', { name: 'LLM describe' })).toHaveCount(0);
  });
});
