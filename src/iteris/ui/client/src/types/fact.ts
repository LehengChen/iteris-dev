/** Durable facts and the terminal answer (Facts view). */

/** One durable fact from /api/facts (`iteris tool ui facts --json`). */
export interface Fact {
  fact_id: string;
  status: string;
  fact_type?: string | null;
  review_level?: string | null;
  claim_policy?: string | null;
  claim_summary?: string | null;
  source_project?: string | null;
  source_task?: string | null;
  predecessors: string[];
  verification?: string | null;
  path: string;
  /** Markdown body — only present on /api/fact?id= detail responses. */
  body?: string;
  updated_at: string;
  /** Present instead of the fields above when the file failed to parse. */
  error?: string;
}

/** Terminal answer from the newest passing assembly verification. */
export interface AnswerInfo {
  target_artifact?: string | null;
  fact_ids: string[];
  summary?: string;
  created_at?: string;
  goal_passed?: boolean | null;
}
