import type { CSSProperties } from 'react';
import type { PageView, Region } from '../api/client';
import { asBox, toCss, type Box } from '../lib/boxes';
import { typeStyle } from '../lib/types';

interface Props {
  view: PageView;
  selected?: string | null;
  /** a search hit (region box or table cell box) */
  match?: Box | null;
  showCells?: boolean;
  /** the box a review item is about, with its tag (Review prototype) */
  focus?: { box: Box; tag: string; color?: string; done?: boolean } | null;
  /** draw absorbed regions (dashed) instead of hiding them */
  showAbsorbed?: boolean;
}

/** The page's boxes over the rendered page: regions coloured by type (the selected one thicker), table cell boxes
 * and find_tables boxes dashed (Review prototype), and a search hit. Boxes are PDF points -> % of the page. */
export function Overlay({ view, selected, match, showCells = true, focus, showAbsorbed = false }: Props) {
  const hidden = (r: Region) => r.status === 'absorbed' && !showAbsorbed;
  const css = (b: Box): CSSProperties => toCss(b, view.width, view.height) as CSSProperties;
  const finds = view.gaps
    .map((g) => ({ gap: g, box: asBox((g as Record<string, unknown>).bbox) }))
    .filter((x): x is { gap: typeof x.gap; box: Box } => x.box !== null && x.gap.code === 'table_suspected');
  return (
    <div className="overlay" data-testid="overlay">
      {view.regions.filter((r) => !hidden(r)).map((r) => {
        const b = asBox(r.bbox);
        if (!b) return null;
        const s = typeStyle(r.type);
        return (
          <div
            key={r.region_id}
            className={`ov-box${r.region_id === selected ? ' sel' : ''}${r.status === 'absorbed' ? ' absorbed' : ''}`}
            data-testid="ov-region"
            data-region={r.region_id}
            data-type={r.type}
            data-status={r.status ?? undefined}
            style={{ ...css(b), ['--c' as string]: s.color, opacity: r.status === 'excluded' ? 0.4 : 1 }}
            title={`${s.label} ${r.region_id}`}
          />
        );
      })}
      {showCells &&
        view.tables.flatMap((t) =>
          t.cells.map((c) => {
            const b = asBox(c.bbox);
            if (!b) return null;
            return (
              <div
                key={`${t.table_id}:${c.r}:${c.c}`}
                className="ov-cell"
                data-testid="ov-cell"
                style={css(b)}
              />
            );
          }),
        )}
      {finds.map(({ gap, box }, i) => (
        <div key={`find-${i}`}>
          <div className="ov-find" data-testid="ov-find" style={css(box)} title={gap.detail} />
          <div className="ov-tag" style={{ ...css(box), width: 'auto', height: 'auto', ['--c' as string]: 'var(--warn-ink)' }}>
            find_tables
          </div>
        </div>
      ))}
      {focus && (
        <>
          <div
            className={`ov-focus${focus.done ? ' done' : ''}`}
            data-testid="ov-focus"
            style={{ ...css(focus.box), ['--c' as string]: focus.color ?? 'var(--warn-ink)' }}
          />
          <div className="ov-tag" style={{ ...css(focus.box), width: 'auto', height: 'auto', ['--c' as string]: focus.color ?? 'var(--warn-ink)' }}>
            {focus.tag}
          </div>
        </>
      )}
      {match && <div className="ov-match" data-testid="ov-match" style={css(match)} />}
    </div>
  );
}
