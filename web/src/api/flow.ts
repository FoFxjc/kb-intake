// The calls of the Workflow, Progress and Deliver pages, over the typed wrapper (client.ts). Only endpoints of the
// frozen API (contract/api/openapi-v1.json).
import { call, type Schemas } from './client';

export type WorkflowView = Schemas['WorkflowView'];
export type LlmInfo = Schemas['LlmInfo'];
export type JobEvent = Schemas['Event'];
export type Facets = Schemas['Facets'];
export type ReviewRow = Schemas['ReviewRow'];
export type PackageEntry = Schemas['PackageEntry'];
export type LlmView = Schemas['LlmView'];

const p = (jobId: string) => ({ job_id: jobId });

export const flowApi = {
  workflow: (jobId: string, version?: number, signal?: AbortSignal) =>
    call('get', '/v1/jobs/{job_id}/workflow', { path: p(jobId), query: version ? { version } : undefined, signal }),
  /** PUT /workflow takes the YAML text (application/yaml). */
  saveWorkflow: (jobId: string, yaml: string) =>
    call('put', '/v1/jobs/{job_id}/workflow', { path: p(jobId), text: yaml, textType: 'application/yaml' }),
  approve: (jobId: string, by: string, version: number) =>
    call('post', '/v1/jobs/{job_id}/approve', { path: p(jobId), json: { by, version } }),
  events: (jobId: string, after: number, signal?: AbortSignal) =>
    call('get', '/v1/jobs/{job_id}/events', { path: p(jobId), query: { after }, signal }),
  facets: (jobId: string, signal?: AbortSignal) => call('get', '/v1/jobs/{job_id}/facets', { path: p(jobId), signal }),
  gaps: (jobId: string, signal?: AbortSignal) => call('get', '/v1/jobs/{job_id}/gaps', { path: p(jobId), signal }),
  reviewOps: (jobId: string, signal?: AbortSignal) => call('get', '/v1/jobs/{job_id}/review', { path: p(jobId), signal }),
  /** GET /llm: only called when /v1/health says llm.enabled. */
  llm: (jobId: string, signal?: AbortSignal) => call('get', '/v1/jobs/{job_id}/llm', { path: p(jobId), signal }),
  confirm: (jobId: string, by: string) => call('post', '/v1/jobs/{job_id}/confirm', { path: p(jobId), json: { by } }),
  packages: (signal?: AbortSignal) => call('get', '/v1/packages', { signal }),
};
