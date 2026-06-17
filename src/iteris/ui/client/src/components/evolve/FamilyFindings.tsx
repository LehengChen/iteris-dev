/**
 * Family findings — the big-picture record of what the evolve family has
 * actually established: curated claims from the family ledger
 * (memory/family/FAMILY_INDEX.jsonl, newest first) plus known dead ends.
 * Each row opens a self-contained drawer; the origin link focuses the node
 * in the genealogy tree.
 */
import { useState } from 'react';
import { useFamily } from '../../hooks/useApi';
import { timeAgo } from '../../lib/format';
import { Tag } from '../Tag';
import { ListRow, SectionCard, SectionEmpty } from '../SectionCard';
import { DrawerBody, DrawerLinks, DrawerMeta, DrawerTitle, GraphDrawer } from '../graph/GraphDrawer';
import type { FailedPath, FamilyClaim } from '../../types';

function claimBody(claim: FamilyClaim): string {
  const parts: string[] = [];
  if (claim.curated_summary) parts.push(`### Curated summary\n\n${claim.curated_summary}`);
  if (claim.family_relevance) parts.push(`### Why it matters to the family\n\n${claim.family_relevance}`);
  return parts.join('\n\n') || 'No curated summary recorded.';
}

function ClaimDrawer({ claim, onFocusNode, onClose }: {
  claim: FamilyClaim;
  onFocusNode: (nodeId: string) => void;
  onClose: () => void;
}) {
  return (
    <GraphDrawer head={<Tag kind="ok">family claim</Tag>} onClose={onClose}>
      <DrawerTitle title={claim.claim_summary} subtitle={claim.origin_fact_id} />
      <DrawerMeta
        rows={[
          ['curated', timeAgo(claim.updated_at)],
          ['sightings', (claim.sightings ?? []).map((s) => `${s.project} (${s.status})`).join(', ') || '—', true],
        ]}
      />
      {claim.origin_node && (
        <DrawerLinks
          title="Origin node"
          links={[
            {
              id: claim.origin_node,
              label: claim.origin_node,
              onClick: () => onFocusNode(claim.origin_node!),
            },
          ]}
        />
      )}
      <DrawerBody markdown text={claimBody(claim)} />
    </GraphDrawer>
  );
}

function DeadEndDrawer({ path, onClose }: { path: FailedPath; onClose: () => void }) {
  return (
    <GraphDrawer head={<Tag kind="bad">dead end</Tag>} onClose={onClose}>
      <DrawerTitle title={path.route} subtitle={path.source_project} />
      <DrawerMeta rows={[['recorded', timeAgo(path.ts)], ['found by', path.source_project, true]]} />
      <DrawerBody
        markdown
        text={`### Why this route is closed\n\n${path.reason ?? 'No reason recorded.'}\n\n*Workers are told not to re-explore this without new information.*`}
      />
    </GraphDrawer>
  );
}

export function FamilyFindings({ onFocusNode }: { onFocusNode: (nodeId: string) => void }) {
  const data = useFamily().data;
  const claims = data?.claims ?? [];
  const deadEnds = data?.failed_paths ?? [];
  const [open, setOpen] = useState<string | null>(null);

  const openClaim = open?.startsWith('claim:') ? claims[Number(open.slice(6))] : null;
  const openDead = open?.startsWith('dead:') ? deadEnds[Number(open.slice(5))] : null;
  return (
    <SectionCard title={`Family findings · ${claims.length}`}>
      {claims.length === 0 && (
        <SectionEmpty>Nothing curated yet — claims appear after the supervisor curates verified facts.</SectionEmpty>
      )}
      {claims.map((c, i) => (
        <button key={c.origin_fact_id ?? i} className="feed-row-btn" onClick={() => setOpen(`claim:${i}`)}>
          <ListRow>
            <Tag kind="ok">claim</Tag>
            <span className="row-title row-title--wrap">{c.claim_summary}</span>
            <span className="row-meta dim">{timeAgo(c.updated_at)}</span>
          </ListRow>
        </button>
      ))}
      {deadEnds.map((p, i) => (
        <button key={`${p.ts}-${i}`} className="feed-row-btn" onClick={() => setOpen(`dead:${i}`)}>
          <ListRow dim>
            <Tag kind="bad">dead end</Tag>
            <span className="row-title row-title--wrap">{p.route}</span>
            <span className="row-meta dim">{timeAgo(p.ts)}</span>
          </ListRow>
        </button>
      ))}
      {openClaim && <ClaimDrawer claim={openClaim} onFocusNode={onFocusNode} onClose={() => setOpen(null)} />}
      {openDead && <DeadEndDrawer path={openDead} onClose={() => setOpen(null)} />}
    </SectionCard>
  );
}
