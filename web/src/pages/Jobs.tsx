import { useCallback, useEffect, useState, type FormEvent } from 'react';
import { api, type ApiError, type CreateJobResult, type JobSummary } from '../api/client';
import { ErrorBanner } from '../components/ErrorBanner';
import { Header } from '../components/Header';
import { declarationYaml, slug, type Declaration } from '../lib/declaration';
import { retryDelay } from '../lib/backoff';
import { asApiError, BUSY_POLL_MS, useHealth } from '../lib/hooks';
import { hostOf, SUMMARY_SENDS } from '../lib/llm';
import { jobHref, tabForStatus } from '../lib/route';

const STATUS_LABEL: Record<string, string> = {
  uploaded: 'Uploaded',
  labeled: 'Labelled',
  planned: 'Ready for workflow',
  approved: 'Approved',
  extracted: 'Extracted',
  in_review: 'In review',
  confirmed: 'Confirmed',
  delivered: 'Delivered',
};

function statusChip(j: JobSummary) {
  if (j.running_op) return <span className="chip chip-run">{`Running ${j.running_op}\u2026`}</span>;
  if (j.last_error) return <span className="chip chip-error">{STATUS_LABEL[j.status] ?? j.status} · failed</span>;
  if (j.status === 'delivered') return <span className="chip chip-ok">Delivered</span>;
  if (j.status === 'planned' || j.status === 'in_review')
    return <span className="chip chip-call">{STATUS_LABEL[j.status]}</span>;
  return <span className="chip">{STATUS_LABEL[j.status] ?? j.status}</span>;
}

const when = (iso: string) => iso.replace('T', ' ').replace(/\.\d+/, '').replace(/(Z|[+-]\d\d:\d\d)$/, ' UTC');

const EMPTY: Declaration = {
  doc_id: '',
  issuer_id: '',
  doc_type: 'annual_report',
  period_end: '',
  source_url: '',
  full: true,
  lang: '',
};

