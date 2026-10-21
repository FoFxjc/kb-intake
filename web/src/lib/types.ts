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
