/**
 * Shared markdown renderer (reports, direction intents, fact statements).
 * react-markdown ignores raw HTML by default, so untrusted agent output is
 * safe to render; GFM adds the tables that stage reports use.
 */
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

export function Markdown({ text }: { text: string }) {
  return (
    <div className="md">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown>
    </div>
  );
}
