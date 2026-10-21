import { useEffect, useRef, useState } from 'react';
import type { ApiError, JobDetail } from '../api/client';
import { flowApi, type JobEvent } from '../api/flow';
import { ErrorBanner } from '../components/ErrorBanner';
import { retryDelay } from '../lib/backoff';
import { asApiError, useHealth } from '../lib/hooks';
import { PHASE_LABEL, runEvents, runView } from '../lib/progress';
import { jobHref } from '../lib/route';
import '../styles/flow.css';

export const EVENTS_POLL_MS = 1000;
const MARK = { todo: '', run: '…', done: '✓', failed: '!' };
const AFTER_EXTRACT = ['in_review', 'confirmed', 'delivered'];

/** Extraction progress: GET /events?after= every second while the job extracts (Docling, regions, LLM
 * descriptions done / total from the `llm` events), then a link to Review. */
export function ProgressPage({ detail, onChanged }: { detail: JobDetail; onChanged: () => void }) {
  const job = detail.job;
  const jobId = job.id;
  const health = useHealth();
  const withLlm = !!health?.llm.enabled;
  const [events, setEvents] = useState<JobEvent[]>([]);
  const [error, setError] = useState<ApiError | null>(null);
  const [tick, setTick] = useState(0);
  const after = useRef(0);
  const failures = useRef(0);
  const extracting =
    job.running_op === 'extract' || job.running_op === 'retry' || (['approved', 'extracted'].includes(job.status) && !job.last_error);

  useEffect(() => {
    after.current = 0;
    setEvents([]);
  }, [jobId]);

  // one request at a time: the next poll is scheduled when the answer is in; a failed one is retried with backoff
  useEffect(() => {
    const ac = new AbortController();
    let timer = 0;
    flowApi.events(jobId, after.current, ac.signal).then(
      (r) => {
        setError(null);
        failures.current = 0;
        if (r.events.length) {
          after.current = r.events[r.events.length - 1].seq;
          setEvents((old) => [...old, ...r.events]);
        }
        if (extracting) timer = window.setTimeout(() => setTick((t) => t + 1), EVENTS_POLL_MS);
      },
      (e: unknown) => {
        if (e instanceof DOMException && e.name === 'AbortError') return;
        setError(asApiError(e));
        failures.current += 1;
        if (extracting) timer = window.setTimeout(() => setTick((t) => t + 1), retryDelay(failures.current));
      },
    );
    return () => {
      ac.abort();
      window.clearTimeout(timer);
    };
  }, [jobId, extracting, tick]);

  const run = runView(events, withLlm);
  // the events say the extraction is over while the job detail still says it runs: refresh the job
  const runDone = run.done || !!run.failed;
  useEffect(() => {
    if (runDone && extracting) onChanged();
  }, [runDone, extracting, onChanged]);

  const reviewReady = AFTER_EXTRACT.includes(job.status);
  const beforeApproval = !job.approved_by && !run.started;
  const log = runEvents(events);

  return (
    <section className="page">
      <main className="card flow-main" data-testid="progress-main">
        <div className="card-head">
          <h1>Extraction progress</h1>
          <span className="muted" style={{ fontSize: 13 }}>
            {job.approved_by ? `Approved by ${job.approved_by} · workflow v${job.workflow_version ?? '?'}` : 'Not approved yet'}
          </span>
        </div>
        {error && (
          <div style={{ padding: 12 }}>
            <ErrorBanner error={error} onRetry={() => setTick((t) => t + 1)} />
          </div>
        )}
        {beforeApproval ? (
          <div className="flow-msg">
            Extraction starts when the workflow is approved. <a href={jobHref(jobId, 'workflow')}>Go to the workflow</a>
          </div>
        ) : (
          <>
            <div style={{ padding: '14px 16px', display: 'flex', flexDirection: 'column', gap: 8, borderBottom: '1px solid var(--line-soft)' }}>
              <div className="bar" role="progressbar" aria-label="Extraction" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(run.fraction * 100)}>
                <div style={{ width: `${Math.round(run.fraction * 100)}%` }} />
              </div>
              <div className="muted" style={{ fontSize: 13 }} data-testid="progress-status">
                {run.failed
                  ? 'Extraction failed'
                  : run.done || reviewReady
                    ? 'Extraction done'
                    : run.started
                      ? 'Extracting…'
                      : 'Waiting for the extraction to start…'}
              </div>
            </div>
            {run.phases.map((p) => (
              <div className="phase" key={p.key} data-state={p.state} data-testid={`phase-${p.key}`}>
                <span className="phase-mark" aria-hidden="true">
                  {MARK[p.state]}
                </span>
                <div>
                  <div>{PHASE_LABEL[p.key]}</div>
                  <div className="phase-detail">{p.detail}</div>
                </div>
                <div className="phase-value">{p.value}</div>
              </div>
            ))}
            {run.failed && (
              <div style={{ padding: 12 }}>
                <div className="banner banner-error" role="alert">
                  <strong>Extraction failed</strong>
                  <span className="banner-msg">{run.failed}</span>
                </div>
              </div>
            )}
            {reviewReady && (
              <div style={{ padding: '14px 16px' }}>
                <a className="go-link" href={jobHref(jobId, 'review')} data-testid="go-review">
                  Go to Review {'→'}
                </a>
              </div>
            )}
          </>
        )}
      </main>
      <aside className="flow-side">
        <div className="card card-pad">
          <div className="flow-title">Events of this run</div>
          {log.length === 0 && <div className="muted" style={{ fontSize: 13 }}>No events yet.</div>}
          <div className="events" data-testid="event-log">
            {log.map((e) => (
              <div key={e.seq}>
                <span className="mono muted">{e.seq}</span>
                <span>
                  <span className="mono">{e.step}</span> {e.status}
                  {e.info && Object.keys(e.info).length > 0 && (
                    <span className="muted"> {Object.entries(e.info).map(([k, v]) => `${k} ${typeof v === 'object' ? JSON.stringify(v) : String(v)}`).join(' · ')}</span>
                  )}
                </span>
              </div>
            ))}
          </div>
        </div>
      </aside>
    </section>
  );
}
