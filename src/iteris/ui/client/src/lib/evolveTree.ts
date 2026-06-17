/**
 * Genealogy tree of an evolve run — pure data, no React.
 *
 * Layers alternate project-node → direction → seeded project-node → …:
 * a family node proposes directions (direction.source_node), an approved
 * direction is seeded into a child node (node.seeded_from_direction), and
 * verified children propose the next generation. Source nodes that are not
 * adopted family members (typically the root project itself) become a
 * synthetic root so every direction stays attached to the tree.
 */
import { dagreLayout, type Pair } from './graphLayout';
import type { Direction, EvolveNode, EvolveState } from '../types';

export const TREE_PROJECT_W = 200;
export const TREE_PROJECT_H = 58;
export const TREE_DIR_W = 230;
export const TREE_DIR_H = 64;

export type TreeSelection = { kind: 'direction'; direction: Direction } | { kind: 'project'; node: EvolveNode };

export interface EvolveTreeModel {
  /** title: the seeding direction's human title (node ids are slugs). */
  projects: Array<{ id: string; node: EvolveNode; synthetic: boolean; title: string }>;
  directions: Array<{ id: string; direction: Direction }>;
  edges: Pair[];
  positions: Map<string, { x: number; y: number }>;
}

export const projectNodeId = (nodeId: string) => `node:${nodeId}`;
export const directionNodeId = (directionId: string) => `dir:${directionId}`;

export function buildEvolveTree(state: EvolveState): EvolveTreeModel {
  const nodes = state.nodes ?? [];
  const pool = state.direction_pool ?? [];
  const known = new Set(nodes.map((n) => n.node_id));

  // Synthetic anchors for proposer ids that are not adopted family nodes.
  const synthetic = [...new Set(pool.map((d) => d.source_node ?? '?').filter((s) => !known.has(s)))];
  const titleOf = new Map(pool.map((d) => [d.direction_id, d.title]));
  const projects = [
    ...synthetic.map((s) => ({
      id: projectNodeId(s),
      node: { node_id: s, project: s, kind: 'root' } as EvolveNode,
      synthetic: true,
      title: s,
    })),
    ...nodes.map((n) => ({
      id: projectNodeId(n.node_id),
      node: n,
      synthetic: false,
      title: (n.seeded_from_direction && titleOf.get(n.seeded_from_direction)) || n.node_id,
    })),
  ];
  const directions = pool.map((d) => ({ id: directionNodeId(d.direction_id), direction: d }));

  const edges: Pair[] = [];
  for (const d of pool) {
    edges.push({ source: projectNodeId(d.source_node ?? '?'), target: directionNodeId(d.direction_id) });
  }
  for (const n of nodes) {
    if (n.seeded_from_direction) {
      edges.push({ source: directionNodeId(n.seeded_from_direction), target: projectNodeId(n.node_id) });
    }
  }

  const positions = dagreLayout(
    [
      ...projects.map((p) => ({ id: p.id, w: TREE_PROJECT_W, h: TREE_PROJECT_H })),
      ...directions.map((d) => ({ id: d.id, w: TREE_DIR_W, h: TREE_DIR_H })),
    ],
    edges,
  );
  return { projects, directions, edges, positions };
}

/** "2026-06-10T20:43:25Z" → "06-10 20:43" (ordering hint on tree nodes). */
export function shortTime(ts?: string | null): string {
  if (!ts) return '';
  const m = ts.match(/^\d{4}-(\d{2}-\d{2})T(\d{2}:\d{2})/);
  return m ? `${m[1]} ${m[2]}` : '';
}

/**
 * Child-project phase → display. `goal_success_verified` is the only
 * machine-stamped phase (written by `iteris tool goal finalize` after the
 * goal-success verification passes; the supervisor keys on exactly it).
 * Everything else ("verified", "complete", …) is free-form prose the worker
 * agent wrote into STATUS.md mid-run — self-reported, not finalized.
 */
export interface PhaseDisplay {
  label: string;
  kind: string;
  finalized: boolean;
  tooltip: string;
}

export function phaseDisplay(phase?: string | null): PhaseDisplay {
  if (phase === 'goal_success_verified') {
    return {
      label: '✓ goal verified',
      kind: 'ok',
      finalized: true,
      tooltip: 'Finalized: the goal-success verification passed — the supervisor counts this node as done.',
    };
  }
  const tooltip = 'Worker-reported phase from the node\'s STATUS.md — not yet finalized by a goal-success verification.';
  if (!phase) return { label: 'no phase yet', kind: 'dim', finalized: false, tooltip };
  let kind = 'warn';
  if (phase.includes('fail') || phase.includes('blocked')) kind = 'bad';
  else if (phase.includes('verified') || phase.includes('complete')) kind = 'ok';
  else if (phase.includes('running') || phase.includes('active') || phase.includes('progress')) kind = 'info';
  return { label: phase, kind, finalized: false, tooltip };
}
