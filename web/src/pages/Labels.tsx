import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api, type ApiError, type Facets, type JobDetail, type PageView, type RegionQuery, type RegionRow, type Summary } from '../api/client';
import { ErrorBanner } from '../components/ErrorBanner';
import { FacetsPanel } from '../components/Facets';
import { HeaderSlot } from '../components/Header';
import { LlmBadge } from '../components/LlmBadge';
import { Overlay } from '../components/Overlay';
import { PdfPage } from '../components/PdfPage';
import { RegionList } from '../components/RegionList';
import { asBox } from '../lib/boxes';
import { useDebounced } from '../lib/debounce';
import { flagInk, gapLabel } from '../lib/gaps';
import { asApiError, useHealth } from '../lib/hooks';
import { usePdfDocument } from '../lib/pdf';
import { Sequencer } from '../lib/sequencer';
import { TYPE_GROUPS, typeStyle } from '../lib/types';

const PAGE_SIZE = 100;
const SUMMARY_POLL_MS = 2000;

const isAbort = (e: unknown) => e instanceof DOMException && e.name === 'AbortError';

/** Labels & inventory (prototype Main): metadata and the document summary; region types and open issues with
 * counts; the searchable region list; the selected region on its page. */
export function LabelsPage({ detail }: { detail: JobDetail }) {
  const jobId = detail.job.id;
  const health = useHealth();
  const llmOn = health?.llm.enabled === true;
  const labelled = detail.stats !== null && !detail.job.running_op;

  // filters: the search text (debounced), one type group or one gap code
  const [q, setQ] = useState('');
  const dq = useDebounced(q.trim());
  const [typeKey, setTypeKey] = useState('all');
  const [gap, setGap] = useState<string | null>(null);
  const query = useMemo<RegionQuery>(() => {
    const group = TYPE_GROUPS.find((g) => g.key === typeKey);
    return { q: dq || undefined, type: group && !gap ? [...group.types] : undefined, gap: gap ? [gap] : undefined };
  }, [dq, typeKey, gap]);
  const queryKey = JSON.stringify(query);

  // the list (cursor paging) and the counts, each latest-request-wins
  const listSeq = useRef(new Sequencer());
  const facetSeq = useRef(new Sequencer());
  const [rows, setRows] = useState<RegionRow[]>([]);
  const [total, setTotal] = useState<number | null>(null);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [facets, setFacets] = useState<Facets | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  // a failed next page: no more automatic loading until the user retries (no request storm)
  const [moreError, setMoreError] = useState<ApiError | null>(null);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    if (!labelled) return;
    setLoading(true);
    setError(null);
    setMoreError(null);
    const q = JSON.parse(queryKey) as RegionQuery;
    listSeq.current
      .run(
        (signal) => api.regions(jobId, { ...q, limit: PAGE_SIZE }, signal),
        (page) => {
          setRows(page.items);
          setTotal(page.total);
          setCursor(page.next_cursor ?? null);
          setLoading(false);
        },
      )
      .catch((e: unknown) => {
        setLoading(false);
        setError(asApiError(e));
      });
    facetSeq.current.run((signal) => api.facets(jobId, q, signal), setFacets).catch((e: unknown) => setError(asApiError(e)));
  }, [jobId, queryKey, labelled, tick]);

  useEffect(() => {
    const list = listSeq.current;
    const counts = facetSeq.current;
    return () => {
      list.cancel();
      counts.cancel();
    };
  }, []);

  const more = useCallback(() => {
    if (!cursor || loading) return;
    const t = listSeq.current.follow();
    setLoading(true);
    setMoreError(null);
    const q = JSON.parse(queryKey) as RegionQuery;
    api.regions(jobId, { ...q, limit: PAGE_SIZE, cursor }, t.signal).then(
      (page) => {
        if (!t.isLatest()) return;
        setRows((old) => [...old, ...page.items]);
        setCursor(page.next_cursor ?? null);
        setLoading(false);
      },
      (e: unknown) => {
        if (!t.isLatest() || isAbort(e)) return;
        setLoading(false);
        setMoreError(asApiError(e));
      },
    );
  }, [cursor, loading, jobId, queryKey]);

  // the page view of the selected row (or of the page picked with the arrows)
  const pdf = usePdfDocument(jobId);
  const statPages = typeof detail.stats?.pages === 'number' ? detail.stats.pages : null;
  const pageCount = pdf.doc?.numPages ?? statPages ?? 1;
  const [pageNo, setPageNo] = useState(1);
  const [selected, setSelected] = useState<RegionRow | null>(null);
  const [view, setView] = useState<PageView | null>(null);
  const [viewError, setViewError] = useState<ApiError | null>(null);
  const [viewTick, setViewTick] = useState(0);

  useEffect(() => {
    if (!labelled) return;
    const ac = new AbortController();
    setViewError(null);
    api.page(jobId, pageNo, ac.signal).then(setView, (e: unknown) => isAbort(e) || setViewError(asApiError(e)));
    return () => ac.abort();
  }, [jobId, pageNo, labelled, viewTick]);

  const pick = (r: RegionRow) => {
    setSelected(r);
    setPageNo(r.page_no);
  };
  const go = (n: number) => {
    setSelected(null);
    setPageNo(Math.min(Math.max(1, n), pageCount));
  };
  const shown = view && view.page_no === pageNo ? view : null;
  const selId = selected && selected.page_no === pageNo ? selected.region_id : null;
  const selRegion = shown?.regions.find((r) => r.region_id === selId) ?? null;
  const match = selected?.match ? asBox(selected.match.bbox) : null;
  const selGaps = shown?.gaps.filter((g) => g.region_id && g.region_id === selId) ?? [];

  const groupLabel = gap ? gapLabel(gap) : (TYPE_GROUPS.find((g) => g.key === typeKey)?.label ?? 'All');
  return (
    <>
      <HeaderSlot>
        <label className="search">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
            <circle cx="11" cy="11" r="7" />
            <path d="M20 20l-3.5-3.5" />
          </svg>
          <span className="sr-only">Search region text</span>
          <input
            type="search"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search region text, e.g. overdue, liquidity, Income Statements"
            data-testid="search"
          />
        </label>
      </HeaderSlot>

      <section className="inv-top">
        <MetadataCard detail={detail} />
        {llmOn && <SummaryCard jobId={jobId} jobStatus={detail.job.status} />}
      </section>

      <section className="page inv">
        <FacetsPanel
          facets={facets}
          typeKey={typeKey}
          gap={gap}
          onType={(k) => {
            setTypeKey(k);
            setGap(null);
          }}
          onGap={(code) => {
            setGap(code);
            setTypeKey('all');
          }}
        />

        <main className="card inv-main">
          <div className="inv-head">
            <div className="inv-title" data-testid="list-title">
              {groupLabel}
            </div>
            <div className="muted inv-hint">{dq ? `Search: "${dq}"` : 'Click a row to locate it on the page'}</div>
            <div style={{ flex: 1 }} />
            <div className="muted inv-hint" data-testid="list-total">
              {total === null ? '' : `${total.toLocaleString('en-US')} region${total === 1 ? '' : 's'}`}
            </div>
          </div>
          {error && (
            <div style={{ padding: 12 }}>
              <ErrorBanner error={error} onRetry={() => setTick((t) => t + 1)} />
            </div>
          )}
          {!labelled && <div className="inv-empty">The regions appear when labelling is done.</div>}
          {labelled && total === 0 && !loading && <div className="inv-empty">No matching regions</div>}
          {labelled && (
            <RegionList
              rows={rows}
              hasMore={cursor !== null}
              loading={loading}
              moreError={moreError}
              selected={selId}
              onPick={pick}
              onMore={more}
            />
          )}
        </main>

        <aside className="inv-view">
          <div className="card inv-page">
            <div className="viewer-bar">
              <button type="button" className="btn" aria-label="Previous page" onClick={() => go(pageNo - 1)} disabled={pageNo <= 1}>
                {'←'}
              </button>
              <button type="button" className="btn" aria-label="Next page" onClick={() => go(pageNo + 1)} disabled={pageNo >= pageCount}>
                {'→'}
              </button>
              <label className="page-input">
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
            </div>
            {viewError && (
              <div style={{ padding: 12 }}>
                <ErrorBanner error={viewError} onRetry={() => setViewTick((t) => t + 1)} />
              </div>
            )}
            <div className="viewer-stage inv-stage">
              <PdfPage doc={pdf.doc} pageNo={pageNo} width={shown?.width} height={shown?.height} error={pdf.error}>
                {shown && <Overlay view={shown} selected={selId} match={selId ? match : null} />}
              </PdfPage>
            </div>
          </div>
          {selected && selected.page_no === pageNo && (
            <div className="card card-pad" data-testid="region-detail">
              <div className="kv">
                <div>Type</div>
                <div>{typeStyle(selected.type).label}</div>
                <div>Confidence</div>
                <div>{selected.score === null ? '—' : selected.score.toFixed(2)}</div>
                <div>Region id</div>
                <div className="mono" style={{ fontSize: 12, overflowWrap: 'anywhere' }}>
                  {selected.region_id}
                </div>
                <div>Flag</div>
                <div style={{ color: flagInk(selected.gaps[0]) }} data-testid="region-flag">
                  {selGaps.length
                    ? selGaps.map((g) => g.detail).join('; ')
                    : selected.gaps.length
                      ? selected.gaps.map(gapLabel).join('; ')
                      : '—'}
                </div>
                <div>Status</div>
                <div>{selected.status ?? 'labelled'}</div>
              </div>
              {(selRegion?.text ?? selected.text_preview) && <div className="text-box">{selRegion?.text ?? selected.text_preview}</div>}
            </div>
          )}
        </aside>
      </section>
    </>
  );
}

