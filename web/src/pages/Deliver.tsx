import { useEffect, useState } from 'react';
import { api, type ApiError, type Gap, type JobDetail } from '../api/client';
import { flowApi, type Facets, type LlmView, type PackageEntry, type ReviewRow } from '../api/flow';
import { ErrorBanner } from '../components/ErrorBanner';
import { retryDelay } from '../lib/backoff';
import { gapCounts, opLines, packageFiles } from '../lib/deliver';
import { asApiError, useHealth } from '../lib/hooks';
import { useName } from '../lib/name';
import { jobHref } from '../lib/route';
import '../styles/flow.css';

const READY = ['in_review', 'confirmed', 'delivered'];
const STATUS_ORDER = ['extracted', 'discarded', 'recorded', 'excluded', 'unhandled', 'absorbed'];
const fmt = (n: number) => n.toLocaleString('en-US');
const isAbort = (e: unknown) => e instanceof DOMException && e.name === 'AbortError';
/** After Confirm the job is watched this long for the delivery (the job may still read confirmed / idle). */
export const DELIVERY_WATCH_MS = 10 * 60_000;

interface Data {
  facets: Facets;
  gaps: Gap[];
  ops: ReviewRow[];
  llm: LlmView | null;
  pkg: PackageEntry | null;
}

/** Confirm & deliver: KPIs, open vs resolved issues per gap code, the human actions, the package file list, and
 * Confirm (POST /confirm); once delivered, the package and its contract check (GET /v1/packages). */
