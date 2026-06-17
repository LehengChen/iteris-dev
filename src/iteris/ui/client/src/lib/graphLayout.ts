/**
 * Pure layout logic for the fact DAG — no React.
 *
 * Structure: connected components that contain at least one of the project's
 * own facts (or the answer node) form the main derivation graph at the top;
 * components made purely of inherited facts + their external parent anchors
 * are packed into a tidy grid shelf below, so reference material never
 * scatters the derivation view.
 */
import dagre from '@dagrejs/dagre';
import { ANSWER_NODE_ID } from './answer';
import type { Fact } from '../types';

export const NODE_W = 240;
export const NODE_H = 76;
export const EXT_W = 200;
export const EXT_H = 52;
export const ANSWER_W = 260;
export const ANSWER_H = 76;
const SHELF_COLS = 2;
const SHELF_GAP_X = 64;
const SHELF_GAP_Y = 32;

export interface Pair {
  source: string;
  target: string;
}
export interface Box {
  id: string;
  w: number;
  h: number;
}

/** Pixel size of a node, by kind. */
export function nodeSize(id: string, isFact: boolean): { w: number; h: number } {
  if (id === ANSWER_NODE_ID) return { w: ANSWER_W, h: ANSWER_H };
  return isFact ? { w: NODE_W, h: NODE_H } : { w: EXT_W, h: EXT_H };
}

/** dagre LR layout; returns id → top-left position. */
export function dagreLayout(boxes: Box[], edges: Pair[]): Map<string, { x: number; y: number }> {
  const g = new dagre.graphlib.Graph();
  g.setGraph({ rankdir: 'LR', align: 'UL', nodesep: 28, ranksep: 90 });
  g.setDefaultEdgeLabel(() => ({}));
  for (const b of boxes) g.setNode(b.id, { width: b.w, height: b.h });
  for (const e of edges) g.setEdge(e.source, e.target);
  dagre.layout(g);
  const out = new Map<string, { x: number; y: number }>();
  for (const b of boxes) {
    const pos = g.node(b.id);
    out.set(b.id, { x: pos.x - b.w / 2, y: pos.y - b.h / 2 });
  }
  return out;
}

/** Normalize positions so the bounding box starts at (0,0); returns its size. */
function normalize(pos: Map<string, { x: number; y: number }>, boxes: Box[]): { w: number; h: number } {
  if (boxes.length === 0) return { w: 0, h: 0 };
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  for (const b of boxes) {
    const p = pos.get(b.id)!;
    minX = Math.min(minX, p.x);
    minY = Math.min(minY, p.y);
    maxX = Math.max(maxX, p.x + b.w);
    maxY = Math.max(maxY, p.y + b.h);
  }
  for (const b of boxes) {
    const p = pos.get(b.id)!;
    pos.set(b.id, { x: p.x - minX, y: p.y - minY });
  }
  return { w: maxX - minX, h: maxY - minY };
}

export interface GraphLayout {
  positions: Map<string, { x: number; y: number }>;
  shelfLabel: { x: number; y: number } | null;
}

export function layoutGraph(facts: Fact[], externals: string[], edges: Pair[], hasAnswer: boolean): GraphLayout {
  const boxOf = new Map<string, Box>();
  for (const f of facts) boxOf.set(f.fact_id, { id: f.fact_id, w: NODE_W, h: NODE_H });
  for (const id of externals) boxOf.set(id, { id, w: EXT_W, h: EXT_H });
  if (hasAnswer) boxOf.set(ANSWER_NODE_ID, { id: ANSWER_NODE_ID, w: ANSWER_W, h: ANSWER_H });

  // Union-find over the whole graph to split connected components.
  const parent = new Map<string, string>();
  const find = (x: string): string => {
    let r = x;
    while (parent.get(r) !== r) r = parent.get(r)!;
    parent.set(x, r);
    return r;
  };
  for (const id of boxOf.keys()) parent.set(id, id);
  for (const e of edges) {
    const a = find(e.source);
    const b = find(e.target);
    if (a !== b) parent.set(a, b);
  }

  const coreRoots = new Set(
    facts.filter((f) => f.claim_policy !== 'inherited_from_parent').map((f) => find(f.fact_id)),
  );
  if (hasAnswer) coreRoots.add(find(ANSWER_NODE_ID));
  const mainIds = new Set([...boxOf.keys()].filter((id) => coreRoots.has(find(id))));

  const positions = new Map<string, { x: number; y: number }>();
  let mainSize = { w: 0, h: 0 };
  const mainBoxes = [...mainIds].map((id) => boxOf.get(id)!);
  if (mainBoxes.length > 0) {
    const pos = dagreLayout(mainBoxes, edges.filter((e) => mainIds.has(e.source) && mainIds.has(e.target)));
    mainSize = normalize(pos, mainBoxes);
    for (const [id, p] of pos) positions.set(id, p);
  }

  // Group the remaining (purely inherited) nodes by component, stable order.
  const shelfGroups = new Map<string, string[]>();
  for (const id of [...boxOf.keys()].sort()) {
    if (mainIds.has(id)) continue;
    const root = find(id);
    shelfGroups.set(root, [...(shelfGroups.get(root) ?? []), id]);
  }
  let shelfLabel: { x: number; y: number } | null = null;
  if (shelfGroups.size > 0) {
    const startY = mainBoxes.length > 0 ? mainSize.h + 110 : 40;
    shelfLabel = { x: 0, y: startY - 34 };
    const comps = [...shelfGroups.values()].sort((a, b) => a[0].localeCompare(b[0]));
    let rowY = startY;
    let rowH = 0;
    let col = 0;
    let colX = 0;
    for (const members of comps) {
      const boxes = members.map((id) => boxOf.get(id)!);
      const pos = dagreLayout(boxes, edges.filter((e) => members.includes(e.source) && members.includes(e.target)));
      const size = normalize(pos, boxes);
      if (col >= SHELF_COLS) {
        col = 0;
        colX = 0;
        rowY += rowH + SHELF_GAP_Y;
        rowH = 0;
      }
      for (const [id, p] of pos) positions.set(id, { x: colX + p.x, y: rowY + p.y });
      colX += size.w + SHELF_GAP_X;
      rowH = Math.max(rowH, size.h);
      col += 1;
    }
  }
  return { positions, shelfLabel };
}
