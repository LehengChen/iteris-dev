/** Shared server types. */

export interface ProjectPaths {
  /** Absolute path to the Iteris project root. */
  projectPath: string;
  /** Absolute path to <project>/.iteris. */
  iterisPath: string;
  /** Absolute path to <project>/.iteris/logs. */
  logsPath: string;
}

/** A single observable log/stream surfaced in the sidebar. */
export interface Stream {
  /** Stable id (e.g. run_id or "pane:<session>"). */
  id: string;
  kind: 'pane' | 'agent' | 'verify';
  title: string;
  /** Path relative to the project root, used for /api/logs and /api/log-stream. */
  path: string;
  /** Whether the underlying process is currently alive. */
  live: boolean;
  status?: string;
  role?: string | null;
  mode?: string | null;
  task_id?: string | null;
  started_at?: string | null;
  /** Executor JSONL, normalized into LogEntry lines. */
  format: 'structured';
  adapter?: 'codex' | 'claude' | null;
  executor?: string | null;
  model?: string | null;
}

/**
 * Unified log entry — the normalized schema the client renders.
 * Mirrors Archon's LogEntry so the frontend renderer can be reused.
 */
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
  // session_end / usage
  total_cost_usd?: number;
  input_tokens?: number;
  output_tokens?: number;
  summary?: string;
}
