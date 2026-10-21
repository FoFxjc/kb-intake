import { expect, seededJob, shot, test } from './fixtures';

async function rendered(page: import('@playwright/test').Page, n: number) {
  const pdfPage = page.getByTestId('pdf-page');
  await expect(pdfPage).toHaveAttribute('data-page', String(n));
  await expect(pdfPage).toHaveAttribute('data-rendered', 'true', { timeout: 20_000 });
}

/** Share of non-white pixels on the canvas: a rendered page is not blank. */
async function inked(page: import('@playwright/test').Page): Promise<number> {
  return page.evaluate(() => {
    const c = document.querySelector('canvas')!;
    const d = c.getContext('2d')!.getImageData(0, 0, c.width, c.height).data;
    let ink = 0;
    for (let i = 0; i < d.length; i += 4) if (d[i] < 200 || d[i + 1] < 200 || d[i + 2] < 200) ink++;
    return ink / (d.length / 4);
  });
}

test('the page viewer renders the table page with its boxes', async ({ page, request }) => {
  const jobId = await seededJob(request);
  await page.goto(`./#/job/${jobId}/labels`);
  await rendered(page, 1);
  await expect(page.getByTestId('page-of')).toHaveText('of 2');
  expect(await inked(page)).toBeGreaterThan(0.001);

  const tables = page.locator('[data-testid="ov-region"][data-type="table"]');
  await expect(tables).toHaveCount(1);
  await expect(page.getByTestId('ov-cell')).toHaveCount(4);
  await expect(page.locator('[data-testid="ov-region"][data-type="chart"]')).toHaveCount(1);
  // the table box sits where the page view says (72, 200, 400, 300 on a 595 x 842 page)
  const frame = (await page.getByTestId('pdf-page').boundingBox())!;
  const box = (await tables.boundingBox())!;
  expect(Math.abs(box.x - frame.x - (72 / 595) * frame.width)).toBeLessThan(2);
  expect(Math.abs(box.y - frame.y - (200 / 842) * frame.height)).toBeLessThan(2);
  expect(Math.abs(box.width - (328 / 595) * frame.width)).toBeLessThan(2);

  await page.getByRole('listitem').filter({ hasText: 'Table' }).first().click();
  await expect(page.getByTestId('region-detail')).toContainText('demo-ar-2025-en:p0001:r003');
  await expect(tables).toHaveClass(/sel/);
  await shot(page, 'viewer-page1');

  await page.getByRole('button', { name: 'Next page' }).click();
  await rendered(page, 2);
  await expect(page.getByTestId('ov-find')).toHaveCount(1);       // the find_tables table Docling missed
  await expect(page.getByTestId('overlay')).toContainText('find_tables');
  await shot(page, 'viewer-page2');
});

test('switching pages 20 times leaves one canvas', async ({ page, request }) => {
  const jobId = await seededJob(request);
  await page.goto(`./#/job/${jobId}/labels`);
  await rendered(page, 1);
  const next = page.getByRole('button', { name: 'Next page' });
  const prev = page.getByRole('button', { name: 'Previous page' });
  for (let i = 0; i < 20; i++) await (i % 2 === 0 ? next : prev).click();
  await rendered(page, 1);
  await expect(page.locator('canvas')).toHaveCount(1);
  for (let i = 0; i < 21; i++) await (i % 2 === 0 ? next : prev).click({ noWaitAfter: true });
  await rendered(page, 2);
  await expect(page.locator('canvas')).toHaveCount(1);
  expect(await inked(page)).toBeGreaterThan(0.001);
});
