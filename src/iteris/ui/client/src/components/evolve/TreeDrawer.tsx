/**
 * Drawer for the evolve tree, composed from the shared GraphDrawer
 * primitives.
 *
 * Both modes lead with the *outcome* (what was actually proved) and only then
 * show the *intent*: a direction's markdown is authored at proposal time and
 * never updated after seeding, so its Target/First steps/Risks must not read
 * as current state once the node has run.
 */
import { useDirectionDetail, useNodeDetail } from '../../hooks/useApi';
import { directionKind } from '../../lib/evolve';
import { phaseDisplay, type TreeSelection } from '../../lib/evolveTree';
import { timeAgo } from '../../lib/format';
import { Tag } from '../Tag';
import { DrawerBody, DrawerLinks, DrawerMeta, DrawerTitle, GraphDrawer } from '../graph/GraphDrawer';
import type { Direction, EvolveState } from '../../types';

interface Nav {
  onSelectDirection: (directionId: string) => void;
  onSelectNode: (nodeId: string) => void;
  onClose: () => void;
}

function scoresText(scores?: Record<string, string>): string {
  if (!scores) return '—';
  return Object.entries(scores)
    .map(([k, v]) => `${k}:${v}`)
    .join('  ');
}

/** Labelled prose block above the drawer body (outcome summaries, claims). */
function DrawerSection({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="fact-drawer-links">
      <div className="fact-drawer-sub">{title}</div>
      {children}
    </div>
  );
}

/** Synthesis directions carry their intent inline instead of a markdown file. */
function synthesisIntent(d: Direction): string | null {
  const parts: string[] = [];
  if (d.target_statement) parts.push(`## Target statement\n\n${d.target_statement}`);
  if (d.regularization_target) parts.push(`## Regularization target\n\n${d.regularization_target}`);
  if (d.first_steps?.length) parts.push(`## First steps\n\n${d.first_steps.map((s, i) => `${i + 1}. ${s}`).join('\n')}`);
  return parts.length > 0 ? parts.join('\n\n') : null;
}

