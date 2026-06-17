/**
 * FactGraph — the fact DAG, laid out left→right (predecessors → conclusions).
 *
 * This view only orchestrates: data → layout (lib/graphLayout) → React Flow
 * nodes/edges (components/graph/GraphNodes) → drawers (FactDrawer /
 * AnswerDrawer). Focus: clicking a node highlights its direct neighbours and
 * dims the rest; drawer links and /facts?focus=<id> deep links glide the
 * camera to the target node.
 *
 * Layout stability while watching a run: dagre only re-runs when the topology
 * (node ids + edge ids) changes; status-only refreshes restyle in place.
 */
import { useMemo, useState } from 'react';
import {
  Background,
  Controls,
  MarkerType,
  ReactFlow,
  ReactFlowProvider,
  useReactFlow,
  type Edge,
  type Node,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';

import { useFacts } from '../hooks/useApi';
import { useDeepLink } from '../hooks/useDeepLink';
import { ANSWER_NODE_ID } from '../lib/answer';
import { layoutGraph, nodeSize, type Pair } from '../lib/graphLayout';
import { nodeTypes } from '../components/graph/GraphNodes';
import { FactDrawer } from '../components/graph/FactDrawer';
import { AnswerDrawer } from '../components/graph/AnswerDrawer';

function FactGraphInner() {
  const { data, isLoading, error } = useFacts();
  const facts = useMemo(() => (data?.facts ?? []).filter((f) => !f.error && f.fact_id), [data]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const { setCenter } = useReactFlow();

  const known = useMemo(() => new Set(facts.map((f) => f.fact_id)), [facts]);
  const externals = useMemo(
    () => [...new Set(facts.flatMap((f) => f.predecessors).filter((p) => !known.has(p)))],
    [facts, known],
  );
  // Terminal answer node: only drawn when at least one cited fact is on the graph.
  const answer = useMemo(() => {
    const a = data?.answer;
    return a && a.fact_ids.some((id) => known.has(id)) ? a : null;
  }, [data, known]);
  const edgePairs: Pair[] = useMemo(
    () => [
      ...facts.flatMap((f) => f.predecessors.map((p) => ({ source: p, target: f.fact_id }))),
      ...(answer
        ? answer.fact_ids.filter((id) => known.has(id)).map((id) => ({ source: id, target: ANSWER_NODE_ID }))
        : []),
    ],
    [facts, answer, known],
  );

  // Re-layout only when the topology changes; data-only refreshes keep positions.
  const topoKey = useMemo(
    () =>
      [...facts.map((f) => f.fact_id), ...externals].sort().join('|') +
      '#' +
      edgePairs.map((e) => `${e.source}>${e.target}`).sort().join('|'),
    [facts, externals, edgePairs],
  );
  const layout = useMemo(() => {
    void topoKey;
    return layoutGraph(facts, externals, edgePairs, answer !== null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [topoKey]);
  const { positions, shelfLabel } = layout;

  // Focus: select + glide the camera to a node (drawer links, deep links).
  const focusNode = (id: string) => {
    setSelectedId(id);
    const p = positions.get(id);
    if (p) {
      const { w, h } = nodeSize(id, known.has(id));
      setCenter(p.x + w / 2, p.y + h / 2, { zoom: 1.05, duration: 600 });
    }
  };

  // Deep link: /facts?focus=<fact_id|answer> (Overview chips, header pill).
  useDeepLink('focus', (id) => positions.has(id), focusNode);

  // Direct neighbourhood of the selection; everything else dims.
  const neighborhood = useMemo(() => {
    if (!selectedId) return null;
    const set = new Set([selectedId]);
    for (const e of edgePairs) {
      if (e.source === selectedId) set.add(e.target);
      if (e.target === selectedId) set.add(e.source);
    }
    return set;
  }, [selectedId, edgePairs]);

  const nodes: Node[] = useMemo(() => {
    const dim = (id: string) => (neighborhood ? !neighborhood.has(id) : false);
    const at = (id: string) => positions.get(id) ?? { x: 0, y: 0 };
    const out: Node[] = [
      ...facts.map((f) => ({
        id: f.fact_id,
        type: 'fact',
        position: at(f.fact_id),
        data: { fact: f, selected: f.fact_id === selectedId, dimmed: dim(f.fact_id) },
        ...nodeSize(f.fact_id, true),
      })),
      ...externals.map((id) => ({
        id,
        type: 'external',
        position: at(id),
        data: { factId: id, dimmed: dim(id) },
        ...nodeSize(id, false),
      })),
    ];
    if (answer) {
      out.push({
        id: ANSWER_NODE_ID,
        type: 'answer',
        position: at(ANSWER_NODE_ID),
        data: { answer, selected: selectedId === ANSWER_NODE_ID, dimmed: dim(ANSWER_NODE_ID) },
        ...nodeSize(ANSWER_NODE_ID, false),
      });
    }
    if (shelfLabel) {
      out.push({
        id: '__shelf-label__',
        type: 'label',
        position: shelfLabel,
        data: { text: 'Inherited / reference facts' },
        selectable: false,
        draggable: false,
      });
    }
    return out;
  }, [facts, externals, answer, positions, shelfLabel, selectedId, neighborhood]);

  const edges: Edge[] = useMemo(
    () =>
      edgePairs.map((e) => {
        const touches = selectedId !== null && (e.source === selectedId || e.target === selectedId);
        return {
          id: `${e.source}>${e.target}`,
          source: e.source,
          target: e.target,
          markerEnd: {
            type: MarkerType.ArrowClosed,
            width: 16,
            height: 16,
            color: touches ? '#6366f1' : undefined,
          },
          style: touches
            ? { stroke: '#6366f1', strokeWidth: 2 }
            : selectedId
              ? { opacity: 0.18 }
              : undefined,
        };
      }),
    [edgePairs, selectedId],
  );

  const selectedFact = facts.find((f) => f.fact_id === selectedId) ?? null;
  const close = () => setSelectedId(null);

  if (error) return <div className="view-message">Failed to load facts: {String(error)}</div>;
  if (!isLoading && facts.length === 0)
    return <div className="view-message dim">No facts yet — they will appear here as the run produces them.</div>;

  return (
    <div className="fact-graph">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        onNodeClick={(_e, node) => {
          if (node.type === 'fact' || node.type === 'answer') focusNode(node.id);
        }}
        onPaneClick={close}
        nodesDraggable={false}
        nodesConnectable={false}
        fitView
        fitViewOptions={{ padding: 0.15, maxZoom: 1 }}
        minZoom={0.2}
        proOptions={{ hideAttribution: true }}
      >
        <Background gap={24} />
        <Controls showInteractive={false} />
      </ReactFlow>
      {selectedFact && <FactDrawer fact={selectedFact} facts={facts} onFocus={focusNode} onClose={close} />}
      {selectedId === ANSWER_NODE_ID && answer && (
        <AnswerDrawer answer={answer} facts={facts} onFocus={focusNode} onClose={close} />
      )}
    </div>
  );
}

export function FactGraph() {
  return (
    <ReactFlowProvider>
      <FactGraphInner />
    </ReactFlowProvider>
  );
}
