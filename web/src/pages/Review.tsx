import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  api,
  type ApiError,
  type Cluster,
  type JobDetail,
  type LlmItem,
  type PageView,
  type QueueItem,
  type QueueView,
  type ReviewOp,
  type ReviewRow,
} from '../api/client';
import { clusterBasis, ClusterCard } from '../components/ClusterCard';
import { ErrorBanner } from '../components/ErrorBanner';
import { LlmBadge } from '../components/LlmBadge';
import { Overlay } from '../components/Overlay';
import { PdfPage } from '../components/PdfPage';
import { asBox } from '../lib/boxes';
import { gapLabel, groupDot } from '../lib/gaps';
import { asApiError, useHealth } from '../lib/hooks';
import { useName } from '../lib/name';
import { usePdfDocument } from '../lib/pdf';
import { REGION_TYPES, typeStyle } from '../lib/types';

/** Shown items are reported in one POST /queue/seen per pause of this length. */
const SEEN_FLUSH_MS = 400;

const isAbort = (e: unknown) => e instanceof DOMException && e.name === 'AbortError';

/** The page of a region id (`{doc_id}:p{page:04d}:r{n:03d}`, contract v2), for cluster members. */
export function pageOfRegion(regionId: string): number | null {
  const m = /:p(\d+):r\d+$/.exec(regionId);
  return m ? Number(m[1]) : null;
}

/** What to look at: a queue item, or a member of a similar group opened from the left column. */
type Mode = { kind: 'queue'; group: string; index: number } | { kind: 'cluster'; cluster: Cluster; index: number };

interface Current {
  key: string;
  page: number | null;
  regionId: string | null;
  item: QueueItem | null;
}

const ITEM_TITLES: Record<string, string> = {
  table_suspected: 'Suspected missed table',
  low_confidence: 'Low confidence',
  table_words_unassigned: 'Unassigned values in a table',
  table_unmatched: 'Table not matched between stages',
  unhandled: 'Unhandled region',
  no_text_layer: 'No text layer',
  lang_mismatch: 'Language differs from the declaration',
  extractor_degraded: 'Extractor degraded',
  llm_failed: 'LLM call failed',
  continued_hint: 'Continuation hint (weak)',
  llm_check: 'Chart description to check',
};

function itemTitle(item: QueueItem): string {
  const base = ITEM_TITLES[item.group] ?? gapLabel(item.group);
  return item.page_no ? `${base} · p${item.page_no}` : base;
}

/** Review inbox (prototype Review): the queue grouped by problem, similar groups with bulk actions, the current
 * item on its page, the issue and the actions (reclassify, exclude, ack). */
