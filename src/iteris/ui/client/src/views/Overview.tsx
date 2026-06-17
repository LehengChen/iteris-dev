/**
 * Overview — frontier-centric landing page.
 *
 * One slim status line, then the active frontiers (components/FrontierCard).
 * Everything else lives on the Facts/Evolve/Logs pages.
 */
import { useMemo, useState } from 'react';
import { useActivity, useFacts, useFrontier, useStatus } from '../hooks/useApi';
import { timeAgo } from '../lib/format';
import { Tag } from '../components/Tag';
import { FrontierCard } from '../components/FrontierCard';
import type { Fact } from '../types';

const FRONTIER_ORDER: Record<string, number> = { blocked: 0, promising: 1, active: 1 };

export function Overview() {
  const status = useStatus().data;
  const frontiers = useFrontier().data?.active_frontiers ?? [];
  const facts = useFacts().data?.facts ?? [];
  const lastActivity = useActivity().data?.items?.[0]?.ts;
  const [showStale, setShowStale] = useState(false);

  const factById = useMemo(
    () => new Map<string, Fact>(facts.filter((f) => !f.error).map((f) => [f.fact_id, f])),
    [facts],
  );
  const stale = frontiers.filter((f) => f.status === 'stale');
  const visible = frontiers
    .filter((f) => showStale || f.status !== 'stale')
    .sort(
      (a, b) =>
        (FRONTIER_ORDER[a.status ?? ''] ?? 2) - (FRONTIER_ORDER[b.status ?? ''] ?? 2) ||
        (a.title ?? a.frontier_id).localeCompare(b.title ?? b.frontier_id),
    );

  return (
    <div className="overview">
      <div className="ov-statusbar">
        <Tag kind={status?.run_active ? 'live' : 'dim'}>{status?.run_state ?? 'unknown'}</Tag>
        <span className="ov-stat">{status?.session_name ?? '—'}</span>
        <span className="ov-stat ov-stat--right">last activity {timeAgo(lastActivity)}</span>
      </div>

      <div className="frontier-list">
        {frontiers.length === 0 && (
          <div className="view-message dim">No frontiers yet — the run will map its routes here.</div>
        )}
        {visible.map((f) => (
          <FrontierCard key={f.frontier_id} frontier={f} factById={factById} />
        ))}
        {stale.length > 0 && (
          <button className="stale-toggle" onClick={() => setShowStale(!showStale)}>
            {showStale ? 'hide' : 'show'} {stale.length} stale frontier{stale.length > 1 ? 's' : ''}
          </button>
        )}
      </div>
    </div>
  );
}
