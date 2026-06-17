/** `iteris status --json` payload. */
import type { VerificationResult } from './frontier';

/** Payload of `iteris status --json` (fields the dashboard reads). */
export interface IterisStatus {
  session_name?: string;
  run_state?: string;
  run_active?: boolean;
  target_artifact?: string;
  target_exists?: boolean;
  facts_ok?: boolean;
  fact_count?: number;
  ready_pool_tasks?: number;
  frontier_health?: { explore_recommended?: boolean; reason?: string; needs_refresh?: boolean };
  verification_results?: VerificationResult[];
  [key: string]: unknown;
}