export function ReviewPage({ detail }: { detail: JobDetail }) {
  const jobId = detail.job.id;
  const jobStatus = detail.job.status;
  // the server takes review ops only in review: elsewhere the page is read-only (no action that must be refused)
  const reviewOpen = jobStatus === 'in_review';
  const health = useHealth();
  const llmOn = health?.llm.enabled === true;
  const pdf = usePdfDocument(jobId);

  const [queue, setQueue] = useState<QueueView | null>(null);
  const [clusters, setClusters] = useState<Cluster[]>([]);
  const [ops, setOps] = useState<ReviewRow[]>([]);
  const [error, setError] = useState<ApiError | null>(null);
  const [tick, setTick] = useState(0);
  const reload = useCallback(() => setTick((t) => t + 1), []);

  useEffect(() => {
    const ac = new AbortController();
    Promise.all([api.queue(jobId, ac.signal), api.clusters(jobId, ac.signal), api.reviewOps(jobId, ac.signal)]).then(
      ([q, c, r]) => {
        setQueue(q);
        setClusters(c.clusters);
        setOps(r.ops);
        setError(null);
      },
      (e: unknown) => isAbort(e) || setError(asApiError(e)),
    );
    return () => ac.abort();
  }, [jobId, tick, detail.job.status]);

  // the current group (first with something open) and item
  const [mode, setMode] = useState<Mode | null>(null);
  useEffect(() => {
    if (!queue || mode) return;
    const g = queue.groups.find((x) => x.items.some((i) => i.status !== 'resolved')) ?? queue.groups[0];
    if (g) setMode({ kind: 'queue', group: g.key, index: 0 });
  }, [queue, mode]);

  const group = mode?.kind === 'queue' ? (queue?.groups.find((g) => g.key === mode.group) ?? null) : null;
  const count = mode?.kind === 'cluster' ? mode.cluster.members.length : (group?.items.length ?? 0);
  const index = mode ? Math.min(mode.index, Math.max(0, count - 1)) : 0;
  const current = useMemo<Current | null>(() => {
    if (!mode) return null;
    if (mode.kind === 'cluster') {
      const rid = mode.cluster.members[index];
      return rid ? { key: `member:${rid}`, page: pageOfRegion(rid), regionId: rid, item: null } : null;
    }
    const item = group?.items[index];
    return item ? { key: item.item_id, page: item.page_no, regionId: item.region_id, item } : null;
  }, [mode, group, index]);

  const step = useCallback(
    (d: number) => {
      if (!mode || count === 0) return;
      setMode({ ...mode, index: (index + d + count) % count });
    },
    [mode, index, count],
  );

  // ← → move through the items (not while typing in a field)
  useEffect(() => {
    const on = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable)) return;
      if (e.altKey || e.ctrlKey || e.metaKey) return;
      if (e.key === 'ArrowRight') {
        e.preventDefault();
        step(1);
      } else if (e.key === 'ArrowLeft') {
        e.preventDefault();
        step(-1);
      }
    };
    window.addEventListener('keydown', on);
    return () => window.removeEventListener('keydown', on);
  }, [step]);

  // every item shown is reported seen once, in batches
  const reported = useRef(new Set<string>());
  const pending = useRef(new Set<string>());
  const timer = useRef<number | null>(null);
  const flush = useCallback(() => {
    timer.current = null;
    const ids = [...pending.current];
    pending.current.clear();
    if (!ids.length) return;
    api.seen(jobId, ids).catch((e: unknown) => {
      for (const id of ids) reported.current.delete(id);
      setError(asApiError(e));
    });
  }, [jobId]);
  const shownItem = current?.item ?? null;
  useEffect(() => {
    if (!shownItem || shownItem.status !== 'open' || reported.current.has(shownItem.item_id)) return;
    reported.current.add(shownItem.item_id);
    pending.current.add(shownItem.item_id);
    if (timer.current === null) timer.current = window.setTimeout(flush, SEEN_FLUSH_MS);
  }, [shownItem, flush]);
  useEffect(
    () => () => {
      if (timer.current !== null) window.clearTimeout(timer.current);
      flush();
    },
    [flush],
  );

  // the page of the current item
  const [view, setView] = useState<PageView | null>(null);
  const [viewError, setViewError] = useState<ApiError | null>(null);
  const page = current?.page ?? null;
  useEffect(() => {
    if (!page) return;
    const ac = new AbortController();
    setViewError(null);
    api.page(jobId, page, ac.signal).then(setView, (e: unknown) => isAbort(e) || setViewError(asApiError(e)));
    return () => ac.abort();
  }, [jobId, page, tick]);
  const shown = view && page && view.page_no === page ? view : null;

  // LLM outputs of the current region (only when the LLM is enabled)
  const [llm, setLlm] = useState<{ regionId: string; items: LlmItem[] } | null>(null);
  const regionId = current?.regionId ?? null;
  useEffect(() => {
    if (!llmOn || !regionId) return;
    const ac = new AbortController();
    api.llm(jobId, regionId, ac.signal).then(
      (v) => setLlm({ regionId, items: v.items }),
      (e: unknown) => isAbort(e) || setError(asApiError(e)),
    );
    return () => ac.abort();
  }, [jobId, regionId, llmOn, tick]);
  const llmItems = llmOn && llm && llm.regionId === regionId ? llm.items : [];

  // actions: every user action is one POST /review, by the reviewer named here (as "Approved by" on the workflow)
  const [by, setBy, rememberBy] = useName();
  const reviewer = by.trim();
  const [busy, setBusy] = useState(false);
  const blocked = busy || !reviewer;
  const [note, setNote] = useState('');
  const [to, setTo] = useState('table');
  const [results, setResults] = useState<Record<string, { cluster: Cluster; text: string }>>({});
  useEffect(() => setNote(''), [current?.key]);

  const send = async (opsToSend: ReviewOp[]): Promise<boolean> => {
    setBusy(true);
    try {
      await api.review(jobId, reviewer, opsToSend);
      rememberBy();
      setError(null);
      reload();
      return true;
    } catch (e) {
      setError(asApiError(e));
      return false;
    } finally {
      setBusy(false);
    }
  };
  const noted = () => note.trim() || null;
  const reclassify = (rid: string, type: string) => send([{ action: 'reclassify', region_id: rid, to: type, note: noted() }]);
  const exclude = (rid: string) => send([{ action: 'exclude', region_id: rid, note: noted() }]);
  const ack = (item: QueueItem, verdict: 'ok' | 'wrong') => send([{ action: 'ack', item_id: item.item_id, verdict, note: noted() }]);

  /** The regions of the job that are excluded or absorbed now (left out of a bulk action). */
  const goneRegions = async (): Promise<Set<string>> => {
    const out = new Set<string>();
    let cursor: string | null = null;
    do {
      const p = await api.regions(jobId, { status: ['excluded', 'absorbed'], limit: 500, cursor: cursor ?? undefined });
      for (const r of p.items) out.add(r.region_id);
      cursor = p.next_cursor ?? null;
    } while (cursor);
    return out;
  };
  const excludeAll = async (c: Cluster) => {
    setBusy(true);
    try {
      const gone = await goneRegions();
      const members = c.members.filter((id) => !gone.has(id));
      if (!members.length) {
        setResults((r) => ({ ...r, [c.cluster_id]: { cluster: c, text: 'Nothing left to exclude' } }));
        return;
      }
      await api.review(
        jobId,
        reviewer,
        members.map((id) => ({ action: 'exclude', region_id: id, note: null })),
      );
      rememberBy();
      setError(null);
      setResults((r) => ({ ...r, [c.cluster_id]: { cluster: c, text: `${members.length} excluded` } }));
      reload();
    } catch (e) {
      setError(asApiError(e));
    } finally {
      setBusy(false);
    }
  };

  const total = queue?.total ?? 0;
  const done = queue?.done ?? 0;
  const pct = total ? Math.round((done / total) * 100) : 0;
  const doneClusters = Object.entries(results).filter(([id]) => !clusters.some((c) => c.cluster_id === id));

  const region = shown?.regions.find((r) => r.region_id === regionId) ?? null;
  const item = current?.item ?? null;
  const resolved = item?.status === 'resolved' || (mode?.kind === 'cluster' && (region?.status === 'excluded' || region?.status === 'absorbed'));
  const table = region?.table_id ? (shown?.tables.find((t) => t.table_id === region.table_id) ?? null) : null;
  const absorbed = table ? (shown?.regions.filter((r) => r.absorbed_into === table.table_id) ?? []) : [];
  const box = asBox(item?.bbox ?? region?.bbox);
  const findItem = item?.code === 'table_suspected';
  const focusColor = item?.group === 'llm_check' ? 'var(--type-chart)' : item?.kind === 'hint' ? 'var(--type-table)' : item?.code === 'low_confidence' ? 'var(--call-ink)' : 'var(--warn-ink)';
  const tag = region ? `${typeStyle(region.type).label}${region.subtype ? ` · ${region.subtype}` : ''}${region.score !== null ? ` ${region.score.toFixed(2)}` : ''}` : '';

  let title = '';
  let detailText = '';
  if (item) {
    title = itemTitle(item);
    // an LLM item's detail repeats the description shown in the panel below
    detailText =
      item.kind === 'llm' && llmItems.length > 0
        ? 'The description below was generated by the LLM; check that it matches the chart.'
        : item.detail;
  } else if (mode?.kind === 'cluster') {
    title = mode.cluster.label;
    detailText = `${clusterBasis(mode.cluster)}. Member ${index + 1} of ${mode.cluster.members.length}: ${regionId ?? ''}`;
  }

  return (
    <section className="page review">
      <aside className="rv-side">
        <div className="card facet-card" data-testid="queue-groups">
          <div className="rv-head">
            <div className="eyebrow">To review</div>
            <div className="muted" style={{ fontSize: 12 }} data-testid="queue-done">
              Done {done} / {total}
            </div>
          </div>
          <div className="progress" aria-label={`${pct}% done`}>
            <div style={{ width: `${pct}%` }} />
          </div>
          {queue && queue.groups.length === 0 && <div className="muted facet-empty">Nothing to review.</div>}
          {queue?.groups.map((g) => {
            const on = mode?.kind === 'queue' && mode.group === g.key;
            return (
              <button
                key={g.key}
                type="button"
                className={`facet${on ? ' on' : ''}`}
                aria-pressed={on}
                data-testid="queue-group"
                data-group={g.key}
                onClick={() => setMode({ kind: 'queue', group: g.key, index: 0 })}
              >
                <span className="facet-label">
                  <span className="dot" style={{ background: groupDot(g.key) }} />
                  {g.label}
                </span>
                <span className="mono facet-count" data-testid="queue-group-count">
                  {g.resolved}/{g.total}
                </span>
              </button>
            );
          })}
        </div>

        <div className="card facet-card rv-clusters" data-testid="clusters">
          <div className="eyebrow facet-head">
            Similar groups · bulk actions{clusters.length > 0 && <span className="muted"> · {clusters.length}</span>}
          </div>
          {clusters.length === 0 && doneClusters.length === 0 && <div className="muted facet-empty">No similar groups.</div>}
          <div className="rv-cluster-list" data-testid="cluster-list">
          {clusters.map((c) => (
            <ClusterCard
              key={c.cluster_id}
              cluster={c}
              result={results[c.cluster_id]?.text}
              busy={busy}
              excludeBlocked={blocked}
              open={mode?.kind === 'cluster' && mode.cluster.cluster_id === c.cluster_id}
              onExcludeAll={reviewOpen ? () => void excludeAll(c) : null}
              onOpen={() => setMode({ kind: 'cluster', cluster: c, index: 0 })}
            />
          ))}
          {doneClusters.map(([id, r]) => (
            <ClusterCard key={id} cluster={r.cluster} result={r.text} busy={busy} excludeBlocked open={false} onExcludeAll={null} onOpen={() => {}} />
          ))}
          </div>
        </div>
      </aside>

      <main className="card rv-main">
        <div className="viewer-bar">
          <button type="button" className="btn" aria-label="Previous item" onClick={() => step(-1)} disabled={count < 2}>
            {'←'}
          </button>
          <button type="button" className="btn" aria-label="Next item" onClick={() => step(1)} disabled={count < 2}>
            {'→'}
          </button>
          <div className="rv-pos" data-testid="item-pos">
            {mode?.kind === 'cluster' ? mode.cluster.label : (group?.label ?? 'Review')}
            {count > 0 ? ` · item ${index + 1} of ${count}` : ''}
          </div>
          {mode?.kind === 'cluster' && (
            <button type="button" className="btn btn-sm" onClick={() => setMode(null)}>
              Back to the queue
            </button>
          )}
          <div style={{ flex: 1 }} />
          <div className="muted" style={{ fontSize: 12 }}>
            {page ? `Page ${page}` : ''}
          </div>
        </div>
        {(error || viewError) && (
          <div style={{ padding: 12, display: 'flex', flexDirection: 'column', gap: 8 }}>
            {error && <ErrorBanner error={error} onRetry={reload} onDismiss={() => setError(null)} />}
            {viewError && <ErrorBanner error={viewError} onRetry={reload} />}
          </div>
        )}
        <div className="viewer-stage">
          {page ? (
            <PdfPage doc={pdf.doc} pageNo={page} width={shown?.width} height={shown?.height} error={pdf.error}>
              {shown && (
                <Overlay
                  view={shown}
                  selected={regionId}
                  showAbsorbed
                  focus={box && !findItem ? { box, tag, color: focusColor, done: resolved } : null}
                />
              )}
            </PdfPage>
          ) : (
            <div className="muted inv-empty">{queue && count === 0 ? 'Nothing to show.' : current ? 'This item is not on a page.' : ''}</div>
          )}
        </div>
      </main>

      <aside className="rv-right">
        {reviewOpen ? (
          <div className="card card-pad" data-testid="reviewer">
            <label className="field">
              Reviewer
              <input className="name" type="text" required value={by} onChange={(e) => setBy(e.target.value)} />
            </label>
            {!reviewer && (
              <div className="muted" style={{ fontSize: 12 }}>
                Enter your name to review; it is recorded with each action.
              </div>
            )}
          </div>
        ) : (
          <div className="card card-pad muted" style={{ fontSize: 13 }} data-testid="review-closed">
            {['confirmed', 'delivered'].includes(jobStatus)
              ? `Read only: this job is ${jobStatus}, so review is closed.`
              : 'Read only: review opens when extraction is done and the job is in review.'}
          </div>
        )}
        <div className="card card-pad" data-testid="issue">
          <div className="eyebrow">Issue</div>
          <div className="rv-title" data-testid="issue-title">
            {title || (queue ? 'Nothing selected' : 'Loading…')}
          </div>
          {detailText && <div className="rv-detail">{detailText}</div>}
          {llmItems.length > 0 && <LlmPanel items={llmItems} />}
          {resolved && (
            <div className="banner-ok rv-resolved" data-testid="resolved">
              {item ? resolutionText(item, ops, region?.type ?? null, table?.table_id ?? null, absorbed.map((r) => r.region_id)) : 'Excluded.'}
            </div>
          )}
        </div>

        {reviewOpen && (item || regionId) && (
          <div className="card card-pad" data-testid="actions">
            <div className="eyebrow">Actions</div>
            {!resolved && (
              <ItemActions
                item={item}
                regionType={region?.type ?? null}
                regionStatus={region?.status ?? null}
                busy={blocked}
                onReclassify={(t) => regionId && void reclassify(regionId, t)}
                onExclude={() => regionId && void exclude(regionId)}
                onAck={(v) => item && void ack(item, v)}
              />
            )}
            {!resolved && regionId && region?.status !== 'excluded' && region?.status !== 'absorbed' && (
              <div className="rv-reclass">
                <label className="field" style={{ flex: 1 }}>
                  <span>Reclassify as</span>
                  <select value={to} onChange={(e) => setTo(e.target.value)} aria-label="New type">
                    {REGION_TYPES.map((t) => (
                      <option key={t} value={t}>
                        {typeStyle(t).label}
                      </option>
                    ))}
                  </select>
                </label>
                <button type="button" className="btn" disabled={blocked || region?.type === to} onClick={() => regionId && void reclassify(regionId, to)}>
                  Reclassify
                </button>
              </div>
            )}
            {!resolved && (
              <label className="field">
                <span>Note (optional)</span>
                <input type="text" value={note} onChange={(e) => setNote(e.target.value)} aria-label="Note" />
              </label>
            )}
            <div className="muted" style={{ fontSize: 12, lineHeight: 1.5 }}>
              You can only reclassify or exclude regions; content comes from the source file and cannot be edited here.
            </div>
          </div>
        )}
      </aside>
    </section>
  );
}

