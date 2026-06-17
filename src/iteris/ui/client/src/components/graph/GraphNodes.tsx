/**
 * Custom React Flow node renderers for the fact DAG.
 *
 * Every renderer is memoized on its data fields: selection changes rebuild
 * the node array (new data objects), and without memo React Flow re-renders
 * every custom node on each click. React Query's structural sharing keeps
 * the underlying fact objects referentially stable across polls, so the
 * `fact` identity check below is sufficient.
 */
import { memo } from 'react';
import { Handle, Position, type Node, type NodeProps } from '@xyflow/react';
import { factShortName, isFresh, statusKind } from '../../lib/format';
import { answerDisplay } from '../../lib/answer';
import { Tag } from '../Tag';
import type { AnswerInfo, Fact } from '../../types';

export type FactNodeData = { fact: Fact; selected: boolean; dimmed: boolean };
export type ExtNodeData = { factId: string; dimmed: boolean };
export type AnswerNodeData = { answer: AnswerInfo; selected: boolean; dimmed: boolean };

function classes(...parts: Array<string | false>): string {
  return parts.filter(Boolean).join(' ');
}

const FactNode = memo(function FactNode({ data }: NodeProps<Node<FactNodeData>>) {
  const { fact, selected, dimmed } = data;
  const inherited = fact.claim_policy === 'inherited_from_parent';
  return (
    <div
      className={classes(
        'fact-node',
        `fact-node--${statusKind(fact.status)}`,
        inherited && 'fact-node--inherited',
        isFresh(fact.updated_at) && 'fact-node--fresh',
        selected && 'fact-node--selected',
        dimmed && 'fact-node--dimmed',
      )}
    >
      <Handle type="target" position={Position.Left} className="fact-handle" />
      <div className="fact-node-meta">
        <Tag kind={statusKind(fact.status)}>{fact.status}</Tag>
        <span className="fact-node-type">{fact.fact_type}</span>
        {inherited && <span className="fact-node-type">inherited</span>}
      </div>
      <div className="fact-node-summary">{fact.claim_summary ?? factShortName(fact.fact_id)}</div>
      <Handle type="source" position={Position.Right} className="fact-handle" />
    </div>
  );
}, (prev, next) =>
  prev.data.fact === next.data.fact &&
  prev.data.selected === next.data.selected &&
  prev.data.dimmed === next.data.dimmed);

const ExternalNode = memo(function ExternalNode({ data }: NodeProps<Node<ExtNodeData>>) {
  return (
    <div
      className={classes('fact-node', 'fact-node--external', data.dimmed && 'fact-node--dimmed')}
      title={data.factId}
    >
      <div className="fact-node-summary">{factShortName(data.factId)}</div>
      <div className="fact-node-type">external fact</div>
      <Handle type="source" position={Position.Right} className="fact-handle" />
    </div>
  );
}, (prev, next) => prev.data.factId === next.data.factId && prev.data.dimmed === next.data.dimmed);

const AnswerNode = memo(function AnswerNode({ data }: NodeProps<Node<AnswerNodeData>>) {
  const { answer, selected, dimmed } = data;
  const display = answerDisplay(answer);
  return (
    <div
      className={classes(
        'fact-node',
        'fact-node--answer',
        selected && 'fact-node--selected',
        dimmed && 'fact-node--dimmed',
      )}
      title={answer.summary}
    >
      <Handle type="target" position={Position.Left} className="fact-handle" />
      <div className="fact-node-meta">
        <Tag kind={display.kind}>{display.label}</Tag>
      </div>
      <div className="fact-node-summary">{answer.target_artifact ?? 'Verified answer'}</div>
    </div>
  );
}, (prev, next) =>
  prev.data.answer === next.data.answer &&
  prev.data.selected === next.data.selected &&
  prev.data.dimmed === next.data.dimmed);

const LabelNode = memo(function LabelNode({ data }: NodeProps<Node<{ text: string }>>) {
  return <div className="graph-label">{data.text}</div>;
});

export const nodeTypes = { fact: FactNode, external: ExternalNode, answer: AnswerNode, label: LabelNode };
