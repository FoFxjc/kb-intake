import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react';
import { api, ApiError, type Health, type JobDetail } from '../api/client';
import { retryDelay } from './backoff';

export function asApiError(e: unknown): ApiError {
  if (e instanceof ApiError) return e;
  return new ApiError(0, e instanceof Error ? e.message : String(e));
}

const isAbort = (e: unknown) => e instanceof DOMException && e.name === 'AbortError';

/** GET /v1/health once for the app (llm.enabled hides every LLM element). */
export const HealthContext = createContext<Health | null>(null);
export const useHealth = () => useContext(HealthContext);

export function useHealthOnce(): { health: Health | null; error: ApiError | null } {
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  useEffect(() => {
    const ac = new AbortController();
    api.health(ac.signal).then(setHealth, (e) => isAbort(e) || setError(asApiError(e)));
    return () => ac.abort();
  }, []);
  return { health, error };
}

export const BUSY_POLL_MS = 2000;

/** GET /v1/jobs/{id}; polled every 2 s only while a step runs (running_op set). A failed poll is retried with
 * backoff (lib/backoff.ts) while the last known state still has a step running. */
export function useJob(jobId: string) {
  const [detail, setDetail] = useState<JobDetail | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [failures, setFailures] = useState(0);
  const [tick, setTick] = useState(0);
  const reload = useCallback(() => setTick((t) => t + 1), []);
  const current = useRef(jobId);
  current.current = jobId;

  useEffect(() => {
    const ac = new AbortController();
    api.job(jobId, ac.signal).then(
      (d) => {
        if (current.current !== jobId) return;
        setDetail(d);
        setError(null);
        setFailures(0);
      },
      (e) => {
        if (isAbort(e) || current.current !== jobId) return;
        setError(asApiError(e));
        setFailures((n) => n + 1);
      },
    );
    return () => ac.abort();
  }, [jobId, tick]);

  const running = detail?.job.running_op ?? null;
  useEffect(() => {
    if (!running) return;
    const t = window.setTimeout(reload, failures ? retryDelay(failures) : BUSY_POLL_MS);
    return () => window.clearTimeout(t);
  }, [running, detail, failures, reload]);

  return { detail: detail && detail.job.id === jobId ? detail : null, error, reload };
}
