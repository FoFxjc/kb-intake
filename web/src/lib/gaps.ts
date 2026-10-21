// What gap codes and review groups mean to a reader (src/kb_intake/vocab.py GAP_LABELS, queue.py GROUPS) and how
// the prototype marks them: a short flag for list rows, a colour for the group dot.

export const GAP_LABELS: Record<string, string> = {
  table_suspected: 'Suspected missed tables',
  low_confidence: 'Low confidence',
  table_words_unassigned: 'Unassigned values in tables',
  table_unmatched: 'Tables not matched between stages',
  unhandled: 'Unhandled regions',
  no_text_layer: 'No text layer',
  lang_mismatch: 'Language differs from the declaration',
  extractor_degraded: 'Extractor degraded',
  llm_failed: 'LLM calls that failed',
  continued_hint: 'Continuation hints (weak)',
  llm_check: 'Chart descriptions to check',
};

export const gapLabel = (code: string) => GAP_LABELS[code] ?? code;

/** The short flag of a list row (prototype Main: "Missed table?", "Low confidence", ...). */
const FLAGS: Record<string, string> = {
  table_suspected: 'Missed table?',
  low_confidence: 'Low confidence',
  table_words_unassigned: 'Unassigned value',
  table_unmatched: 'Unmatched table',
  unhandled: 'Unhandled',
  no_text_layer: 'No text layer',
  lang_mismatch: 'Language?',
  extractor_degraded: 'Degraded',
  llm_failed: 'LLM failed',
};

export const gapFlag = (code: string) => FLAGS[code] ?? code;

/** Low confidence is amber, every other issue orange (prototype Main). */
export const flagInk = (code: string | null | undefined) =>
  !code ? 'var(--muted)' : code === 'low_confidence' ? 'var(--call-ink)' : 'var(--warn-ink)';

/** The dot of a review group (prototype Review). */
export function groupDot(key: string): string {
  switch (key) {
    case 'low_confidence':
      return 'var(--call-ink)';
    case 'continued_hint':
      return 'var(--type-table)';
    case 'llm_check':
      return 'var(--type-chart)';
    case 'no_text_layer':
    case 'unhandled':
      return 'var(--type-picture)';
    default:
      return 'var(--warn-ink)';
  }
}
