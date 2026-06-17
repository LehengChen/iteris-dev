/**
 * Header pill showing whether the project has a verified terminal answer.
 * Always visible; when the answer exists it deep-links to its node on the
 * Facts graph (the graph auto-focuses via ?focus=).
 */
import { Link } from 'react-router-dom';
import { useFacts } from '../hooks/useApi';
import { answerDisplay, answerFocusUrl } from '../lib/answer';

export function AnswerStatus() {
  const { data } = useFacts();
  const { kind, label, ready } = answerDisplay(data?.answer);
  if (!ready) {
    return (
      <span className="answer-status">
        <i className={`dot dot--${kind}`} />
        {label}
      </span>
    );
  }
  return (
    <Link
      to={answerFocusUrl()}
      className="answer-status answer-status--ready"
      title={data?.answer?.target_artifact ?? undefined}
    >
      <i className={`dot dot--${kind}`} />
      {label}
    </Link>
  );
}
