/** The direction pool: every generalization direction with its lifecycle state. */
import { directionKind, directionOrder } from '../../lib/evolve';
import { timeAgo } from '../../lib/format';
import { Tag } from '../Tag';
import { ListRow, SectionCard, SectionEmpty } from '../SectionCard';
import type { Direction } from '../../types';

function Scores({ scores }: { scores?: Record<string, string> }) {
  if (!scores) return null;
  return (
    <span className="dir-scores">
      {Object.entries(scores).map(([k, v]) => (
        <span key={k} title={k}>
          {k[0].toUpperCase()}:{v}
        </span>
      ))}
    </span>
  );
}

function VetoNote({ until }: { until?: string | null }) {
  if (!until) return null;
  const open = new Date(until).getTime() > Date.now();
  return (
    <span className={`dir-veto${open ? '' : ' dim'}`}>
      {open ? `vetoable until ${new Date(until).toLocaleTimeString()}` : 'veto window closed'}
    </span>
  );
}

export function DirectionPool({ pool }: { pool: Direction[] }) {
  const sorted = [...pool].sort(
    (a, b) =>
      directionOrder(a.status) - directionOrder(b.status) ||
      (a.rank ?? 99) - (b.rank ?? 99) ||
      (b.proposed_at ?? '').localeCompare(a.proposed_at ?? ''),
  );
  return (
    <SectionCard title={`Direction pool · ${pool.length}`}>
      {sorted.length === 0 && <SectionEmpty>Empty — waiting for analysis or harvest.</SectionEmpty>}
      {sorted.map((d) => (
        <ListRow key={d.direction_id}>
          <Tag kind={directionKind(d.status)}>{d.status ?? '?'}</Tag>
          <span className="dir-rank">{d.rank != null ? `#${d.rank}` : '—'}</span>
          <span className="row-title" title={d.direction_id}>
            {d.title ?? d.direction_id}
          </span>
          <span className="row-meta">
            <span className="dim">{d.kind}</span>
            {d.tier != null && <span className="dim">T{d.tier}</span>}
            <Scores scores={d.scores} />
            {d.status === 'proposed' && <VetoNote until={d.vetoable_until} />}
            {d.status === 'running' && <span className="dim">{timeAgo(d.proposed_at)}</span>}
          </span>
        </ListRow>
      ))}
    </SectionCard>
  );
}
