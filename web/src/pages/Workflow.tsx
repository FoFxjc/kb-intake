import { useEffect, useState } from 'react';
import { type ApiError, type JobDetail } from '../api/client';
import { flowApi, type LlmInfo, type WorkflowView } from '../api/flow';
import { ErrorBanner } from '../components/ErrorBanner';
import { asApiError, useHealth } from '../lib/hooks';
import { useName } from '../lib/name';
import { jobHref } from '../lib/route';
import {
  formatPages,
  HANDLER_NAMES,
  parsePages,
  rowHandler,
  rowOptions,
  ROWS,
  runSummary,
  setRow,
  workflowYaml,
  type Workflow,
} from '../lib/workflow';
import '../styles/flow.css';

const isAbort = (e: unknown) => e instanceof DOMException && e.name === 'AbortError';
const fmt = (n: number) => n.toLocaleString('en-US');
const count = (n: number, word: string) => `${fmt(n)} ${word}${n === 1 ? '' : 's'}`;

function hostOf(baseUrl: string): string {
  try {
    return new URL(baseUrl).host || baseUrl;
  } catch {
    return baseUrl;
  }
}

interface Switches {
  describe: boolean;
  chartTable: boolean;
}

/** Workflow & approval: a handler per region type (rows as in the prototype), excluded pages, the LLM switches of
 * this job (only when the LLM is enabled), what the run will process, and approval (PUT /workflow, POST /approve). */