interface ActionProps {
  item: QueueItem | null;
  regionType: string | null;
  regionStatus: string | null | undefined;
  busy: boolean;
  onReclassify: (to: string) => void;
  onExclude: () => void;
  onAck: (verdict: 'ok' | 'wrong') => void;
}

/** The actions of an item by its kind (prototype Review): the first one is the primary button. */
function ItemActions({ item, regionType, regionStatus, busy, onReclassify, onExclude, onAck }: ActionProps) {
  const acts: { label: string; run: () => void; test: string }[] = [];
  const canEdit = regionStatus !== 'excluded' && regionStatus !== 'absorbed';
  if (item?.kind === 'llm') {
    acts.push({ label: 'Description is fine', run: () => onAck('ok'), test: 'ack-ok' });
    acts.push({ label: 'Description is wrong', run: () => onAck('wrong'), test: 'ack-wrong' });
    if (canEdit && regionType !== 'picture') acts.push({ label: 'Reclassify as picture', run: () => onReclassify('picture'), test: 'reclassify-picture' });
  } else if (item?.kind === 'hint') {
    acts.push({ label: 'Hint is right', run: () => onAck('ok'), test: 'ack-ok' });
    acts.push({ label: 'Hint is wrong', run: () => onAck('wrong'), test: 'ack-wrong' });
  } else if (item) {
    if (item.code === 'table_suspected' && item.region_id && canEdit) {
      acts.push({ label: 'Reclassify as table', run: () => onReclassify('table'), test: 'reclassify-table' });
    }
    acts.push({ label: 'Mark seen, no change', run: () => onAck('ok'), test: 'ack-ok' });
    acts.push({ label: 'The flag is wrong', run: () => onAck('wrong'), test: 'ack-wrong' });
    if (item.region_id && canEdit) acts.push({ label: 'Exclude this region', run: onExclude, test: 'exclude' });
  } else if (canEdit) {
    acts.push({ label: 'Exclude this region', run: onExclude, test: 'exclude' });
  }
  return (
    <>
      {acts.map((a, i) => (
        <button
          key={a.test}
          type="button"
          className={`btn rv-act${i === 0 ? ' btn-primary' : ''}`}
          disabled={busy}
          onClick={a.run}
          data-testid={`act-${a.test}`}
        >
          {a.label}
        </button>
      ))}
    </>
  );
}

