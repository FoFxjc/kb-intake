import type { Job } from '../api/client';
import { jobHref, type Tab } from '../lib/route';
import { STEP_MARK, STEPS, stepState } from '../lib/steps';

/** The flow of the job (prototype nav "Flow"): done / now / run / next / todo per step. */
export function StepBar({ job, tab }: { job: Job; tab: Tab | null }) {
  return (
    <nav className="steps" aria-label="Flow">
      {STEPS.map((step) => {
        const state = stepState(step, job.status, job.running_op, tab);
        const text = `${STEP_MARK[state]}${step.label}${state === 'next' ? ' (your call)' : ''}`;
        const cls = `step step-${state}`;
        return step.tab && state !== 'now' ? (
          <a key={step.key} className={cls} href={jobHref(job.id, step.tab)} data-state={state}>
            {text}
          </a>
        ) : (
          <span key={step.key} className={cls} data-state={state} aria-current={state === 'now' ? 'step' : undefined}>
            {text}
          </span>
        );
      })}
    </nav>
  );
}