function num(v: unknown): number | null {
  return typeof v === 'number' && Number.isFinite(v) ? v : null;
}

function seconds(s: number): string {
  if (s < 90) return `${Math.round(s * 10) / 10} s`;
  return `${Math.round(s / 60)} min`;
}

/** The labelling stats (label/stats.json). */
function MetadataCard({ detail }: { detail: JobDetail }) {
  const st = detail.stats ?? {};
  const pages = num(st.pages);
  const kinds = st.pages_by_kind && typeof st.pages_by_kind === 'object' ? (st.pages_by_kind as Record<string, unknown>) : {};
  const kindText = Object.entries(kinds)
    .filter(([, n]) => num(n))
    .map(([k, n]) => `${k[0].toUpperCase()}${k.slice(1)} ${String(n)}`)
    .join(' · ');
  const label = num(st.label_seconds);
  const est = num(st.estimated_extract_seconds);
  const regions = num(st.regions);
  return (
    <div className="card card-pad" data-testid="metadata">
      <div className="eyebrow">Metadata</div>
      {detail.stats === null ? (
        <div className="muted" style={{ fontSize: 13 }}>
          Available when labelling is done.
        </div>
      ) : (
        <div className="kv">
          <div>Pages</div>
          <div>{pages ?? '—'}</div>
          <div>Page kinds</div>
          <div>{kindText || '—'}</div>
          <div>Regions</div>
          <div>{regions === null ? '—' : regions.toLocaleString('en-US')}</div>
          <div>Labeling time</div>
          <div>{label === null ? '—' : seconds(label)}</div>
          <div>Est. extraction</div>
          <div>{est === null ? '—' : `~${seconds(est)}`}</div>
        </div>
      )}
    </div>
  );
}

