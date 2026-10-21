// The upload form's declaration (docs/api-v1.md, POST /v1/jobs) as YAML text. Every string is written as a
// double-quoted scalar (JSON string syntax is valid YAML), so no value is read as another type or as syntax.
export interface Declaration {
  doc_id: string;
  issuer_id: string;
  doc_type: string;
  period_end: string;
  source_url: string;
  full: boolean;
  lang: string;
}

export function declarationYaml(d: Declaration): string {
  const q = (s: string) => JSON.stringify(s.trim());
  const lines = [
    `doc_id: ${q(d.doc_id)}`,
    `issuer_id: ${q(d.issuer_id)}`,
    `doc_type: ${q(d.doc_type)}`,
    `period_end: ${q(d.period_end)}`,
    `source_url: ${q(d.source_url)}`,
    'completeness:',
    `  full: ${d.full ? 'true' : 'false'}`,
  ];
  if (d.lang.trim()) lines.push(`lang: ${q(d.lang)}`);
  return `${lines.join('\n')}\n`;
}

/** A file-name-safe doc_id from a file name, like the server's slug(): "UOB AR2025.pdf" -> "uob-ar2025". */
export function slug(fileName: string): string {
  const stem = fileName.replace(/\.[^.]*$/, '');
  const ascii = stem.normalize('NFKD').replace(/[^\x00-\x7f]/g, '').toLowerCase();
  return ascii
    .replace(/[^a-z0-9._-]+/g, '-')
    .replace(/-{2,}/g, '-')
    .replace(/^[-._]+|[-._]+$/g, '');
}
