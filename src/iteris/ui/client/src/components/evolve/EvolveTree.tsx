/**
 * The evolve genealogy as an interactive React Flow tree.
 *
 * Project nodes (root + seeded children, phase-colored) alternate with
 * direction nodes (status + proposed time as the ordering hint). Clicking
 * either opens the TreeDrawer with the full story; selection dims the
 * non-neighbourhood like the fact graph does.
 */
import { memo, useMemo } from 'react';
import {
  Background,
  Controls,
  Handle,
  MarkerType,
  Position,
  ReactFlow,
  ReactFlowProvider,
  type Edge,
  type Node,
  type NodeProps,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';

import {
  buildEvolveTree,
  directionNodeId,
  phaseDisplay,
  projectNodeId,
  shortTime,
  TREE_DIR_H,
  TREE_DIR_W,
  TREE_PROJECT_H,
  TREE_PROJECT_W,
} from '../../lib/evolveTree';
import { directionKind } from '../../lib/evolve';
import { Tag } from '../Tag';
import { TreeDrawer } from './TreeDrawer';
import type { Direction, EvolveNode, EvolveState } from '../../types';

type ProjectData = { node: EvolveNode; synthetic: boolean; title: string; selected: boolean; dimmed: boolean };
type DirectionData = { direction: Direction; selected: boolean; dimmed: boolean };

const ProjectNode = memo(function ProjectNode({ data }: NodeProps<Node<ProjectData>>) {
  const { node, synthetic, title, selected, dimmed } = data;
  return (
    <div
      className={[
        'tree-node',
        'tree-node--project',
        synthetic ? 'tree-node--root' : '',
        selected ? 'fact-node--selected' : '',
        dimmed ? 'fact-node--dimmed' : '',
      ].join(' ')}
      title={synthetic ? node.project : `${node.node_id}\n${node.project}`}
    >
      <Handle type="target" position={Position.Left} className="fact-handle" />
      <div className="fact-node-meta">
        {synthetic ? (
          <Tag kind="info">root</Tag>
        ) : (
          (() => {
            const ph = phaseDisplay(node.phase);
            return (
              <span title={ph.tooltip} className={ph.finalized ? undefined : 'tag--soft'}>
                <Tag kind={ph.kind}>{ph.label}</Tag>
              </span>
            );
          })()
        )}
        {!synthetic && <span className="fact-node-type">{node.kind}</span>}
      </div>
      <div className="fact-node-summary tree-node-name">{title}</div>
      <Handle type="source" position={Position.Right} className="fact-handle" />
    </div>
  );
}, (prev, next) =>
  prev.data.node === next.data.node &&
  prev.data.title === next.data.title &&
  prev.data.selected === next.data.selected &&
  prev.data.dimmed === next.data.dimmed);

const DirectionNode = memo(function DirectionNode({ data }: NodeProps<Node<DirectionData>>) {
  const { direction, selected, dimmed } = data;
  return (
    <div
      className={[
        'tree-node',
        'tree-node--direction',
        selected ? 'fact-node--selected' : '',
        dimmed ? 'fact-node--dimmed' : '',
      ].join(' ')}
      title={direction.direction_id}
    >
      <Handle type="target" position={Position.Left} className="fact-handle" />
      <div className="fact-node-meta">
        <Tag kind={directionKind(direction.status)}>{direction.status ?? '?'}</Tag>
        <span className="fact-node-type">{direction.kind}</span>
        <span className="tree-node-time">{shortTime(direction.proposed_at)}</span>
      </div>
      <div className="fact-node-summary">{direction.title ?? direction.direction_id}</div>
      <Handle type="source" position={Position.Right} className="fact-handle" />
    </div>
  );
}, (prev, next) =>
  prev.data.direction === next.data.direction &&
  prev.data.selected === next.data.selected &&
  prev.data.dimmed === next.data.dimmed);

const nodeTypes = { project: ProjectNode, direction: DirectionNode };

export function EvolveTree({ state, selectedId, onSelect }: {
  state: EvolveState;
  /** Selection is owned by the parent view so other panels can focus tree nodes. */
  selectedId: string | null;
  onSelect: (id: string | null) => void;
}) {
  const model = useMemo(() => buildEvolveTree(state), [state]);

  const neighborhood = useMemo(() => {
    if (!selectedId) return null;
    const set = new Set([selectedId]);
    for (const e of model.edges) {
      if (e.source === selectedId) set.add(e.target);
      if (e.target === selectedId) set.add(e.source);
    }
    return set;
  }, [selectedId, model.edges]);

  const nodes: Node[] = useMemo(() => {
    const dim = (id: string) => (neighborhood ? !neighborhood.has(id) : false);
    const at = (id: string) => model.positions.get(id) ?? { x: 0, y: 0 };
    return [
      ...model.projects.map((p) => ({
        id: p.id,
        type: 'project',
        position: at(p.id),
        data: { node: p.node, synthetic: p.synthetic, title: p.title, selected: selectedId === p.id, dimmed: dim(p.id) },
        width: TREE_PROJECT_W,
        height: TREE_PROJECT_H,
      })),
      ...model.directions.map((d) => ({
        id: d.id,
        type: 'direction',
        position: at(d.id),
        data: { direction: d.direction, selected: selectedId === d.id, dimmed: dim(d.id) },
        width: TREE_DIR_W,
        height: TREE_DIR_H,
      })),
    ];
  }, [model, selectedId, neighborhood]);

  const edges: Edge[] = useMemo(
    () =>
      model.edges.map((e) => {
        const touches = selectedId !== null && (e.source === selectedId || e.target === selectedId);
        return {
          id: `${e.source}>${e.target}`,
          source: e.source,
          target: e.target,
          markerEnd: { type: MarkerType.ArrowClosed, width: 14, height: 14, color: touches ? '#6366f1' : undefined },
          style: touches ? { stroke: '#6366f1', strokeWidth: 2 } : selectedId ? { opacity: 0.25 } : undefined,
        };
      }),
    [model.edges, selectedId],
  );

  const selection = useMemo(() => {
    if (!selectedId) return null;
    const dir = model.directions.find((d) => d.id === selectedId);
    if (dir) return { kind: 'direction' as const, direction: dir.direction };
    const proj = model.projects.find((p) => p.id === selectedId && !p.synthetic);
    return proj ? { kind: 'project' as const, node: proj.node } : null;
  }, [selectedId, model]);

  return (
    <div className="evolve-tree">
      <ReactFlowProvider>
        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={nodeTypes}
          onNodeClick={(_e, node) => onSelect(node.id)}
          onPaneClick={() => onSelect(null)}
          nodesDraggable={false}
          nodesConnectable={false}
          fitView
          fitViewOptions={{ padding: 0.1, maxZoom: 1 }}
          minZoom={0.15}
          proOptions={{ hideAttribution: true }}
        >
          <Background gap={24} />
          <Controls showInteractive={false} />
        </ReactFlow>
      </ReactFlowProvider>
      <div className="tree-legend">
        <span><i className="dot dot--warn" /> direction: proposed → approved → running</span>
        <span><i className="dot dot--ok" /> verified (its node finished)</span>
        <span><i className="dot dot--dim" /> superseded / vetoed</span>
        <span className="tree-legend-sep" />
        <span>node: <em>worker-reported phase</em> vs <strong>✓ goal verified</strong> (finalized by goal-success verification)</span>
      </div>
      {selection && (
        <TreeDrawer
          selection={selection}
          state={state}
          onSelectDirection={(id) => onSelect(directionNodeId(id))}
          onSelectNode={(id) => onSelect(projectNodeId(id))}
          onClose={() => onSelect(null)}
        />
      )}
    </div>
  );
}
