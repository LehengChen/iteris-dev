/** Frontiers and the task pool (Overview). */

/** Entry in FRONTIER_INDEX.json's active_frontiers (loosely typed). */
export interface FrontierEntry {
  frontier_id: string;
  title?: string;
  status?: string;
  hypothesis?: string;
  fact_ids?: string[];
  blocker_fact_ids?: string[];
  last_progress_at?: string;
  health?: { is_blocked?: boolean; explore_recommended?: boolean };
  [key: string]: unknown;
}

export interface FrontierIndex {
  active_frontiers?: FrontierEntry[];
  completion_gaps?: string[];
  closed_lanes?: unknown[];
  updated_at?: string;
  [key: string]: unknown;
}

/** Task from tasks/TASK_POOL.json. */
export interface PoolTask {
  task_id: string;
  mode?: string;
  objective?: string;
  status: string;
  priority?: number;
  dependencies?: string[];
  assigned_agent_run?: string | null;
  updated_at?: string;
}

export interface TaskPool {
  tasks?: PoolTask[];
  active_frontier?: string | null;
  updated_at?: string;
}

export interface VerificationResult {
  request_id?: string;
  mode?: string;
  verdict?: string;
  passed?: boolean;
  summary?: string;
  claim?: string;
  created_at?: string;
}