export function DeliverPage({ detail, onChanged }: { detail: JobDetail; onChanged: () => void }) {
  const job = detail.job;
  const jobId = job.id;
  const health = useHealth();
  const llmEnabled = !!health?.llm.enabled;
  const ready = READY.includes(job.status);
  const [data, setData] = useState<Data | null>(null);
  const [loadError, setLoadError] = useState<ApiError | null>(null);
  const [tick, setTick] = useState(0);
  const [by, setBy, rememberBy] = useName();
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  // after a Confirm here: the job is asked again (with backoff) while it is confirmed, idle and without a package,
  // so a reload that sees it before the delivery step starts does not leave the page on "not delivered yet"
  const [watchSince, setWatchSince] = useState<number | null>(null);
  const [watchTry, setWatchTry] = useState(0);
  const awaitingDelivery = watchSince !== null && job.status === 'confirmed' && !job.running_op && !job.package && !job.last_error;
  useEffect(() => {
    if (!awaitingDelivery || watchSince === null) return;
    if (Date.now() - watchSince > DELIVERY_WATCH_MS) {
      setWatchSince(null);
      return;
    }
    const t = window.setTimeout(() => {
      setWatchTry((n) => n + 1);
      onChanged();
    }, retryDelay(watchTry + 1));
    return () => window.clearTimeout(t);
  }, [awaitingDelivery, watchSince, watchTry, onChanged]);

  useEffect(() => {
    if (!ready || job.running_op) return;
    const ac = new AbortController();
    setLoadError(null);
    Promise.all([
      flowApi.facets(jobId, ac.signal),
      flowApi.gaps(jobId, ac.signal),
      flowApi.reviewOps(jobId, ac.signal),
      llmEnabled ? flowApi.llm(jobId, ac.signal) : Promise.resolve(null),
      job.status === 'delivered' ? flowApi.packages(ac.signal) : Promise.resolve(null),
    ]).then(
      ([facets, gaps, ops, llm, pkgs]) =>
        setData({
          facets,
          gaps: gaps.gaps,
          ops: ops.ops,
          llm,
          pkg: pkgs?.packages.find((p) => p.package === job.package || p.job_id === jobId) ?? null,
        }),
      (e: unknown) => isAbort(e) || setLoadError(asApiError(e)),
    );
    return () => ac.abort();
  }, [jobId, ready, job.status, job.running_op, job.package, llmEnabled, tick]);

  const confirm = async () => {
    if (!by.trim()) return;
    setSending(true);
    setError(null);
    try {
      await flowApi.confirm(jobId, by.trim());
      rememberBy();
      setWatchSince(Date.now());
      setWatchTry(0);
    } catch (e) {
      setError(asApiError(e));
    } finally {
      setSending(false);
      onChanged();
    }
  };

  const retry = async () => {
    setError(null);
    try {
      await api.retry(jobId);
    } catch (e) {
      setError(asApiError(e));
    } finally {
      onChanged();
    }
  };

  if (!ready) {
    return (
      <section className="page">
        <main className="card flow-main">
          <div className="card-head">
            <h1>Confirm & deliver</h1>
          </div>
          <div className="flow-msg">
            Confirmation opens once extraction is done and the job is in review.{' '}
            <a href={jobHref(jobId, job.approved_by ? 'progress' : 'workflow')}>{job.approved_by ? 'See the extraction' : 'Go to the workflow'}</a>
          </div>
        </main>
      </section>
    );
  }

  const facets = data?.facets ?? null;
  const delivered = job.status === 'delivered';
  const check = data?.pkg?.check ?? null;
  const counts = check?.counts as Record<string, unknown> | undefined;
  const gaps = data ? gapCounts(data.gaps) : [];
  const open = data ? data.gaps.filter((g) => !g.resolved).length : 0;
  const resolved = data ? data.gaps.length - open : 0;
  const llmItems = data?.llm?.items ?? [];
  const llmKinds = { summary: 0, describe: 0, chart_table: 0 };
  for (const it of llmItems) llmKinds[it.kind]++;
  const pages = typeof detail.stats?.pages === 'number' ? detail.stats.pages : null;
  const statuses = facets ? STATUS_ORDER.filter((s) => facets.status[s]).map((s) => `${s} ${fmt(facets.status[s])}`) : [];
  const tablesRows = counts && typeof counts['tables.jsonl'] === 'number' ? (counts['tables.jsonl'] as number) : null;
  const pkgName = job.package ?? `${job.doc_id}@${job.sha256.slice(0, 12)}`;
  const lines = data ? opLines(data.ops) : [];

  return (
    <section className="page">
      <main className="flow-main-stack">
        {loadError && <ErrorBanner error={loadError} onRetry={() => setTick((t) => t + 1)} />}
        <div className="kpis" data-testid="kpis">
          <Kpi id="regions" label="Regions" value={facets ? fmt(facets.total) : '…'} sub={statuses.length ? cap(statuses.join(' · ')) : ''} />
          <Kpi
            id="tables"
            label="Tables"
            value={facets ? fmt(facets.type.table ?? 0) : '…'}
            sub={tablesRows !== null ? `${fmt(tablesRows)} row${tablesRows === 1 ? '' : 's'} in tables.jsonl` : 'Table regions after review'}
          />
          {llmEnabled && (
            <Kpi
              id="llm"
              label="LLM outputs"
              value={data ? fmt(llmItems.length) : '…'}
              sub={`Summary ${llmKinds.summary} · descriptions ${llmKinds.describe} · chart tables ${llmKinds.chart_table} (llm.jsonl)`}
            />
          )}
          <Kpi id="open" label="Open issues" value={data ? fmt(open) : '…'} sub={`Resolved in review ${fmt(resolved)}; handled ones are marked resolved`} />
          <Kpi
            id="check"
            label="Contract check"
            value={check ? (check.ok ? 'OK' : 'Failed') : '—'}
            sub={check ? `contract v${check.contract_version}` : 'After delivery'}
          />
        </div>

        <div className="card" data-testid="gaps">
          <div className="list-head">Open issues (delivered with the package)</div>
          <div className="gap-row head">
            <span>Issue type</span>
            <span className="n">Open</span>
            <span className="r">Resolved</span>
          </div>
          {data && gaps.length === 0 && <div className="flow-msg">No issues recorded.</div>}
          {gaps.map((g) => (
            <div className="gap-row" key={g.code} data-testid="gap-row" data-code={g.code}>
              <span>
                {g.label} <span className="mono muted" style={{ fontSize: 11 }}>{g.code}</span>
              </span>
              <span className="n" data-testid="gap-open">
                {g.open}
              </span>
              <span className="r" data-testid="gap-resolved">
                {g.resolved || '—'}
              </span>
            </div>
          ))}
        </div>

        <div className="card" data-testid="ops">
          <div className="list-head">Human actions (review.jsonl)</div>
          {lines.map((o, i) => (
            <div className="op-row" key={i} data-testid="op-row">
              <span className="what">{o.what}</span>
              <span className="detail">{o.detail}</span>
              <span className="by">{o.by}</span>
            </div>
          ))}
          <div className="op-row" data-testid="op-confirm">
            <span className="what">confirm</span>
            <span className="detail">{job.confirmed_by ? 'Confirmed for delivery' : '(pending)'}</span>
            <span className="by">{job.confirmed_by ?? ''}</span>
          </div>
        </div>
      </main>

      <aside className="flow-side">
        <div className="card card-pad" style={{ gap: 8, fontSize: 13 }} data-testid="package">
          <div className="flow-title">Delivery package</div>
          <div className="pkg-name" data-testid="package-name">
            delivery/{pkgName}/
          </div>
          {packageFiles({ pages, regions: facets?.total ?? null, llmOutputs: llmItems.length > 0, counts }).map((f) => (
            <div className="file-row" key={f.name} data-testid="package-file">
              <span className="name">{f.name}</span>
              <span className="note">{f.note}</span>
            </div>
          ))}
        </div>

        {error && <ErrorBanner error={error} onDismiss={() => setError(null)} />}
        {job.status === 'in_review' && !job.running_op && (
          <form
            className="card card-pad"
            data-testid="confirm-form"
            onSubmit={(e) => {
              e.preventDefault();
              void confirm();
            }}
          >
            <div style={{ fontSize: 13, lineHeight: 1.55, color: 'var(--ink-2)' }}>
              Once confirmed, the package is final and cannot be changed. It appears in the delivery folder all at once,
              so the downstream never reads a half-written package.
            </div>
            <label className="field">
              Confirmed by
              <input className="name" type="text" required value={by} onChange={(e) => setBy(e.target.value)} />
            </label>
            <button type="submit" className="btn btn-primary" disabled={sending || !by.trim()}>
              {sending ? 'Confirming…' : 'Confirm and deliver'}
            </button>
          </form>
        )}
        {job.running_op && (
          <div className="card card-pad muted" style={{ fontSize: 13 }} data-testid="delivering">
            {job.running_op === 'deliver' ? 'Writing and checking the package…' : `Waiting for ${job.running_op} to finish…`}
          </div>
        )}
        {awaitingDelivery && (
          <div className="card card-pad muted" style={{ fontSize: 13 }} data-testid="delivering">
            Confirmed; waiting for the delivery to start…
          </div>
        )}
        {job.status === 'confirmed' && !job.running_op && !awaitingDelivery && (
          <div className="card card-pad" style={{ fontSize: 13 }}>
            <div>Confirmed by {job.confirmed_by}; the package is not delivered yet.</div>
            <button type="button" className="btn" onClick={() => void retry()}>
              Retry delivery
            </button>
          </div>
        )}
        {delivered && (
          <div className={`delivered${check && !check.ok ? ' bad' : ''}`} data-testid="delivered">
            <div style={{ fontWeight: 600, fontSize: 14 }}>Delivered</div>
            {check ? (
              check.ok ? (
                <div data-testid="package-check">Contract v{check.contract_version} check passed · written to the delivery folder</div>
              ) : (
                <div data-testid="package-check">
                  Contract v{check.contract_version} check failed: {check.errors.slice(0, 5).join('; ')}
                </div>
              )
            ) : (
              <div>{data ? 'The package is not in the delivery folder.' : 'Checking the package…'}</div>
            )}
            <div style={{ color: 'var(--ink-2)' }}>The downstream (kb) reads from the delivery folder; intake neither calls nor notifies it.</div>
          </div>
        )}
      </aside>
    </section>
  );
}

const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

function Kpi({ id, label, value, sub }: { id: string; label: string; value: string; sub: string }) {
  return (
    <div className="kpi" data-testid={`kpi-${id}`}>
      <div className="kpi-label">{label}</div>
      <div className="kpi-value">{value}</div>
      <div className="kpi-sub">{sub}</div>
    </div>
  );
}
