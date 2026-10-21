// What the Confirm & deliver page shows, computed from the API answers: gap counts per code (open / resolved),
// human actions grouped as they were sent, and the package file list (prototype Deliver.dc.html).
import type { Gap } from '../api/client';
import type { ReviewRow } from '../api/flow';

export const GAP_LABELS: Record<string, string> = {
  table_suspected: 'Suspected missed tables',
  low_confidence: 'Low confidence',
  table_words_unassigned: 'Unassigned values in tables',
  table_unmatched: 'Tables found only by extraction',
  unhandled: 'Unhandled regions',
  no_text_layer: 'No text layer (needs OCR)',
  lang_mismatch: 'Language differs from the declaration',
  extractor_degraded: 'Extractor degraded',
  llm_failed: 'LLM call failed',
};

export interface GapCount {
  code: string;
  label: string;
  open: number;
  resolved: number;
}

export function gapCounts(gaps: Gap[]): GapCount[] {
  const by = new Map<string, GapCount>();
  for (const g of gaps) {
    const c = by.get(g.code) ?? { code: g.code, label: GAP_LABELS[g.code] ?? g.code, open: 0, resolved: 0 };
    if (g.resolved) c.resolved++;
    else c.open++;
    by.set(g.code, c);
  }
  const order = Object.keys(GAP_LABELS);
  const rank = (code: string) => (order.includes(code) ? order.indexOf(code) : order.length);
  return [...by.values()].sort((a, b) => rank(a.code) - rank(b.code) || a.code.localeCompare(b.code));
}

export const pageOfRegion = (id: string | null | undefined) => {
  const m = id ? /:p(\d{4}):/.exec(id) : null;
  return m ? Number(m[1]) : null;
};

export interface OpLine {
  what: string;
  detail: string;
  by: string;
  count: number;
}

function describe(o: ReviewRow): string {
  const page = pageOfRegion(o.region_id);
  const where = page ? `p${page} ` : '';
  switch (o.action) {
    case 'reclassify':
      return `${where}${o.from ?? '?'} → ${o.to ?? '?'}`;
    case 'exclude':
      return `${where}region excluded${o.from ? ` (was ${o.from})` : ''}`;
    case 'ack': {
      const t = o.target;
      const what = t ? (t.code ?? t.kind) : 'item';
      const at = t?.page_no ? ` p${t.page_no}` : '';
      return `${what}${at}${o.verdict ? ` · ${o.verdict}` : ''}`;
    }
    default:
      return o.region_id ?? '';
  }
}

/** Review rows as lines; consecutive rows of one request (same action, second, name and change) become one line. */
export function opLines(ops: ReviewRow[]): OpLine[] {
  const out: (OpLine & { key: string })[] = [];
  for (const o of ops) {
    const key = [o.action, o.at.slice(0, 19), o.by, o.from ?? '', o.to ?? ''].join('|');
    const last = out[out.length - 1];
    if (last && last.key === key && o.action !== 'ack') {
      last.count++;
      continue;
    }
    out.push({ key, what: o.action, detail: describe(o) + (o.note ? ` (${o.note})` : ''), by: o.by, count: 1 });
  }
  return out.map(({ key: _key, ...l }) => {
    if (l.count === 1) return l;
    const what = l.what === 'exclude' ? 'regions excluded' : `regions ${l.detail.replace(/^p\d+ /, '')}`;
    return { ...l, detail: `${l.count} ${what} (one request)` };
  });
}

export interface FileLine {
  name: string;
  note: string;
}

/** The package files (deliver.py); llm.jsonl only when the job has LLM outputs. Row counts come from the contract
 * check once the package is delivered; before, from the job (pages, regions). */
export function packageFiles(o: { pages: number | null; regions: number | null; llmOutputs: boolean; counts?: Record<string, unknown> }): FileLine[] {
  const rows = (f: string, before: number | null, what: string) => {
    const n = o.counts && typeof o.counts[f] === 'number' ? (o.counts[f] as number) : before;
    return n === null ? what : `${n.toLocaleString('en-US')} row${n === 1 ? '' : 's'}${what ? ` · ${what}` : ''}`;
  };
  const files: FileLine[] = [
    { name: 'source.pdf', note: 'Original, byte for byte' },
    { name: 'manifest.json', note: 'Identity, metadata, stats, versions' },
    { name: 'pages.jsonl', note: rows('pages.jsonl', o.pages, '') },
    { name: 'regions.jsonl', note: rows('regions.jsonl', o.regions, '') },
    { name: 'tables.jsonl', note: rows('tables.jsonl', null, 'Table grids') },
    { name: 'layout.jsonl', note: rows('layout.jsonl', o.pages, 'word boxes') },
    { name: 'gaps.jsonl', note: rows('gaps.jsonl', null, 'Open issues') },
  ];
  if (o.llmOutputs) files.push({ name: 'llm.jsonl', note: rows('llm.jsonl', null, 'Model outputs · low weight') });
  files.push(
    { name: 'workflow.json', note: 'Approved plan' },
    { name: 'review.jsonl', note: rows('review.jsonl', null, 'Human actions') },
    { name: 'raw/docling*.json', note: 'For audit' },
  );
  return files;
}
