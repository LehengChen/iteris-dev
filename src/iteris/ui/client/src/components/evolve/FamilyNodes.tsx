/** Family tree of evolve nodes (root + seeded generalization projects). */
import { timeAgo } from '../../lib/format';
import { Tag } from '../Tag';
import { ListRow, SectionCard, SectionEmpty } from '../SectionCard';
import type { EvolveNode } from '../../types';

function nodeKindTag(kind?: string): string {
  if (kind === 'root') return 'info';
  if (kind === 'abstract') return 'ok';
  return 'warn'; // instantiate / synthesis / other
}

/** Depth-first order so children render under their parents, indented. */
function orderByTree(nodes: EvolveNode[]): Array<{ node: EvolveNode; depth: number }> {
  const byParent = new Map<string | null, EvolveNode[]>();
  for (const n of nodes) {
    const key = n.parent ?? null;
    byParent.set(key, [...(byParent.get(key) ?? []), n]);
  }
  const out: Array<{ node: EvolveNode; depth: number }> = [];
  const ids = new Set(nodes.map((n) => n.node_id));
  const roots = nodes.filter((n) => !n.parent || !ids.has(n.parent));
  const walk = (node: EvolveNode, depth: number) => {
    out.push({ node, depth });
    for (const child of byParent.get(node.node_id) ?? []) walk(child, depth + 1);
  };
  for (const r of roots) walk(r, 0);
  return out;
}

export function FamilyNodes({ nodes }: { nodes: EvolveNode[] }) {
  return (
    <SectionCard title={`Family nodes · ${nodes.length}`}>
      {nodes.length === 0 && <SectionEmpty>No nodes yet.</SectionEmpty>}
      {orderByTree(nodes).map(({ node, depth }) => (
        <ListRow key={node.node_id} style={{ paddingLeft: 14 + depth * 22 }}>
          <Tag kind={nodeKindTag(node.kind)}>{node.kind ?? '?'}</Tag>
          <span className="row-title" title={node.project}>
            {node.node_id}
          </span>
          <span className="row-meta">
            {node.seeded_from_direction && (
              <span className="dim" title={node.seeded_from_direction}>
                ← {node.seeded_from_direction.replace(/^dir-/, '')}
              </span>
            )}
            <span className="dim">progress {timeAgo(node.last_progress_at)}</span>
          </span>
        </ListRow>
      ))}
    </SectionCard>
  );
}
