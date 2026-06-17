/** Activity feed items (Overview). */

/** One feed item from /api/activity (`iteris tool ui activity --json`). */
export interface ActivityItem {
  ts: string;
  type: 'verification_result' | 'verification_pending' | 'fact';
  id?: string | null;
  mode?: string | null;
  title: string;
  verdict?: string | null;
  passed?: boolean | null;
  status?: string | null;
}
