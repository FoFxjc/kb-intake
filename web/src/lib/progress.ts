// The extraction run as the progress events tell it (extract.py, llm.py): docling started/done, regions
// started/done, llm started/done (only when regions are described), extract done/failed.
import type { JobEvent } from '../api/flow';

export type PhaseState = 'todo' | 'run' | 'done' | 'failed';

export interface Phase {
  key: 'docling' | 'regions' | 'llm';
  state: PhaseState;
  value: string;
  detail: string;
}

export interface RunView {
  started: boolean;
  phases: Phase[];
  done: boolean;
  failed: string | null;
  /** 0..1 for the bar */
  fraction: number;
  /** the LLM descriptions of the run: done / total regions, null when none were started */
  llm: { done: number; total: number; failed: number } | null;
}

const num = (v: unknown) => (typeof v === 'number' ? v : null);

/** The events of the last extraction run (from its `docling started {stage: extract}` on). */
export function runEvents(events: JobEvent[]): JobEvent[] {
  let start = -1;
  events.forEach((e, i) => {
    if (e.step === 'docling' && e.status === 'started' && e.info?.stage === 'extract') start = i;
  });
  return start < 0 ? [] : events.slice(start);
}

export function runView(all: JobEvent[], withLlm: boolean): RunView {
  const ev = runEvents(all);
  const last = (step: string) => [...ev].reverse().find((e) => e.step === step) ?? null;
  const first = (step: string, status: string) => ev.find((e) => e.step === step && e.status === status) ?? null;
  const extractDone = !!ev.find((e) => e.step === 'extract' && e.status === 'done');
  const failedEv = ev.find((e) => e.step === 'extract' && e.status === 'failed') ?? null;
  const failed = failedEv ? String(failedEv.info?.error ?? 'extraction failed') : null;
  const stateOf = (step: string): PhaseState => {
    const e = last(step);
    if (!e) return 'todo';
    if (e.status === 'done') return 'done';
    return failed ? 'failed' : 'run';
  };

  const phases: Phase[] = [];
  const d = last('docling');
  const dState = stateOf('docling');
  const secs = num(d?.info?.seconds);
  phases.push({
    key: 'docling',
    state: dState,
    value: dState === 'done' ? (d?.info?.cached ? 'cached' : secs !== null ? `${secs.toFixed(1)} s` : 'done') : dState === 'run' ? 'running' : '',
    detail: 'Docling table structure (TableFormer) on the table pages',
  });

  const r = last('regions');
  const rState = stateOf('regions');
  const rStart = first('regions', 'started');
  const total = num(rStart?.info?.regions);
  phases.push({
    key: 'regions',
    state: rState,
    value:
      rState === 'done'
        ? `${num(r?.info?.regions) ?? '?'} regions`
        : total !== null && rState !== 'todo'
          ? `${total} regions`
          : '',
    detail:
      rState === 'done'
        ? `Text by box, tables and gaps: ${num(r?.info?.tables) ?? 0} tables, ${num(r?.info?.gaps) ?? 0} gaps`
        : 'Text by box, tables and gaps per region',
  });

  let llm: RunView['llm'] = null;
  const lStart = first('llm', 'started');
  const lDone = first('llm', 'done');
  if (lStart) {
    const n = num(lStart.info?.regions) ?? 0;
    const failedCalls = num(lDone?.info?.failed) ?? 0;
    llm = { total: n, done: lDone ? n : 0, failed: failedCalls };
  }
  if (withLlm) {
    const lState: PhaseState = lDone ? 'done' : lStart ? (failed ? 'failed' : 'run') : rState === 'done' || extractDone ? 'done' : 'todo';
    phases.push({
      key: 'llm',
      state: lState,
      value: llm ? `${llm.done} / ${llm.total}` : rState === 'done' || extractDone ? 'none' : '',
      detail: llm
        ? `LLM descriptions of the regions set to describe${llm.failed ? ` · ${llm.failed} call(s) failed (see the gaps)` : ''}`
        : 'LLM descriptions of the regions set to describe',
    });
  }

  const started = ev.length > 0;
  const units = phases.length + 1;
  const doneUnits = phases.filter((p) => p.state === 'done').length + (extractDone ? 1 : 0);
  return { started, phases, done: extractDone, failed, fraction: started ? doneUnits / units : 0, llm };
}

export const PHASE_LABEL: Record<Phase['key'], string> = {
  docling: 'Docling',
  regions: 'Regions',
  llm: 'LLM descriptions',
};