/** What review did to an item (its resolution, and the review row it names). */
function resolutionText(item: QueueItem, ops: ReviewRow[], type: string | null, tableId: string | null, absorbed: string[]): string {
  const r = item.resolution;
  if (!r) return 'Resolved.';
  const row = ops[r.review_seq - 1];
  switch (r.action) {
    case 'reclassify': {
      const to = row?.to ?? type;
      let s = `Reclassified as ${to ? typeStyle(to).label.toLowerCase() : 'another type'}`;
      if (tableId) s += ` (${tableId})`;
      if (absorbed.length) s += `; ${absorbed.length} region${absorbed.length === 1 ? '' : 's'} absorbed: ${absorbed.join(', ')}`;
      return `${s}; resolved.`;
    }
    case 'exclude':
      return 'Region excluded; resolved.';
    case 'ack': {
      const wrong = row?.verdict === 'wrong';
      const what = item.kind === 'llm' ? 'Description' : item.kind === 'hint' ? 'Hint' : 'Flag';
      const s = wrong ? `${what} marked wrong.` : item.kind === 'gap' ? 'Marked seen, no change.' : `${what} checked.`;
      return row?.note ? `${s} Note: ${row.note}` : s;
    }
    default:
      return `Resolved (${r.action}).`;
  }
}

