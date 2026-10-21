// Region types as the prototype shows them: a colour (box), a tint and an ink (chip), and a label.
export interface TypeStyle {
  color: string;
  tint: string;
  ink: string;
  label: string;
}

const style = (name: string, label: string): TypeStyle => ({
  color: `var(--type-${name})`,
  tint: `var(--type-${name}-tint)`,
  ink: `var(--type-${name}-ink)`,
  label,
});

const STYLES: Record<string, TypeStyle> = {
  table: style('table', 'Table'),
  chart: style('chart', 'Chart'),
  diagram: style('diagram', 'Diagram'),
  picture: style('picture', 'Picture'),
  icon: style('picture', 'Icon'),
  logo: style('picture', 'Logo'),
  signature: style('picture', 'Signature'),
  stamp: style('picture', 'Stamp'),
  title: style('heading', 'Title'),
  heading: style('heading', 'Heading'),
  text: style('text', 'Text'),
  list: style('text', 'List'),
  caption: style('text', 'Caption'),
  footnote: style('text', 'Footnote'),
  formula: style('text', 'Formula'),
  toc: style('text', 'Contents'),
  page_header: style('text', 'Page header'),
  page_footer: style('text', 'Page footer'),
  unknown: style('picture', 'Unknown'),
};

export function typeStyle(type: string): TypeStyle {
  return STYLES[type] ?? style('text', type);
}

/** Every region type (src/kb_intake/vocab.py REGION_TYPES), the choices of a reclassify. */
export const REGION_TYPES = [
  'title', 'heading', 'text', 'list', 'caption', 'footnote', 'formula', 'toc', 'table', 'chart', 'diagram', 'picture',
  'logo', 'icon', 'signature', 'stamp', 'page_header', 'page_footer', 'unknown',
] as const;

export const TEXTUAL_TYPES = [
  'title', 'heading', 'text', 'list', 'caption', 'footnote', 'formula', 'toc', 'page_header', 'page_footer',
] as const;

/** The rows of the "Region types" card (prototype Main): one type each, and Text for all textual types. Stamps and
 * Unknown are listed only when the document has them. */
export interface TypeGroup {
  key: string;
  label: string;
  types: readonly string[];
  color: string;
  always: boolean;
}

export const TYPE_GROUPS: TypeGroup[] = [
  { key: 'table', label: 'Tables', types: ['table'], color: typeStyle('table').color, always: true },
  { key: 'chart', label: 'Charts', types: ['chart'], color: typeStyle('chart').color, always: true },
  { key: 'diagram', label: 'Diagrams', types: ['diagram'], color: typeStyle('diagram').color, always: true },
  { key: 'picture', label: 'Pictures', types: ['picture'], color: typeStyle('picture').color, always: true },
  { key: 'icon', label: 'Icons', types: ['icon'], color: typeStyle('icon').color, always: true },
  { key: 'logo', label: 'Logos', types: ['logo'], color: typeStyle('logo').color, always: true },
  { key: 'signature', label: 'Signatures', types: ['signature'], color: typeStyle('signature').color, always: true },
  { key: 'stamp', label: 'Stamps', types: ['stamp'], color: typeStyle('stamp').color, always: false },
  { key: 'unknown', label: 'Unknown', types: ['unknown'], color: typeStyle('unknown').color, always: false },
  { key: 'textual', label: 'Text (body, headings, lists…)', types: TEXTUAL_TYPES, color: typeStyle('text').color, always: true },
];
