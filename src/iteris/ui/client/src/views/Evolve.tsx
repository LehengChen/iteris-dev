/**
 * Evolve — the master-supervisor view for a family root project.
 *
 * The genealogy tree is the centerpiece: what directions were proposed,
 * which were seeded into child nodes, what those children spawned next.
 * Below it: the supervisor's stage-report narrative and the journal feed.
 * Pure orchestration; all rendering lives in components/evolve/*.
 */
import { useMemo, useState } from 'react';
import { useEvolve, useSupervision } from '../hooks/useApi';
import { projectNodeId } from '../lib/evolveTree';
import { timeAgo } from '../lib/format';
import { Tag } from '../components/Tag';
import { BudgetBar } from '../components/evolve/BudgetBar';
import { EvolveTree } from '../components/evolve/EvolveTree';
import { FamilyFindings } from '../components/evolve/FamilyFindings';
import { ReportsList } from '../components/evolve/ReportsList';
import { BoundaryList } from '../components/evolve/BoundaryList';
import { SupervisionFeed } from '../components/evolve/SupervisionFeed';

export function Evolve() {
  const { data, isLoading } = useEvolve();
  const journal = useSupervision().data?.items ?? [];
  const [openReportId, setOpenReportId] = useState<string | null>(null);
  // Tree selection lives here so the findings panel can focus origin nodes.
  const [selectedId, setSelectedId] = useState<string | null>(null);
  // Journal sentences resolve direction ids to their human titles.
  const journalCtx = useMemo(() => {
    const titles = new Map((data?.direction_pool ?? []).map((d) => [d.direction_id, d.title ?? d.direction_id]));
    return { directionTitle: (id: string) => titles.get(id) ?? id };
  }, [data]);

  if (isLoading) return null;
  if (!data?.initialized)
    return (
      <div className="view-message dim">
        Evolve is not initialized for this project — run <code>iteris evolve init</code> in a family root.
      </div>
    );

  return (
    <div className="evolve">
      <div className="ov-statusbar">
        <Tag kind={data.session_live ? 'live' : 'dim'}>
          {data.session_live ? 'supervisor live' : 'supervisor stopped'}
        </Tag>
        <span className="ov-stat" title={data.goal}>{data.goal}</span>
        <span className="ov-stat ov-stat--right">state updated {timeAgo(data.updated_at)}</span>
      </div>
      {data.budget && <BudgetBar budget={data.budget} />}

      <EvolveTree state={data} selectedId={selectedId} onSelect={setSelectedId} />

      <div className="evo-columns">
        <div className="evo-left">
          <FamilyFindings onFocusNode={(id) => setSelectedId(projectNodeId(id))} />
          <ReportsList openId={openReportId} onOpen={setOpenReportId} />
          <BoundaryList boundary={data.boundary ?? []} />
        </div>
        <SupervisionFeed items={journal} ctx={journalCtx} onOpenReport={setOpenReportId} />
      </div>
    </div>
  );
}