/** The LLM outputs of the region: description and chart table (purple, low weight). */
function LlmPanel({ items }: { items: LlmItem[] }) {
  return (
    <>
      {items.map((x, i) => {
        const out = x.output as { text?: unknown; table?: unknown };
        const table = out.table && typeof out.table === 'object' ? (out.table as { title?: unknown; columns?: unknown; rows?: unknown }) : null;
        const columns = Array.isArray(table?.columns) ? (table.columns as unknown[]).map(String) : [];
        const rows = Array.isArray(table?.rows) ? (table.rows as unknown[]).filter(Array.isArray).map((r) => (r as unknown[]).map((c) => (c === null ? '' : String(c)))) : [];
        return (
          <div key={`${x.kind}-${i}`} className="llm-panel" data-testid="llm-panel" data-kind={x.kind}>
            <LlmBadge>{`${x.model} ${x.kind === 'chart_table' ? 'chart table (experimental)' : 'description'} · low weight`}</LlmBadge>
            {typeof out.text === 'string' && <div style={{ fontSize: 13, lineHeight: 1.5 }}>{out.text}</div>}
            {table && (
              <div className="table-scroll">
                {typeof table.title === 'string' && <div style={{ fontSize: 12, fontWeight: 500 }}>{table.title}</div>}
                <table className="llm-table" data-testid="llm-table">
                  {columns.length > 0 && (
                    <thead>
                      <tr>
                        {columns.map((c, j) => (
                          <th key={j}>{c}</th>
                        ))}
                      </tr>
                    </thead>
                  )}
                  <tbody>
                    {rows.map((r, j) => (
                      <tr key={j}>
                        {r.map((c, k) => (
                          <td key={k}>{c}</td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        );
      })}
    </>
  );
}
