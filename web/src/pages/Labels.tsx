import { useEffect, useState } from 'react';
import { api, type ApiError, type JobDetail, type PageView } from '../api/client';
import { ErrorBanner } from '../components/ErrorBanner';
import { Overlay } from '../components/Overlay';
import { PdfPage } from '../components/PdfPage';
import { asApiError } from '../lib/hooks';
import { usePdfDocument } from '../lib/pdf';
import { typeStyle } from '../lib/types';

const LEGEND = ['table', 'chart', 'diagram', 'picture', 'heading', 'text'];

/** Labels & inventory. This stage carries the page viewer (source.pdf through pdf.js, the page's boxes over it);
 * the inventory (metadata, summary, facets, region list, search) is added with the next task of the plan. */
export function LabelsPage({ detail }: { detail: JobDetail }) {
  const jobId = detail.job.id;
  const pdf = usePdfDocument(jobId);
  const statPages = typeof detail.stats?.pages === 'number' ? detail.stats.pages : null;
  const pageCount = pdf.doc?.numPages ?? statPages ?? 1;
  const [pageNo, setPageNo] = useState(1);
  const [view, setView] = useState<PageView | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  const labelled = detail.stats !== null && !detail.job.running_op;

  useEffect(() => {
    if (!labelled) return;
    const ac = new AbortController();
    setError(null);
    api.page(jobId, pageNo, ac.signal).then(
      (v) => setView(v),
      (e: unknown) => {
        if (!(e instanceof DOMException && e.name === 'AbortError')) setError(asApiError(e));
      },
    );
    return () => ac.abort();
  }, [jobId, pageNo, labelled, tick]);

  const go = (n: number) => {
    const next = Math.min(Math.max(1, n), pageCount);
    setSelected(null);
    setPageNo(next);
  };
  const shown = view && view.page_no === pageNo ? view : null;
  const sel = shown?.regions.find((r) => r.region_id === selected) ?? null;

  return (
    <section className="page">
      <main className="card viewer-main">
        <div className="viewer-bar">
          <button type="button" className="btn" aria-label="Previous page" onClick={() => go(pageNo - 1)} disabled={pageNo <= 1}>
            {'←'}
          </button>
          <button type="button" className="btn" aria-label="Next page" onClick={() => go(pageNo + 1)} disabled={pageNo >= pageCount}>
            {'→'}
          </button>
          <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 14, fontWeight: 600 }}>
            Page
            <input
              type="number"
              min={1}
              max={pageCount}
              value={pageNo}
              aria-label="Page number"
              onChange={(e) => {
                const n = Number(e.target.value);
                if (Number.isInteger(n) && n >= 1) go(n);
              }}
            />
            <span className="muted" style={{ fontWeight: 400 }} data-testid="page-of">
              of {pageCount}
            </span>
          </label>
          <div style={{ flex: 1 }} />
          <div className="legend" aria-label="Box colours">
            {LEGEND.map((t) => (
              <span key={t}>
                <span className="swatch" style={{ ['--c' as string]: typeStyle(t).color }} />
                {typeStyle(t).label}
              </span>
            ))}
            <span>
              <span className="swatch" style={{ ['--c' as string]: 'var(--warn-ink)' }} />
              find_tables
            </span>
          </div>
        </div>
        {error && (
          <div style={{ padding: 12 }}>
            <ErrorBanner error={error} onRetry={() => setTick((t) => t + 1)} />
          </div>
        )}
        <div className="viewer-stage">
          <PdfPage doc={pdf.doc} pageNo={pageNo} width={shown?.width} height={shown?.height} error={pdf.error}>
            {shown && <Overlay view={shown} selected={selected} />}
          </PdfPage>
        </div>
      </main>
      <aside className="viewer-side">
        <div className="card card-pad">
          <div className="eyebrow">Regions on page {pageNo}</div>
          {!labelled && <div className="muted">The regions appear when labelling is done.</div>}
          {shown && shown.regions.length === 0 && <div className="muted">No regions on this page.</div>}
          <div className="region-list" role="list">
            {shown?.regions.map((r) => {
              const s = typeStyle(r.type);
              return (
                <button
                  key={r.region_id}
                  type="button"
                  role="listitem"
                  className={`region-row${r.region_id === selected ? ' on' : ''}`}
                  onClick={() => setSelected(r.region_id === selected ? null : r.region_id)}
                >
                  <span className="swatch" style={{ ['--c' as string]: s.color }} />
                  <span style={{ width: 64 }}>{s.label}</span>
                  <span className="t">{r.text ?? (r.status ? `(${r.status})` : '')}</span>
                </button>
              );
            })}
          </div>
        </div>
        {sel && (
          <div className="card card-pad" data-testid="region-detail">
            <div className="kv">
              <div>Type</div>
              <div>{typeStyle(sel.type).label}</div>
              <div>Confidence</div>
              <div>{sel.score === null ? '—' : sel.score.toFixed(2)}</div>
              <div>Region id</div>
              <div className="mono" style={{ fontSize: 12, overflowWrap: 'anywhere' }}>
                {sel.region_id}
              </div>
              <div>Status</div>
              <div>{sel.status ?? 'labelled'}</div>
            </div>
            {sel.text && (
              <div style={{ fontSize: 13, lineHeight: 1.5, padding: 10, background: 'var(--surface-2)', borderRadius: 6, border: '1px solid var(--line-faint)', whiteSpace: 'pre-wrap' }}>
                {sel.text}
              </div>
            )}
          </div>
        )}
      </aside>
    </section>
  );
}
