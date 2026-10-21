import type { Cluster } from '../api/client';
import { gapLabel } from '../lib/gaps';
import { typeStyle } from '../lib/types';

/** What the members of a similar group share, in words (docs/api-v1.md, clusters). */
export function clusterBasis(c: Cluster): string {
  const b = c.basis as Record<string, unknown>;
  switch (c.kind) {
    case 'repeated_text':
      return `Same text · same spot at the page ${b.band === 'bottom' ? 'bottom' : 'top'} · same font`;
    case 'same_shape':
      return `${typeof b.type === 'string' ? `Classifier: ${typeStyle(b.type).label.toLowerCase()}` : 'Same type'}${
        typeof b.subtype === 'string' && b.subtype ? ` (${b.subtype})` : ''
      } · similar size`;
    case 'same_table_header':
      return 'Same first-row text · same column count';
    case 'same_gap':
      return `Same issue: ${typeof b.code === 'string' ? gapLabel(b.code).toLowerCase() : 'same gap code'}`;
    default:
      return '';
  }
}

/** Exclude all only for repeated page furniture and same-shape pictures; every group can be opened. */
export const canExcludeAll = (c: Cluster) => c.kind === 'repeated_text' || c.kind === 'same_shape';

interface Props {
  cluster: Cluster;
  result?: string;
  busy: boolean;
  open: boolean;
  onExcludeAll: () => void;
  onOpen: () => void;
}

/** A similar group with its bulk actions (prototype Review, "Similar groups · bulk actions"). */
export function ClusterCard({ cluster, result, busy, open, onExcludeAll, onOpen }: Props) {
  return (
    <div className={`cluster${open ? ' on' : ''}`} data-testid="cluster" data-cluster={cluster.cluster_id}>
      <div className="cluster-top">
        <span className="cluster-label">{cluster.label}</span>
        <span className="mono muted" style={{ fontSize: 12 }}>
          ×{cluster.n}
        </span>
      </div>
      <div className="muted" style={{ fontSize: 12 }}>
        {clusterBasis(cluster)} · p{cluster.pages.slice(0, 6).join(', p')}
        {cluster.pages.length > 6 ? '…' : ''}
      </div>
      <div className="cluster-acts">
        {!result && canExcludeAll(cluster) && (
          <button type="button" className="btn btn-sm" disabled={busy} onClick={onExcludeAll}>
            Exclude all
          </button>
        )}
        {!result && (
          <button type="button" className="btn btn-sm" disabled={busy} onClick={onOpen}>
            Open this group
          </button>
        )}
        {result && (
          <span className="cluster-result" data-testid="cluster-result">
            {result}
          </span>
        )}
      </div>
    </div>
  );
}
