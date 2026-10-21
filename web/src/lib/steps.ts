import type { Tab } from './route';

// The flow of the step bar (prototype Main / Workflow / Review / Deliver) and each step's state for a job:
// done (green), now (the page shown), run (the step running in the background), next (waits for the user),
// todo (grey).
export type StepState = 'done' | 'now' | 'run' | 'next' | 'todo';

export interface Step {
  key: string;
  label: string;
  tab: Tab | null;
  /** the job states (index in STATES) from which the step is done */
  doneFrom: number;
  /** the running_op that runs this step */
  op?: string;
  /** the step waits for the user once the job reaches this state */
  callAt?: number;
}

export const STATES = ['uploaded', 'labeled', 'planned', 'approved', 'extracted', 'in_review', 'confirmed', 'delivered'];

export const STEPS: Step[] = [
  { key: 'upload', label: 'Upload', tab: null, doneFrom: 0 },
  { key: 'label', label: 'Label', tab: null, doneFrom: 1, op: 'label' },
  { key: 'stats', label: 'Stats & inventory', tab: 'labels', doneFrom: 2 },
  { key: 'workflow', label: 'Workflow', tab: 'workflow', doneFrom: 3, callAt: 2 },
  { key: 'approve', label: 'Approve', tab: 'workflow', doneFrom: 3 },
  { key: 'extract', label: 'Extract', tab: 'progress', doneFrom: 4, op: 'extract' },
  { key: 'review', label: 'Review', tab: 'review', doneFrom: 6, op: 'review', callAt: 5 },
  { key: 'confirm', label: 'Confirm', tab: 'deliver', doneFrom: 6 },
  { key: 'deliver', label: 'Deliver', tab: 'deliver', doneFrom: 7, op: 'deliver' },
];

const NOW_STEP: Record<Tab, string> = {
  labels: 'stats',
  workflow: 'workflow',
  progress: 'extract',
  review: 'review',
  deliver: 'confirm',
};

/** The step of the page shown: the deliver page is Confirm until the job is delivered, then Deliver. */
function nowStep(tab: Tab, status: string): string {
  return tab === 'deliver' && status === 'delivered' ? 'deliver' : NOW_STEP[tab];
}

export function stepState(step: Step, status: string, runningOp: string | null, tab: Tab | null): StepState {
  const at = STATES.indexOf(status);
  if (runningOp && step.op === runningOp) return 'run';
  if (tab && nowStep(tab, status) === step.key) return 'now';
  if (at >= step.doneFrom) return 'done';
  if (step.callAt !== undefined && at >= step.callAt && !runningOp) return 'next';
  return 'todo';
}

export const STEP_MARK: Record<StepState, string> = { done: '✓ ', now: '● ', run: '… ', next: '→ ', todo: '' };
