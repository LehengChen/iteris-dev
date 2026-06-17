/**
 * One expandable frontier: the agent's hypothesis for a route, the facts on
 * it (deep-linking into the Facts graph), blockers and tasks.
 */
import { memo, useState } from 'react';
import { Link } from 'react-router-dom';
import { factShortName, frontierKind, statusKind, timeAgo } from '../lib/format';
import { Tag } from './Tag';
import type { Fact, FrontierEntry } from '../types';

function FactChip({ factId, fact }: { factId: string; fact?: Fact }) {
  return (
    <Link
      to={`/facts?focus=${encodeURIComponent(factId)}`}
      className="fchip"
      title={fact?.claim_summary ?? factId}
    >
      <i className={`dot dot--${fact ? statusKind(fact.status) : 'dim'}`} />
      {factShortName(factId)}
    </Link>
  );
}

function ChipGroup({ label, ids, factById }: { label: string; ids: string[]; factById: Map<string, Fact> }) {
  if (ids.length === 0) return null;
  return (
    <div>
      <div className="frontier-sub">{label}</div>
      <div className="chips">
        {ids.map((id) => (
          <FactChip key={id} factId={id} fact={factById.get(id)} />
        ))}
      </div>
    </div>
  );
}

export const FrontierCard = memo(function FrontierCard({ frontier, factById }: {
  frontier: FrontierEntry;
  factById: Map<string, Fact>;
}) {
  const [open, setOpen] = useState(false);
  const blockers = frontier.blocker_fact_ids ?? [];
  const tasks = [
    ...((frontier.active_tasks as string[] | undefined) ?? []),
    ...((frontier.blocked_tasks as string[] | undefined) ?? []),
  ];
  const hypothesis = frontier.hypothesis || (frontier.summary as string | undefined) || '';
  return (
    <div className={`frontier-card${open ? ' frontier-card--open' : ''}`}>
      <button className="frontier-head" onClick={() => setOpen(!open)}>
        <Tag kind={frontierKind(frontier.status)}>{frontier.status ?? 'active'}</Tag>
        <span className="frontier-title">{frontier.title ?? frontier.frontier_id}</span>
        {blockers.length > 0 && <span className="frontier-note">{blockers.length} blocker</span>}
        <span className="frontier-time">{timeAgo(frontier.last_progress_at)}</span>
        <span className="frontier-chevron">{open ? '▾' : '▸'}</span>
      </button>
      {open && (
        <div className="frontier-body">
          {hypothesis && (
            <div>
              <div className="frontier-sub">Hypothesis</div>
              <p className="frontier-hypo">{hypothesis}</p>
            </div>
          )}
          <ChipGroup label="Facts on route" ids={frontier.fact_ids ?? []} factById={factById} />
          <ChipGroup label="Blocked by" ids={blockers} factById={factById} />
          {tasks.length > 0 && (
            <div>
              <div className="frontier-sub">Tasks</div>
              <div className="chips">
                {tasks.map((id) => (
                  <span key={id} className="fchip">{id}</span>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
});
