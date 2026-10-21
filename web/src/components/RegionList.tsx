import { useVirtualizer } from '@tanstack/react-virtual';
import { useEffect, useRef } from 'react';
import type { ApiError, RegionRow } from '../api/client';
import { flagInk, gapFlag } from '../lib/gaps';
import { typeStyle } from '../lib/types';
import { Snippet } from './Snippet';

const ROW_HEIGHT = 44;

interface Props {
  rows: RegionRow[];
  /** more rows on the server (next_cursor) */
  hasMore: boolean;
  loading: boolean;
  /** the last next-page request failed: shown in place of "Loading more…"; only Retry asks again */
  moreError: ApiError | null;
  selected: string | null;
  onPick: (row: RegionRow) => void;
  /** asked when the list scrolls near its end (not after a failed next page), and by Retry */
  onMore: () => void;
}

/** The region list (prototype Main): virtualised, so only the visible rows are in the DOM; the next page is asked
 * for when the list scrolls near its end. */
export function RegionList({ rows, hasMore, loading, moreError, selected, onPick, onMore }: Props) {
  const scroller = useRef<HTMLDivElement>(null);
  const v = useVirtualizer({
    count: rows.length + (hasMore ? 1 : 0),
    getScrollElement: () => scroller.current,
    estimateSize: () => ROW_HEIGHT,
    overscan: 12,
  });
  const items = v.getVirtualItems();
  const last = items.length ? items[items.length - 1].index : -1;

  useEffect(() => {
    if (hasMore && !loading && !moreError && last >= rows.length - 5) onMore();
  }, [last, rows.length, hasMore, loading, moreError, onMore]);

  // a new list (other filters) starts at the top
  const first = rows[0]?.region_id;
  useEffect(() => {
    scroller.current?.scrollTo({ top: 0 });
  }, [first]);

  return (
    <div ref={scroller} className="rlist" role="list" aria-label="Regions" data-testid="region-list">
      <div style={{ height: v.getTotalSize(), position: 'relative' }}>
        {items.map((it) => {
          const r = rows[it.index];
          if (!r && moreError) {
            return (
              <div
                key="more"
                className="rrow rrow-more"
                style={{ transform: `translateY(${it.start}px)` }}
                role="alert"
                data-testid="more-error"
              >
                <span className="err-text rrow-text">
                  More regions could not be loaded{moreError.status ? ` (${moreError.status})` : ''}: {moreError.detail}
                </span>
                <button type="button" className="btn btn-sm" onClick={onMore}>
                  Retry
                </button>
              </div>
            );
          }
          if (!r) {
            return (
              <div key="more" className="rrow rrow-more muted" style={{ transform: `translateY(${it.start}px)` }}>
                Loading more…
              </div>
            );
          }
          const s = typeStyle(r.type);
          const gap = r.gaps[0] ?? null;
          const text = r.match?.snippet ?? r.text_preview ?? (r.status ? `(${r.status})` : '(no text)');
          return (
            <button
              key={r.region_id}
              type="button"
              role="listitem"
              className={`rrow${r.region_id === selected ? ' on' : ''}`}
              style={{ transform: `translateY(${it.start}px)` }}
              onClick={() => onPick(r)}
              data-testid="region-row"
              data-region={r.region_id}
              data-page={r.page_no}
              data-type={r.type}
            >
              <span className="mono rrow-page">p{r.page_no}</span>
              <span className="rrow-type" style={{ background: s.tint, color: s.ink }}>
                {s.label}
              </span>
              <span className="rrow-text">
                {r.match?.source === 'cell' && <span className="rrow-src">cell · </span>}
                {r.match?.source === 'llm' && <span className="rrow-src llm">LLM · </span>}
                <Snippet text={text.replace(/\s+/g, ' ')} />
              </span>
              {r.status === 'excluded' || r.status === 'absorbed' ? (
                <span className="rrow-flag muted">{r.status}</span>
              ) : (
                <span className="rrow-flag" style={{ color: flagInk(gap) }}>
                  {gap ? gapFlag(gap) : ''}
                </span>
              )}
              <span className="mono rrow-score">{r.score === null ? '' : r.score.toFixed(2)}</span>
            </button>
          );
        })}
      </div>
    </div>
  );
}
