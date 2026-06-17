/** Evolve supervisor state and supervision journal (Evolve view). */

/** Node in the evolve family tree (generalize/EVOLVE.json). */
export interface EvolveNode {
  node_id: string;
  project: string;
  kind?: string;
  parent?: string | null;
  seeded_from_direction?: string | null;
  started_at?: string;
  last_progress_at?: string | null;
  analyzed?: boolean;
  /** Child project phase synced from its STATUS.md (e.g. verified, running). */
  phase?: string;
  /** Post-run summary of what the node proved (from its generalize analysis). */
  result_summary?: string | null;
}

/** Entry in the evolve direction pool. */
export interface Direction {
  direction_id: string;
  source_node?: string;
  title?: string;
  kind?: string;
  uses_inputs?: string[];
  scores?: Record<string, string>;
  tier?: number;
  status?: string;
  rank?: number | null;
  rank_decision?: string;
  proposed_at?: string;
  vetoable_until?: string | null;
  seeded_project?: string;
  /** Synthesis directions carry their intent inline instead of a markdown file. */
  synthesis?: boolean;
  target_statement?: string | null;
  first_steps?: string[] | null;
  regularization_target?: string | null;
}

export interface BoundaryEntry {
  direction_id: string;
  verdict?: string;
  reason_summary?: string;
  recorded_at?: string;
}

/** Payload of /api/evolve (`iteris tool ui evolve --json`). */
export interface EvolveState {
  initialized: boolean;
  goal?: string;
  session_name?: string;
  session_live?: boolean;
  budget?: {
    wall_hours?: number;
    spent_hours?: number;
    remaining_hours?: number;
    exhausted?: boolean;
    running?: number;
    max_concurrent?: number;
    slots_free?: number;
    max_nodes?: number;
    nodes?: number;
  };
  nodes?: EvolveNode[];
  direction_pool?: Direction[];
  pending_veto?: string[];
  boundary?: BoundaryEntry[];
  updated_at?: string;
}

/** One supervision journal line from /api/supervision. */
export interface JournalEntry {
  entry_id: string;
  ts: string;
  entry_type: 'tick' | 'decision' | 'judgment_failed' | 'action_intent' | 'action_outcome' | 'action_refused' | string;
  payload: Record<string, any>;
  supersedes?: string;
  agent_run?: string;
  superseded: boolean;
}

/** Payload of /api/direction (`iteris tool ui direction --json`). */
export interface DirectionDetail {
  direction: Direction | null;
  /** Markdown of the direction's intent file (target statement, steps, risks). */
  content?: string;
  content_error?: string;
  seeded_node?: EvolveNode | null;
  children_directions?: Array<Pick<Direction, 'direction_id' | 'title' | 'status' | 'kind' | 'proposed_at'>>;
  boundary?: BoundaryEntry | null;
}

/** One curated claim from the family ledger (memory/family/FAMILY_INDEX.jsonl). */
export interface FamilyClaim {
  origin_fact_id?: string;
  origin_node?: string | null;
  claim_summary?: string;
  curated_summary?: string;
  family_relevance?: string;
  updated_at?: string;
  sightings?: Array<{ project?: string; status?: string }>;
}

/** One recorded dead end from failed_paths.jsonl. */
export interface FailedPath {
  ts?: string;
  source_project?: string;
  route?: string;
  reason?: string;
}

/** Payload of /api/family (`iteris tool ui family --json`). */
export interface FamilyMemory {
  claims: FamilyClaim[];
  failed_paths: FailedPath[];
}

/** Payload of /api/evolve-node (`iteris tool ui node --json`). */
export interface NodeDetail {
  node: EvolveNode | null;
  result_summary?: string | null;
  answer?: { path: string; content: string | null } | null;
  family_claims?: FamilyClaim[];
}

/** One immutable supervisor stage report from /api/reports. */
export interface ReportItem {
  report_id: string;
  headline: string;
  created_at?: string | null;
  path: string;
  content: string;
}