/** Jobs and upload: the job list (GET /v1/jobs) and a new job from a file plus its declaration (POST /v1/jobs). */
export function JobsPage() {
  const [jobs, setJobs] = useState<JobSummary[] | null>(null);
  const [listError, setListError] = useState<ApiError | null>(null);
  const [failures, setFailures] = useState(0);
  const [tick, setTick] = useState(0);
  const reload = useCallback(() => setTick((t) => t + 1), []);

  useEffect(() => {
    const ac = new AbortController();
    api.jobs(ac.signal).then(
      (r) => {
        setJobs(r.jobs);
        setListError(null);
        setFailures(0);
      },
      (e: unknown) => {
        if (e instanceof DOMException && e.name === 'AbortError') return;
        setListError(asApiError(e));
        setFailures((n) => n + 1);
      },
    );
    return () => ac.abort();
  }, [tick]);

  // polled while a job runs (as last seen); a failed poll is retried with backoff
  const anyRunning = !!jobs?.some((j) => j.running_op);
  useEffect(() => {
    if (!anyRunning) return;
    const t = window.setTimeout(reload, failures ? retryDelay(failures) : BUSY_POLL_MS);
    return () => window.clearTimeout(t);
  }, [anyRunning, jobs, failures, reload]);

  return (
    <div className="app">
      <Header />
      <section className="page">
        <main className="card jobs-main">
          <div className="card-head">
            <h1>Jobs</h1>
            <span className="muted" style={{ fontSize: 13 }}>
              Newest first · open a job to continue where it stands
            </span>
          </div>
          {listError && (
            <div style={{ padding: 12 }}>
              <ErrorBanner error={listError} onRetry={reload} />
            </div>
          )}
          {jobs && jobs.length === 0 && <div className="jobs-empty">No jobs yet. Upload a PDF to start.</div>}
          {jobs && jobs.length > 0 && (
            <div className="table-scroll">
              <table className="jobs-table" data-testid="jobs-table">
                <thead>
                  <tr>
                    <th>File</th>
                    <th>Doc id</th>
                    <th>Status</th>
                    <th>Updated</th>
                    <th>Job</th>
                  </tr>
                </thead>
                <tbody>
                  {jobs.map((j) => (
                    <tr key={j.job_id} data-testid="job-row" data-job={j.job_id}>
                      <td>
                        <a href={jobHref(j.job_id, tabForStatus(j.status))} className="mono">
                          {j.source_name}
                        </a>
                        {j.last_error && <div className="err-text">{j.last_error}</div>}
                      </td>
                      <td className="mono">{j.doc_id}</td>
                      <td>{statusChip(j)}</td>
                      <td className="muted">{when(j.updated_at)}</td>
                      <td className="mono muted">{j.job_id}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </main>
        <aside className="jobs-side">
          <UploadCard onUploaded={reload} />
        </aside>
      </section>
    </div>
  );
}

function UploadCard({ onUploaded }: { onUploaded: () => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [decl, setDecl] = useState<Declaration>(EMPTY);
  const [docIdTouched, setDocIdTouched] = useState(false);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [existing, setExisting] = useState<CreateJobResult | null>(null);
  const llm = useHealth()?.llm;

  const set = <K extends keyof Declaration>(k: K, v: Declaration[K]) => setDecl((d) => ({ ...d, [k]: v }));

  const pick = (f: File | null) => {
    setFile(f);
    setExisting(null);
    setError(null);
    if (f && !docIdTouched) set('doc_id', slug(f.name));
  };

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!file) return;
    setSending(true);
    setError(null);
    setExisting(null);
    try {
      const r = await api.upload(file, declarationYaml(decl));
      onUploaded();
      if (r.existing_job) setExisting(r);
      else window.location.hash = jobHref(r.job_id, 'labels');
    } catch (err) {
      setError(asApiError(err));
    } finally {
      setSending(false);
    }
  };

  return (
    <form className="card card-pad" onSubmit={submit} data-testid="upload-form">
      <div style={{ fontWeight: 600, fontSize: 14 }}>New job</div>
      <label className="field">
        PDF file
        <input type="file" accept="application/pdf,.pdf" required onChange={(e) => pick(e.target.files?.[0] ?? null)} />
      </label>
      <label className="field">
        Doc id
        <input
          type="text"
          required
          value={decl.doc_id}
          pattern="[a-z0-9][a-z0-9._\-]*"
          onChange={(e) => {
            setDocIdTouched(true);
            set('doc_id', e.target.value);
          }}
        />
        <span className="field-hint">Names the delivery package: lower case letters, digits, . _ -</span>
      </label>
      <label className="field">
        Issuer id
        <input type="text" required value={decl.issuer_id} onChange={(e) => set('issuer_id', e.target.value)} />
      </label>
      <label className="field">
        Document type
        <input type="text" required value={decl.doc_type} onChange={(e) => set('doc_type', e.target.value)} />
      </label>
      <label className="field">
        Period end
        <input type="date" required value={decl.period_end} onChange={(e) => set('period_end', e.target.value)} />
      </label>
      <label className="field">
        Source URL
        <input type="text" required value={decl.source_url} onChange={(e) => set('source_url', e.target.value)} />
      </label>
      <label className="field">
        Language (optional)
        <input
          type="text"
          value={decl.lang}
          maxLength={2}
          placeholder="detected when empty, e.g. en"
          onChange={(e) => set('lang', e.target.value)}
        />
      </label>
      <label className="check">
        <input type="checkbox" checked={decl.full} onChange={(e) => set('full', e.target.checked)} />
        The file is the complete document
      </label>
      {llm?.enabled && llm.features.summary && (
        <div className="llm-notice" data-testid="upload-llm-notice">
          LLM on: after labeling, {SUMMARY_SENDS} is sent to {hostOf(llm.base_url)} for a document summary.
        </div>
      )}
      {error && <ErrorBanner error={error} onDismiss={() => setError(null)} />}
      {existing && (
        <div className="banner banner-note" role="status" data-testid="existing-job">
          <span className="banner-msg">
            This file already has a job ({existing.status}). {existing.existing_note ?? ''}
          </span>
          <a href={jobHref(existing.job_id, tabForStatus(existing.status))}>Open job {existing.job_id}</a>
        </div>
      )}
      <button type="submit" className="btn btn-primary" disabled={!file || sending}>
        {sending ? 'Uploading\u2026' : 'Upload and label'}
      </button>
    </form>
  );
}
