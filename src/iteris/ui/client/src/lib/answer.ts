/**
 * Shared contract for the terminal-answer pseudo-node.
 *
 * The answer is not a fact file; the graph draws it under this reserved id,
 * and anything that wants to deep-link to it (header pill, future views) must
 * use the same id via answerFocusUrl() — keep all of that knowledge here.
 */
import type { AnswerInfo } from '../types';

export const ANSWER_NODE_ID = '__answer__';

export function answerFocusUrl(): string {
  return `/facts?focus=${encodeURIComponent(ANSWER_NODE_ID)}`;
}

/** Display state shared by the header pill and the graph node. */
export function answerDisplay(answer: AnswerInfo | null | undefined): {
  kind: string;
  label: string;
  ready: boolean;
} {
  if (!answer) return { kind: 'dim', label: 'no answer yet', ready: false };
  if (answer.goal_passed) return { kind: 'ok', label: 'answer · goal verified', ready: true };
  return { kind: 'ok', label: 'answer assembled', ready: true };
}
