import type { Facets } from '../api/client';
import { gapLabel } from '../lib/gaps';
import { TYPE_GROUPS } from '../lib/types';

const fmt = (n: number) => n.toLocaleString('en-US');

interface Props {
  facets: Facets | null;
  typeKey: string;
  gap: string | null;
  onType: (key: string) => void;
  onGap: (code: string | null) => void;
}

/** The left column of Labels & inventory: region types and the open issues ("Needs attention"), with the counts
 * of GET /facets for the current search (each dimension counted without its own filter). */
export function FacetsPanel({ facets, typeKey, gap, onType, onGap }: Props) {
  const types = facets?.type ?? {};
  const count = (ts: readonly string[]) => ts.reduce((a, t) => a + (types[t] ?? 0), 0);
  const all = Object.values(types).reduce((a, n) => a + n, 0);
  const groups = TYPE_GROUPS.filter((g) => g.always || count(g.types) > 0);
  const gaps = Object.entries(facets?.gap ?? {}).filter(([, n]) => n > 0);
  return (
    <aside className="inv-side">
      <div className="card facet-card" data-testid="facet-types">
        <div className="eyebrow facet-head">Region types</div>
        <FacetButton label="All" count={facets ? fmt(all) : '…'} color="var(--ink)" on={typeKey === 'all' && !gap} onClick={() => onType('all')} />
        {groups.map((g) => (
          <FacetButton
            key={g.key}
            label={g.label}
            count={facets ? fmt(count(g.types)) : '…'}
            color={g.color}
            on={typeKey === g.key && !gap}
            onClick={() => onType(g.key)}
          />
        ))}
      </div>
      <div className="card facet-card" data-testid="facet-gaps">
        <div className="eyebrow facet-head">Needs attention</div>
        {facets && gaps.length === 0 && <div className="muted facet-empty">Nothing open</div>}
        {gaps.map(([code, n]) => (
          <FacetButton key={code} label={gapLabel(code)} count={fmt(n)} on={gap === code} onClick={() => onGap(gap === code ? null : code)} />
        ))}
      </div>
    </aside>
  );
}

function FacetButton({ label, count, color, on, onClick }: { label: string; count: string; color?: string; on: boolean; onClick: () => void }) {
  return (
    <button type="button" className={`facet${on ? ' on' : ''}`} aria-pressed={on} onClick={onClick} data-testid="facet" data-label={label}>
      <span className="facet-label">
        {color && <span className="swatch" style={{ ['--c' as string]: color }} />}
        {label}
      </span>
      <span className="mono facet-count" data-testid="facet-count">
        {count}
      </span>
    </button>
  );
}
