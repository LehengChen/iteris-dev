/** Boundary map: where generalization failed and why (renders nothing when empty). */
import { timeAgo } from '../../lib/format';
import { Tag } from '../Tag';
import { ListRow, SectionCard } from '../SectionCard';
import type { BoundaryEntry } from '../../types';

export function BoundaryList({ boundary }: { boundary: BoundaryEntry[] }) {
  if (boundary.length === 0) return null;
  return (
    <SectionCard title={`Boundary map · ${boundary.length}`}>
      {boundary.map((b) => (
        <ListRow key={`${b.direction_id}-${b.recorded_at}`}>
          <Tag kind="bad">{b.verdict ?? 'blocked'}</Tag>
          <span className="row-title" title={b.direction_id}>
            {b.reason_summary ?? b.direction_id}
          </span>
          <span className="row-meta dim">{timeAgo(b.recorded_at)}</span>
        </ListRow>
      ))}
    </SectionCard>
  );
}
