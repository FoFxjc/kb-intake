const LABELS: Record<string, string> = {
  label: 'Labelling',
  extract: 'Extracting',
  review: 'Applying review',
  deliver: 'Delivering',
};

/** Shown while a step runs on the job (running_op); the job is polled meanwhile. */
export function Busy({ op }: { op: string | null }) {
  if (!op) return null;
  return (
    <span className="busy" role="status" data-testid="busy">
      <span className="busy-dot" aria-hidden="true" />
      {LABELS[op] ?? `Running ${op}`}
      {'…'}
    </span>
  );
}
