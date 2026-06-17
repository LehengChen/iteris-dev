/** Log streams and entries (Logs view). */

export type StructuredLogAdapter = 'codex' | 'claude';

export interface Stream {
  id: string;
  kind: 'pane' | 'agent' | 'verify';
  title: string;
  path: string;
  live: boolean;
  status?: string;
  role?: string | null;
  mode?: string | null;
  task_id?: string | null;
  started_at?: string | null;
  format: 'structured';
  adapter?: StructuredLogAdapter | null;
  executor?: string | null;
  model?: string | null;
}

export interface LogEntry {
  ts: string;
  event:
    | 'shell'
    | 'thinking'
    | 'tool_call'
    | 'tool_result'
    | 'text'
    | 'session_end'
    | 'prompt';
  level?: 'info' | 'warn' | 'error';
  message?: string;
  content?: string;
  tool?: string;
  input?: Record<string, unknown>;
  raw_type?: string;
  session_id?: string;
  request_id?: string;
  tool_call_id?: string;
  model?: string;
  total_cost_usd?: number;
  input_tokens?: number;
  output_tokens?: number;
  summary?: string;
}

/** Snapshot returned by /api/logs/*. */
export interface Snapshot {
  format: 'structured';
  adapter?: StructuredLogAdapter;
  entries: LogEntry[];
  truncated?: boolean;
}
