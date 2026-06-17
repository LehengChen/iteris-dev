/** Drawer content for a durable fact node. */
import { useFactDetail } from '../../hooks/useApi';
import { factShortName, statusKind, timeAgo } from '../../lib/format';
import { Tag } from '../Tag';
import { DrawerBody, DrawerLinks, DrawerMeta, DrawerTitle, GraphDrawer } from './GraphDrawer';
import type { Fact } from '../../types';

export function FactDrawer({ fact, facts, onFocus, onClose }: {
  fact: Fact;
  facts: Fact[];
  onFocus: (id: string) => void;
  onClose: () => void;
}) {
  // The polled list omits markdown bodies; fetch this fact's on demand.
  const detail = useFactDetail(fact.fact_id);
  const body = fact.body ?? detail.data?.fact?.body;
  const known = new Set(facts.map((f) => f.fact_id));
  const successors = facts.filter((f) => f.predecessors.includes(fact.fact_id));
  return (
    <GraphDrawer head={<Tag kind={statusKind(fact.status)}>{fact.status}</Tag>} onClose={onClose}>
      <DrawerTitle title={fact.claim_summary} subtitle={fact.fact_id} />
      <DrawerMeta
        rows={[
          ['type', fact.fact_type],
          ['review', fact.review_level],
          ['policy', fact.claim_policy],
          ['task', fact.source_task],
          ['verification', fact.verification ?? 'none', true],
          ['updated', timeAgo(fact.updated_at)],
          ['file', fact.path, true],
        ]}
      />
      <DrawerLinks
        title="Depends on"
        links={fact.predecessors.map((p) =>
          known.has(p)
            ? { id: p, label: factShortName(p), onClick: () => onFocus(p) }
            : { id: p, label: `${factShortName(p)} (external)`, title: p },
        )}
      />
      <DrawerLinks
        title="Used by"
        links={successors.map((s) => ({
          id: s.fact_id,
          label: factShortName(s.fact_id),
          onClick: () => onFocus(s.fact_id),
        }))}
      />
      <DrawerBody markdown={Boolean(body)} text={body ?? (detail.isLoading ? 'Loading statement…' : 'Statement unavailable.')} />
    </GraphDrawer>
  );
}
