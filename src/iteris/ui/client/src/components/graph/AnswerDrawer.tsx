/** Drawer content for the terminal answer node. */
import { answerDisplay } from '../../lib/answer';
import { factShortName, timeAgo } from '../../lib/format';
import { Tag } from '../Tag';
import { DrawerBody, DrawerLinks, DrawerMeta, DrawerTitle, GraphDrawer } from './GraphDrawer';
import type { AnswerInfo, Fact } from '../../types';

export function AnswerDrawer({ answer, facts, onFocus, onClose }: {
  answer: AnswerInfo;
  facts: Fact[];
  onFocus: (id: string) => void;
  onClose: () => void;
}) {
  const display = answerDisplay(answer);
  const known = new Set(facts.map((f) => f.fact_id));
  return (
    <GraphDrawer head={<Tag kind={display.kind}>{display.label}</Tag>} onClose={onClose}>
      <DrawerTitle title="Verified answer" subtitle={answer.target_artifact} />
      <DrawerMeta
        rows={[
          ['assembly', 'passed'],
          ['goal', answer.goal_passed == null ? 'not run' : answer.goal_passed ? 'passed' : 'failed'],
          ['facts cited', answer.fact_ids.length],
          ['verified', timeAgo(answer.created_at)],
        ]}
      />
      <DrawerLinks
        title="Built from"
        links={answer.fact_ids.map((id) => {
          const fact = facts.find((f) => f.fact_id === id);
          return known.has(id)
            ? { id, label: factShortName(id), title: fact?.claim_summary ?? undefined, onClick: () => onFocus(id) }
            : { id, label: `${factShortName(id)} (missing)`, title: id };
        })}
      />
      {answer.summary && <DrawerBody text={answer.summary} />}
    </GraphDrawer>
  );
}