function DirectionDrawer({ direction, nav }: { direction: Direction; nav: Nav }) {
  const detail = useDirectionDetail(direction.direction_id).data;
  const seeded = detail?.seeded_node;
  const children = detail?.children_directions ?? [];
  const outcome = seeded?.result_summary;
  const intent = detail?.content ?? (detail ? synthesisIntent(detail.direction ?? direction) : null);
  const intentText =
    intent ??
    detail?.content_error ??
    (detail ? 'No intent markdown recorded for this direction.' : 'Loading intent...');
  const settled = ['verified', 'superseded', 'vetoed'].includes(direction.status ?? '');
  const collapseIntent = Boolean(outcome) || settled;
  return (
    <GraphDrawer head={<Tag kind={directionKind(direction.status)}>{direction.status ?? '?'}</Tag>} onClose={nav.onClose}>
      <DrawerTitle title={direction.title ?? direction.direction_id} subtitle={direction.direction_id} />
      <DrawerMeta
        rows={[
          ['kind', direction.kind],
          ['tier / rank', `T${direction.tier ?? '?'} / ${direction.rank != null ? `#${direction.rank}` : 'unranked'}`],
          ['proposal score', scoresText(direction.scores)],
          ['inputs', (direction.uses_inputs ?? []).join(', ') || '—'],
          ['proposed', timeAgo(direction.proposed_at)],
          ['proposed by', direction.source_node ?? '—'],
        ]}
      />
      {outcome ? (
        <DrawerSection title="Outcome — what the seeded node proved">
          <p className="drawer-para">{outcome}</p>
        </DrawerSection>
      ) : (
        seeded && phaseDisplay(seeded.phase).finalized && (
          <DrawerSection title="Outcome">
            <span className="dim">
              Node verified, analysis pending — open the seeded node for its final answer.
            </span>
          </DrawerSection>
        )
      )}
      {seeded && (
        <DrawerLinks
          title="Seeded node"
          links={[
            {
              id: seeded.node_id,
              label: `${seeded.node_id} — ${phaseDisplay(seeded.phase).label}`,
              title: seeded.project,
              onClick: () => nav.onSelectNode(seeded.node_id),
            },
          ]}
        />
      )}
      {detail?.boundary && (
        <DrawerSection title="Boundary">
          <span className="dim">{detail.boundary.reason_summary}</span>
        </DrawerSection>
      )}
      {children.length > 0 && (
        <DrawerLinks
          title="Spawned directions"
          links={children.map((c) => ({
            id: c.direction_id!,
            label: `${c.title ?? c.direction_id} (${c.status ?? '?'})`,
            onClick: () => nav.onSelectDirection(c.direction_id!),
          }))}
        />
      )}
      {collapseIntent ? (
        <details className="drawer-details">
          <summary>Original proposal</summary>
          <DrawerBody markdown={Boolean(intent)} text={intentText} />
        </details>
      ) : (
        <>
          <div className="fact-drawer-sub drawer-body-label">Proposal intent</div>
          <DrawerBody markdown={Boolean(intent)} text={intentText} />
        </>
      )}
    </GraphDrawer>
  );
}

function ProjectDrawer({ nodeId, state, nav }: { nodeId: string; state: EvolveState; nav: Nav }) {
  const node = (state.nodes ?? []).find((n) => n.node_id === nodeId);
  const detail = useNodeDetail(node ? nodeId : null).data;
  if (!node) return null;
  const pool = state.direction_pool ?? [];
  const proposed = pool.filter((d) => d.source_node === node.node_id);
  const seededFrom = pool.find((d) => d.direction_id === node.seeded_from_direction);
  const phase = phaseDisplay(node.phase);
  const summary = detail?.result_summary ?? node.result_summary;
  const claims = detail?.family_claims ?? [];
  const answer = detail?.answer;
  return (
    <GraphDrawer
      head={
        <span title={phase.tooltip}>
          <Tag kind={phase.kind}>{phase.label}</Tag>
        </span>
      }
      onClose={nav.onClose}
    >
      <DrawerTitle title={seededFrom?.title ?? node.node_id} subtitle={node.node_id} />
      <DrawerMeta
        rows={[
          ['kind', node.kind],
          ['started', timeAgo(node.started_at)],
          ['last progress', timeAgo(node.last_progress_at)],
          ['analyzed', node.analyzed ? 'yes' : 'not yet'],
        ]}
      />
      {summary ? (
        <DrawerSection title="Outcome — what this node proved">
          <p className="drawer-para">{summary}</p>
        </DrawerSection>
      ) : (
        phase.finalized && (
          <DrawerSection title="Outcome">
            <span className="dim">
              Verified, analysis pending — the outcome summary appears once the supervisor analyzes this node.
            </span>
          </DrawerSection>
        )
      )}
      {claims.length > 0 && (
        <DrawerSection title="Curated into family memory">
          {claims.map((c) => (
            <p key={c.origin_fact_id} className="drawer-para" title={c.family_relevance}>
              {c.curated_summary ?? c.claim_summary}
            </p>
          ))}
        </DrawerSection>
      )}
      {node.seeded_from_direction && (
        <DrawerLinks
          title="Seeded from"
          links={[
            {
              id: node.seeded_from_direction,
              label: seededFrom?.title ?? node.seeded_from_direction,
              onClick: () => nav.onSelectDirection(node.seeded_from_direction!),
            },
          ]}
        />
      )}
      <DrawerLinks
        title="Proposed directions"
        links={proposed.map((d) => ({
          id: d.direction_id,
          label: `${d.title ?? d.direction_id} (${d.status ?? '?'})`,
          onClick: () => nav.onSelectDirection(d.direction_id),
        }))}
      />
      {answer?.content ? (
        <>
          <div className="fact-drawer-sub drawer-body-label" title={answer.path}>
            Final answer — self-contained record ({answer.path})
          </div>
          <DrawerBody markdown text={answer.content} />
        </>
      ) : (
        <DrawerBody
          text={
            detail
              ? phase.finalized
                ? 'Final answer document not found in the child project.'
                : 'No final answer yet — the node has not finished.'
              : 'Loading outcome…'
          }
        />
      )}
    </GraphDrawer>
  );
}

export function TreeDrawer({ selection, state, onSelectDirection, onSelectNode, onClose }: {
  selection: TreeSelection;
  state: EvolveState;
} & Nav) {
  const nav = { onSelectDirection, onSelectNode, onClose };
  if (selection.kind === 'direction') return <DirectionDrawer direction={selection.direction} nav={nav} />;
  return <ProjectDrawer nodeId={selection.node.node_id} state={state} nav={nav} />;
}
