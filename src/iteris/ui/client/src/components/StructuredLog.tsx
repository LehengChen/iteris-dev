import { useState } from 'react';
import { logAdapterMeta } from '../lib/logAdapters';
import type { LogEntry } from '../types';

const EVENT_COLOR: Record<string, string> = {
  thinking: '#7c3aed',
  tool_call: '#2563eb',
  tool_result: '#10b981',
  text: '#475569',
  shell: '#ea580c',
  session_end: '#0891b2',
  prompt: '#94a3b8',
};

function headline(e: LogEntry): string {
  switch (e.event) {
    case 'tool_call':
      return `${e.tool || 'tool'}: ${truncate(stringifyInput(e.input), 160)}`;
    case 'tool_result':
      return truncate(e.content || '', 160);
    case 'thinking':
    case 'text':
      return truncate(e.content || e.message || '', 200);
    case 'shell':
      return truncate(e.message || e.content || '', 160);
    case 'session_end':
      return `Session end · ${e.input_tokens ?? '?'}→${e.output_tokens ?? '?'} tok${
        e.total_cost_usd != null ? ` · $${e.total_cost_usd.toFixed(2)}` : ''
      }`;
    case 'prompt':
      return truncate(e.content || '', 160);
    default:
      return e.raw_type || e.event;
  }
}

function stringifyInput(input?: Record<string, unknown>): string {
  if (!input) return '';
  if (typeof input.command === 'string') return input.command;
  return JSON.stringify(input);
}

function truncate(s: string, n: number): string {
  const flat = s.replace(/\s+/g, ' ').trim();
  return flat.length > n ? flat.slice(0, n) + '…' : flat;
}

function formatTs(ts: string): string {
  const match = ts.match(/^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}:\d{2})(?:\.\d+)?(Z|[+-]\d{2}:\d{2})?$/);
  if (!match) return ts;
  return `${match[1]} ${match[2]}${match[3] || ''}`;
}

function detail(e: LogEntry): string | null {
  if (e.event === 'tool_call') return stringifyInput(e.input) || null;
  if (e.event === 'tool_result' || e.event === 'thinking' || e.event === 'text')
    return e.content || null;
  if (e.event === 'session_end') return e.summary || null;
  return null;
}

function Line({ e }: { e: LogEntry }) {
  const [open, setOpen] = useState(false);
  const d = detail(e);
  const expandable = d != null && d.length > 160;
  return (
    <div className="log-line">
      <button
        className="log-headline"
        onClick={() => expandable && setOpen((v) => !v)}
        style={{ cursor: expandable ? 'pointer' : 'default' }}
      >
        <span className="log-ts" title={e.ts}>
          {formatTs(e.ts)}
        </span>
        <span className="log-event" style={{ color: EVENT_COLOR[e.event] || '#475569' }}>
          {e.event}
        </span>
        <span className="log-text">{headline(e)}</span>
      </button>
      {open && d && <pre className="log-detail">{d}</pre>}
    </div>
  );
}

const FILTERS = ['text', 'thinking', 'tool_call', 'tool_result', 'shell', 'prompt', 'session_end'] as const;

export function StructuredLog({ entries, adapter }: { entries: LogEntry[]; adapter?: string | null }) {
  const [active, setActive] = useState<Set<string>>(new Set(FILTERS));
  const meta = logAdapterMeta(adapter);
  const toggle = (f: string) =>
    setActive((prev) => {
      const next = new Set(prev);
      next.has(f) ? next.delete(f) : next.add(f);
      return next;
    });
  // Unknown event types (not covered by the filter chips) stay visible.
  const visible = entries
    .map((e, idx) => ({ e, idx }))
    .filter(({ e }) => active.has(e.event) || !(FILTERS as readonly string[]).includes(e.event));

  return (
    <div className="structured-log">
      <div className="filter-bar">
        {FILTERS.map((f) => (
          <button
            key={f}
            className={`chip ${active.has(f) ? 'on' : ''}`}
            onClick={() => toggle(f)}
          >
            {f}
          </button>
        ))}
        <span className="entry-count">{meta.label} · {visible.length} entries</span>
      </div>
      <div className="log-entries">
        {visible
          .slice()
          .reverse()
          .map(({ e, idx }) => (
            // Key by position in the source array: streaming appends new
            // entries without remounting (and collapsing) the existing lines.
            <Line key={idx} e={e} />
          ))}
      </div>
    </div>
  );
}