export function WorkflowPage({ detail, onChanged }: { detail: JobDetail; onChanged: () => void }) {
  const job = detail.job;
  const jobId = job.id;
  const health = useHealth();
  const labelled = !['uploaded', 'labeled'].includes(job.status) && job.running_op !== 'label';
  const editable = job.status === 'planned' && !job.running_op;

  const [view, setView] = useState<WorkflowView | null>(null);
  const [wf, setWf] = useState<Workflow | null>(null);
  const [counts, setCounts] = useState<Record<string, number> | null>(null);
  const [pagesText, setPagesText] = useState('');
  const [sw, setSw] = useState<Switches>({ describe: true, chartTable: true });
  const [loadError, setLoadError] = useState<ApiError | null>(null);
  const [tick, setTick] = useState(0);
  const [by, setBy, rememberBy] = useName();
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const approvedVersion = job.workflow_version;

  useEffect(() => {
    if (!labelled) return;
    const ac = new AbortController();
    setLoadError(null);
    (async () => {
      let v = await flowApi.workflow(jobId, undefined, ac.signal);
      if (approvedVersion && v.version !== approvedVersion) v = await flowApi.workflow(jobId, approvedVersion, ac.signal);
      const f = await flowApi.facets(jobId, ac.signal);
      setView(v);
      setCounts(f.type);
      const parsed = v.workflow as Workflow | null;
      if (parsed) {
        setWf(parsed);
        setPagesText(formatPages(parsed.exclude_pages));
        setSw({ describe: parsed.llm?.describe ?? true, chartTable: parsed.llm?.chart_table ?? true });
      }
    })().catch((e: unknown) => isAbort(e) || setLoadError(asApiError(e)));
    return () => ac.abort();
  }, [jobId, labelled, approvedVersion, tick]);

  // the LLM: GET /v1/health (llm.enabled); every LLM element is hidden when it is off
  const llm: LlmInfo | null = health?.llm ?? null;
  const llmEnabled = !!llm?.enabled;
  const eff = {
    describe: llmEnabled && !!llm?.features.describe && sw.describe,
    chartTable: llmEnabled && !!llm?.features.chart_table && sw.chartTable,
  };
  const pages = typeof detail.stats?.pages === 'number' ? detail.stats.pages : null;
  const parsedPages = parsePages(pagesText, pages);
  const pagesError = 'error' in parsedPages ? parsedPages.error : null;

  /** The workflow to save: the edited handlers, the excluded pages, and the `llm` block when the LLM is enabled
   * (otherwise the block the workflow already has is kept as it is). */
  let toSave: Workflow | null = null;
  if (wf && 'pages' in parsedPages) {
    toSave = { ...wf, exclude_pages: parsedPages.pages };
    if (llmEnabled) toSave.llm = { describe: eff.describe, chart_table: eff.chartTable };
  }

  const sum = wf && counts ? runSummary(wf, counts, eff) : null;

  const approve = async () => {
    if (!toSave || !view || !by.trim()) return;
    setSending(true);
    setError(null);
    try {
      const saved = await flowApi.saveWorkflow(jobId, workflowYaml(toSave, view.types));
      await flowApi.approve(jobId, by.trim(), saved.version);
      rememberBy();
      onChanged();
      window.location.hash = jobHref(jobId, 'progress');
    } catch (e) {
      setError(asApiError(e));
      onChanged();
    } finally {
      setSending(false);
    }
  };

  if (!labelled) {
    return (
      <section className="page">
        <main className="card flow-main">
          <div className="card-head">
            <h1>How to handle each region type</h1>
          </div>
          <div className="flow-msg">The suggested workflow appears when labelling is done.</div>
        </main>
      </section>
    );
  }

  return (
    <section className="page">
      <main className="card flow-main" data-testid="workflow-main">
        <div className="card-head">
          <h1>How to handle each region type</h1>
          <span className="muted" style={{ fontSize: 13 }}>
            {view ? `Template ${wf?.name ?? ''} v${wf?.version ?? ''} · workflow version ${view.version}` : 'Loading…'}
            {editable ? ' · suggested from this file’s stats, editable per row' : ''}
          </span>
        </div>
        {loadError && (
          <div style={{ padding: 12 }}>
            <ErrorBanner error={loadError} onRetry={() => setTick((t) => t + 1)} />
          </div>
        )}
        {view?.error && (
          <div style={{ padding: 12 }}>
            <div className="banner banner-error" role="alert">
              <strong>The stored workflow is not valid</strong>
              <span className="banner-msg">{view.error}</span>
            </div>
          </div>
        )}
        {wf && (
          <div className="wf-scroll">
            <div className="wf-grid" role="table" aria-label="Handlers">
              <div className="wf-row head" role="row">
                <div role="columnheader">Region type</div>
                <div role="columnheader" style={{ textAlign: 'right' }}>
                  Count
                </div>
                <div role="columnheader">Handler</div>
              </div>
              {ROWS.map((row) => {
                const current = rowHandler(row, wf);
                const n = row.types.reduce((a, t) => a + (counts?.[t] ?? 0), 0);
                return (
                  <div className="wf-row" role="row" key={row.key} data-testid={`wf-row-${row.key}`}>
                    <div className="wf-type" role="cell">
                      <span className="swatch" style={{ ['--c' as string]: row.color }} />
                      {row.label}
                    </div>
                    <div className="wf-count" role="cell">
                      {counts ? (n ? fmt(n) : '—') : ''}
                    </div>
                    <div className="wf-opts" role="cell">
                      {rowOptions(row, wf, llmEnabled).map((o) => (
                        <button
                          key={o}
                          type="button"
                          className={`pill-opt${o === 'describe' ? ' llm' : ''}`}
                          aria-pressed={current === o}
                          disabled={!editable}
                          onClick={() => setWf(setRow(wf, row, o))}
                        >
                          {HANDLER_NAMES[o] ?? o}
                        </button>
                      ))}
                      {current === null && <span className="wf-note">Mixed: {row.types.map((t) => `${t} ${wf.handlers[t]}`).join(', ')}</span>}
                      {row.note && <span className="wf-note">{row.note}</span>}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        )}
        {wf && (
          <div className="wf-exclude">
            <label htmlFor="wf-exclude" className="muted">
              Excluded pages
            </label>
            <input
              id="wf-exclude"
              type="text"
              value={pagesText}
              disabled={!editable}
              placeholder={'None (e.g. 1-4, 270-276)'}
              aria-invalid={!!pagesError}
              onChange={(e) => setPagesText(e.target.value)}
            />
            {pagesError ? (
              <span className="err-text" role="alert">
                {pagesError}
              </span>
            ) : (
              'pages' in parsedPages &&
              parsedPages.pages.length > 0 && (
                <span className="muted">
                  {parsedPages.pages.length} page{parsedPages.pages.length === 1 ? '' : 's'}: their regions are recorded as excluded
                </span>
              )
            )}
          </div>
        )}
      </main>

      <aside className="flow-side">
        {view && view.notes.length > 0 && (
          <div className="note-card" data-testid="wf-notes">
            <div style={{ fontWeight: 600, marginBottom: 4 }}>Suggestion</div>
            <ul>
              {view.notes.map((n, i) => (
                <li key={i}>{String(n)}</li>
              ))}
            </ul>
          </div>
        )}

        {llmEnabled && llm && (
          <div className="card card-pad" data-testid="llm-panel" style={{ gap: 12 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
              <div className="flow-title">LLM</div>
              <span className="llm-chip">
                {llm.model} · {llm.remote ? 'remote' : 'local'}
              </span>
            </div>
            <div className="llm-notice" data-testid="llm-notice">
              When on, region images and text are sent to {hostOf(llm.base_url)}
              {llm.remote ? ' (a remote service)' : ''}. Results are kept separately in llm.jsonl and marked low weight.
            </div>
            <Toggle label="Document summary" hint="Generated after labeling; set in the configuration" on={llm.features.summary} />
            <Toggle
              label="Chart and picture descriptions"
              hint={llm.features.describe ? 'Runs on regions set to "LLM describe" above' : 'Off in the configuration'}
              on={eff.describe}
              onFlip={editable && llm.features.describe ? () => setSw((s) => ({ ...s, describe: !s.describe })) : undefined}
            />
            <Toggle
              label="Chart to table (experimental)"
              hint={llm.features.chart_table ? 'The model estimates values; low weight, check by hand' : 'Off in the configuration'}
              on={eff.chartTable}
              onFlip={editable && llm.features.chart_table ? () => setSw((s) => ({ ...s, chartTable: !s.chartTable })) : undefined}
            />
          </div>
        )}

        {sum && (
          <div className="card card-pad" data-testid="run-summary">
            <div className="flow-title">This run will process</div>
            <div className="sum-grid">
              <div>Table structure</div>
              <div data-testid="sum-tables">{count(sum.tables, 'table')}</div>
              <div>Text by box</div>
              <div data-testid="sum-text">{count(sum.text, 'region')}</div>
              {llmEnabled && (
                <>
                  <div>LLM description</div>
                  <div data-testid="sum-describe">{count(sum.describe, 'region')}</div>
                  <div>Chart to table</div>
                  <div data-testid="sum-chart-table">{sum.chartTable === null ? 'Off' : `up to ${count(sum.chartTable, 'chart')} (experimental)`}</div>
                </>
              )}
              <div>Discard / record only</div>
              <div data-testid="sum-discard">
                {fmt(sum.discard)} / {fmt(sum.record)}
              </div>
              {sum.unhandled > 0 && (
                <>
                  <div>Left unhandled</div>
                  <div>{count(sum.unhandled, 'region')}</div>
                </>
              )}
            </div>
            {'pages' in parsedPages && parsedPages.pages.length > 0 && (
              <div className="muted" style={{ fontSize: 12 }}>
                Counts include the regions on the excluded pages.
              </div>
            )}
          </div>
        )}

        {error && (
          <ErrorBanner
            error={error}
            onRetry={() => {
              setError(null);
              setTick((t) => t + 1);
              onChanged();
            }}
            onDismiss={() => setError(null)}
          />
        )}
        {editable && (
          <form
            className="card card-pad"
            data-testid="approve-form"
            onSubmit={(e) => {
              e.preventDefault();
              void approve();
            }}
          >
            <label className="field">
              Approved by
              <input className="name" type="text" required value={by} onChange={(e) => setBy(e.target.value)} />
            </label>
            <button type="submit" className="btn btn-primary" disabled={sending || !toSave || !by.trim()}>
              {sending ? 'Approving…' : 'Approve and start extraction'}
            </button>
          </form>
        )}
        {!editable && job.approved_by && (
          <div className="card card-pad" data-testid="approved" style={{ fontSize: 13 }}>
            <div style={{ fontWeight: 600 }}>
              Approved ({job.approved_by} · workflow v{job.workflow_version ?? '?'})
              {job.running_op === 'extract' ? ' · extracting' : ''}
            </div>
            <a className="go-link" href={jobHref(jobId, ['in_review', 'confirmed', 'delivered'].includes(job.status) ? 'review' : 'progress')}>
              {['in_review', 'confirmed', 'delivered'].includes(job.status) ? 'Go to Review →' : 'Follow the extraction →'}
            </a>
          </div>
        )}
        {!editable && !job.approved_by && job.running_op && (
          <div className="card card-pad muted" style={{ fontSize: 13 }}>
            The workflow can be edited when the running step is done.
          </div>
        )}
      </aside>
    </section>
  );
}

function Toggle({ label, hint, on, onFlip }: { label: string; hint: string; on: boolean; onFlip?: () => void }) {
  return (
    <button type="button" className="toggle" aria-pressed={on} disabled={!onFlip} onClick={onFlip}>
      <span className="toggle-text">
        <span>{label}</span>
        <span className="toggle-hint">{hint}</span>
      </span>
      <span className={`toggle-state${on ? ' on' : ''}`}>{on ? 'On' : 'Off'}</span>
    </button>
  );
}
