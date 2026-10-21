// A typed fetch wrapper over the frozen API (contract/api/openapi-v1.json). Paths, parameters and response
// bodies come from the generated types (schema.gen.ts); nothing outside them is called.
import type { components, paths } from './schema.gen';

export type Schemas = components['schemas'];
export type Job = Schemas['Job'];
export type JobDetail = Schemas['JobDetail'];
export type JobSummary = Schemas['JobSummary'];
export type PageView = Schemas['PageView'];
export type Region = Schemas['Region'];
export type Table = Schemas['Table'];
export type Gap = Schemas['Gap'];
export type Health = Schemas['Health'];
export type CreateJobResult = Schemas['CreateJobResult'];

/** An error answer of the API (status and the server's `detail`), or status 0 when the server is unreachable. */
export class ApiError extends Error {
  readonly status: number;
  readonly detail: string;
  readonly code: string | null;

  constructor(status: number, detail: string, code: string | null = null) {
    super(`${status || 'network'}: ${detail}`);
    this.name = 'ApiError';
    this.status = status;
    this.detail = detail;
    this.code = code;
  }
}

type Method = 'get' | 'post' | 'put';
type Op<P extends keyof paths, M extends Method> = NonNullable<paths[P][M]>;
type Ok<O> = O extends { responses: infer R }
  ? 200 extends keyof R
    ? R[200]
    : 202 extends keyof R
      ? R[202]
      : never
  : never;
type JsonOf<R> = R extends { content: { 'application/json': infer J } } ? J : never;
type PathParams<O> = O extends { parameters: { path: infer P } } ? P : never;
type QueryParams<O> = O extends { parameters: { query?: infer Q } } ? Q : never;

/** The JSON body of the success answer of `method path`. */
export type Response<P extends keyof paths, M extends Method> = JsonOf<Ok<Op<P, M>>>;

interface CallOptions<P extends keyof paths, M extends Method> {
  path?: PathParams<Op<P, M>>;
  query?: QueryParams<Op<P, M>>;
  json?: unknown;
  body?: BodyInit;
  text?: string;
  signal?: AbortSignal;
}

/** The URL of an API path with its path parameters filled in and its query appended (lists repeat the key). */
export function url<P extends keyof paths>(path: P, params?: Record<string, string | number>, query?: object): string {
  let out = (path as string).replace(/\{(\w+)\}/g, (_, name: string) => {
    const v = params?.[name];
    if (v === undefined) throw new Error(`missing path parameter ${name}`);
    return encodeURIComponent(String(v));
  });
  if (query) {
    const qs = new URLSearchParams();
    for (const [k, v] of Object.entries(query)) {
      if (v === undefined || v === null || v === '') continue;
      for (const one of Array.isArray(v) ? v : [v]) qs.append(k, String(one));
    }
    const s = qs.toString();
    if (s) out += `?${s}`;
  }
  return out;
}

/** The server's `detail` as one line of text (a string, a validation list or a {message} object). */
export function detailText(detail: unknown): string {
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((d) => (d && typeof d === 'object' && 'msg' in d ? String((d as { msg: unknown }).msg) : JSON.stringify(d)))
      .join('; ');
  }
  if (detail && typeof detail === 'object') {
    const d = detail as Record<string, unknown>;
    if (typeof d.message === 'string') return d.param ? `${d.message} (${String(d.param)})` : d.message;
    return JSON.stringify(detail);
  }
  return detail === undefined ? '' : String(detail);
}

export async function call<P extends keyof paths, M extends Method>(
  method: M,
  path: P,
  opts: CallOptions<P, M> = {},
): Promise<Response<P, M>> {
  const headers: Record<string, string> = { Accept: 'application/json' };
  let body: BodyInit | undefined = opts.body;
  if (opts.json !== undefined) {
    headers['Content-Type'] = 'application/json';
    body = JSON.stringify(opts.json);
  } else if (opts.text !== undefined) {
    headers['Content-Type'] = 'text/plain; charset=utf-8';
    body = opts.text;
  }
  let res: globalThis.Response;
  try {
    res = await fetch(url(path, opts.path as Record<string, string | number> | undefined, opts.query as object), {
      method: method.toUpperCase(),
      headers,
      body,
      signal: opts.signal,
    });
  } catch (e) {
    if (e instanceof DOMException && e.name === 'AbortError') throw e;
    throw new ApiError(0, 'The kb-intake server cannot be reached.');
  }
  const text = await res.text();
  let data: unknown = undefined;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = text;
    }
  }
  if (!res.ok) {
    const err = data && typeof data === 'object' ? (data as { detail?: unknown; error?: unknown }) : { detail: data };
    const detail = detailText(err.detail) || res.statusText || 'request failed';
    throw new ApiError(res.status, detail, typeof err.error === 'string' ? err.error : null);
  }
  return data as Response<P, M>;
}

export const api = {
  health: (signal?: AbortSignal) => call('get', '/v1/health', { signal }),
  jobs: (signal?: AbortSignal) => call('get', '/v1/jobs', { signal }),
  job: (jobId: string, signal?: AbortSignal) => call('get', '/v1/jobs/{job_id}', { path: { job_id: jobId }, signal }),
  page: (jobId: string, n: number, signal?: AbortSignal) =>
    call('get', '/v1/jobs/{job_id}/pages/{n}', { path: { job_id: jobId, n }, signal }),
  /** POST /v1/jobs as multipart: the file and the declaration (YAML text). */
  upload: (file: File, declaration: string) => {
    const form = new FormData();
    form.append('file', file, file.name);
    form.append('declaration', declaration);
    return call('post', '/v1/jobs', { body: form });
  },
  retry: (jobId: string) => call('post', '/v1/jobs/{job_id}/retry', { path: { job_id: jobId } }),
};

/** The job's source file (HTTP Range), for pdf.js. */
export const sourcePdfUrl = (jobId: string) => url('/v1/jobs/{job_id}/source.pdf', { job_id: jobId });
