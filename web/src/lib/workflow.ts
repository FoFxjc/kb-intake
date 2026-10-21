// The workflow the Workflow page edits (src/kb_intake/workflow.py): a handler per region type, excluded pages and an
// optional `llm` block {describe, chart_table}. The page groups the region types into rows as the prototype does
// (Workflow.dc.html); a row's handler is set on every type of the row.

export interface Workflow {
  name: string;
  version: number;
  handlers: Record<string, string>;
  exclude_pages: number[];
  llm?: { describe?: boolean; chart_table?: boolean };
}

export interface HandlerRow {
  key: string;
  label: string;
  types: string[];
  color: string;
  /** handler options in the prototype's order; `describe` is dropped when the LLM is not enabled */
  options: string[];
  note: string;
}

export const DESCRIBE_TYPES = ['chart', 'diagram', 'picture'];

export const HANDLER_NAMES: Record<string, string> = {
  table_structure: 'Table structure',
  text: 'Text by box',
  describe: 'LLM describe',
  discard: 'Discard',
  record: 'Record position',
  unhandled: 'Leave unhandled',
  ocr: 'OCR',
};

export const ROWS: HandlerRow[] = [
  { key: 'table', label: 'Tables', types: ['table'], color: 'var(--type-table)', options: ['table_structure', 'text', 'unhandled'], note: 'TableFormer grid, text from the text layer' },
  { key: 'chart', label: 'Charts', types: ['chart'], color: 'var(--type-chart)', options: ['describe', 'text', 'discard'], note: 'Text inside the chart is always kept' },
  { key: 'diagram', label: 'Diagrams', types: ['diagram'], color: 'var(--type-diagram)', options: ['describe', 'text', 'discard'], note: '' },
  { key: 'picture', label: 'Pictures', types: ['picture'], color: 'var(--type-picture)', options: ['discard', 'describe', 'record'], note: '' },
  { key: 'icon', label: 'Icons', types: ['icon'], color: 'var(--type-picture)', options: ['discard', 'record'], note: '' },
  { key: 'logo', label: 'Logos', types: ['logo'], color: 'var(--type-picture)', options: ['discard', 'record'], note: '' },
  { key: 'signature', label: 'Signatures', types: ['signature'], color: 'var(--type-picture)', options: ['record', 'discard'], note: 'Position only, no content' },
  { key: 'stamp', label: 'Stamps', types: ['stamp'], color: 'var(--type-picture)', options: ['record', 'discard'], note: 'Position only, no content' },
  { key: 'textual', label: 'Text (body, headings, lists, footnotes…)', types: ['title', 'heading', 'text', 'list', 'caption', 'footnote', 'formula', 'toc'], color: 'var(--type-text)', options: ['text'], note: '' },
  { key: 'furniture', label: 'Page headers and footers', types: ['page_header', 'page_footer'], color: '#b5b5ae', options: ['discard', 'text'], note: '' },
  { key: 'unknown', label: 'Unknown', types: ['unknown'], color: '#b5b5ae', options: ['unhandled', 'text'], note: '' },
];

/** The row's handler: the one all its types share, or null when they differ. */
export function rowHandler(row: HandlerRow, wf: Workflow): string | null {
  const hs = new Set(row.types.map((t) => wf.handlers[t]));
  return hs.size === 1 ? [...hs][0] : null;
}

/** The options a row shows: the prototype's (without `describe` when the LLM is off), plus the current handler
 * when it is another one, so the workflow on the server is always visible. */
export function rowOptions(row: HandlerRow, wf: Workflow, llmEnabled: boolean): string[] {
  const opts = row.options.filter((o) => llmEnabled || o !== 'describe');
  for (const t of row.types) {
    const h = wf.handlers[t];
    if (h && !opts.includes(h)) opts.push(h);
  }
  return opts;
}

export function setRow(wf: Workflow, row: HandlerRow, handler: string): Workflow {
  const handlers = { ...wf.handlers };
  for (const t of row.types) handlers[t] = handler;
  return { ...wf, handlers };
}

/** "1-4, 270-276" (hyphen or en dash, commas or spaces) -> sorted distinct page numbers, or an error message. */
export function parsePages(text: string, pageCount: number | null): { pages: number[] } | { error: string } {
  const out = new Set<number>();
  const parts = text.split(/[,;\s]+/).filter(Boolean);
  for (const part of parts) {
    const m = /^(\d+)(?:[-–](\d+))?$/.exec(part);
    if (!m) return { error: `"${part}" is not a page or a range like 1-4` };
    const a = Number(m[1]);
    const b = m[2] === undefined ? a : Number(m[2]);
    if (a < 1 || b < a) return { error: `"${part}" is not a valid range` };
    if (pageCount !== null && b > pageCount) return { error: `page ${b} is beyond the last page (${pageCount})` };
    for (let n = a; n <= b; n++) out.add(n);
  }
  return { pages: [...out].sort((x, y) => x - y) };
}

/** [1, 2, 3, 4, 270] -> "1-4, 270". */
export function formatPages(pages: number[]): string {
  const sorted = [...pages].sort((a, b) => a - b);
  const runs: string[] = [];
  for (let i = 0; i < sorted.length; ) {
    let j = i;
    while (j + 1 < sorted.length && sorted[j + 1] === sorted[j] + 1) j++;
    runs.push(i === j ? String(sorted[i]) : `${sorted[i]}-${sorted[j]}`);
    i = j + 1;
  }
  return runs.join(', ');
}

/** The YAML text of a workflow, in the server's layout (workflow.dump). Strings are JSON-quoted (valid YAML). */
export function workflowYaml(wf: Workflow, types: string[]): string {
  const order = [...types.filter((t) => t in wf.handlers), ...Object.keys(wf.handlers).filter((t) => !types.includes(t)).sort()];
  const lines = [`name: ${JSON.stringify(wf.name)}`, `version: ${wf.version}`, 'handlers:'];
  for (const t of order) lines.push(`  ${t}: ${wf.handlers[t]}`);
  lines.push(`exclude_pages: ${JSON.stringify(wf.exclude_pages)}`);
  if (wf.llm) {
    const parts = (['describe', 'chart_table'] as const)
      .filter((k) => wf.llm![k] !== undefined)
      .map((k) => `${k}: ${wf.llm![k] ? 'true' : 'false'}`);
    lines.push(`llm: {${parts.join(', ')}}`);
  }
  return `${lines.join('\n')}\n`;
}

export interface RunSummary {
  tables: number;
  text: number;
  describe: number;
  chartTable: number | null;
  discard: number;
  record: number;
  unhandled: number;
}

/** "This run will process": region counts per handler (label-stage counts per type, before excluded pages). */
export function runSummary(wf: Workflow, counts: Record<string, number>, llmOn: { describe: boolean; chartTable: boolean }): RunSummary {
  const sum = (h: string) => Object.entries(counts).reduce((a, [t, n]) => a + (wf.handlers[t] === h ? n : 0), 0);
  const describe = sum('describe');
  return {
    tables: wf.handlers.table === 'table_structure' ? counts.table ?? 0 : 0,
    text: sum('text') + (llmOn.describe ? 0 : describe),
    describe: llmOn.describe ? describe : 0,
    // chart to table runs inside describe (llm.describe_inputs), on bar, line and pie charts only: an upper bound
    chartTable: llmOn.describe && llmOn.chartTable && wf.handlers.chart === 'describe' ? counts.chart ?? 0 : null,
    discard: sum('discard'),
    record: sum('record'),
    unhandled: sum('unhandled'),
  };
}