/** The document summary (GET /summary; only rendered when the LLM is enabled). Asked again when the job's status
 * changes (a page opened while labelling asked before the summary existed) and polled while it is pending. */
function SummaryCard({ jobId, jobStatus }: { jobId: string; jobStatus: string }) {
  const [s, setS] = useState<Summary | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    const ac = new AbortController();
    api.summary(jobId, ac.signal).then(
      (v) => {
        setS(v);
        setError(null);
      },
      (e: unknown) => isAbort(e) || setError(asApiError(e)),
    );
    return () => ac.abort();
  }, [jobId, jobStatus, tick]);
  const pending = s?.status === 'pending';
  useEffect(() => {
    if (!pending) return;
    const t = window.setTimeout(() => setTick((n) => n + 1), SUMMARY_POLL_MS);
    return () => window.clearTimeout(t);
  }, [pending, s]);

  return (
    <div className="card card-pad summary-card" data-testid="summary-card">
      <div className="summary-head">
        <div className="eyebrow">Document summary</div>
        {s && <LlmBadge>{`${s.status === 'done' ? 'Generated by' : 'Model'} ${s.model} · low weight · for reference`}</LlmBadge>}
        {s?.remote && (s.status === 'done' || s.status === 'pending') && (
          <span className="muted" style={{ fontSize: 11 }}>
            Remote model: text was sent to an external service
          </span>
        )}
      </div>
      {error && <ErrorBanner error={error} onRetry={() => setTick((n) => n + 1)} />}
      {s?.status === 'done' && s.text && (
        <p className="summary-text" data-testid="summary-text">
          {s.text}
        </p>
      )}
      {s?.status === 'pending' && <div className="muted summary-note">The summary is being written…</div>}
      {s?.status === 'failed' && (
        <div className="summary-note" style={{ color: 'var(--warn-ink)' }}>
          The summary could not be made; an llm_failed gap records why. The rest of the job is not affected.
        </div>
      )}
      {s?.status === 'off' && (
        <div className="muted summary-note">No summary for this job: it was labelled while the summary was off.</div>
      )}
    </div>
  );
}
