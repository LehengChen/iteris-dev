/**
 * Display mappings for the evolve supervisor — all status vocabularies and
 * journal-entry summarization live here so the components stay declarative
 * and new entry types / actions only need a row in a table.
 *
 * journalDisplay turns the supervisor's machine vocabulary (triggers,
 * contracts, actuators, raw params) into plain-English sentences, resolving
 * direction ids to titles via the caller-supplied resolver.
 */
import type { JournalEntry } from '../types';

/** Direction lifecycle status → semantic dot kind (see lib/format statusKind). */
export function directionKind(status?: string | null): string {
  switch (status) {
    case 'verified':
      return 'ok';
    case 'running':
      return 'info';
    case 'proposed':
    case 'approved':
    case 'seeded':
      return 'warn';
    case 'failed':
    case 'blocked':
    case 'vetoed':
      return 'bad';
    default:
      return 'dim'; // superseded / unknown
  }
}

/** Sort the pool for display: actives first, then by rank, then recency. */
const STATUS_ORDER: Record<string, number> = {
  running: 0,
  seeded: 1,
  approved: 2,
  proposed: 3,
  verified: 4,
  blocked: 5,
  failed: 5,
  vetoed: 6,
  superseded: 7,
};
export function directionOrder(status?: string | null): number {
  return STATUS_ORDER[status ?? ''] ?? 8;
}

export interface JournalContext {
  /** direction_id → human title (falls back to the id). */
  directionTitle: (id: string) => string;
}

export interface JournalDisplay {
  kind: string;
  label: string;
  summary: string;
}

const noCtx: JournalContext = { directionTitle: (id) => id };

function names(ctx: JournalContext, ids: unknown, max = 2): string {
  if (!Array.isArray(ids) || ids.length === 0) return '';
  const titles = ids.slice(0, max).map((id) => `“${ctx.directionTitle(String(id))}”`);
  const more = ids.length > max ? ` +${ids.length - max} more` : '';
  return titles.join(', ') + more;
}

/** Outcome sentence per actuator; intent reuses it with an "…" suffix. */
function actionSummary(action: string, detail: Record<string, any>, ctx: JournalContext): string {
  switch (action) {
    case 'approve_directions':
      return `Approved ${detail.approved?.length ?? '?'} lapsed direction(s): ${names(ctx, detail.approved)}`;
    case 'apply_curation':
      return `Curated ${detail.curated ?? '?'} fact(s) into family memory (${detail.skipped ?? 0} skipped)`;
    case 'apply_ranking':
      return `Ranked ${detail.ranked ?? '?'} direction(s)${detail.dropped?.length ? `, dropped ${names(ctx, detail.dropped)}` : ''}`;
    case 'apply_plan_revision':
      return 'Revised the direction pool from the plan-revision judgment';
    case 'schedule_next':
      return detail.scheduled
        ? `Seeded and launched direction ${names(ctx, [detail.scheduled])}`
        : 'Seeded and launched the next direction';
    case 'run_analyze':
      return `Launched generalization analysis on node ${detail.node ?? detail.node_id ?? ''}`;
    case 'ingest_analysis':
      return `Ingested ${detail.added?.length ?? '?'} proposed direction(s) from analysis`;
    case 'mark_direction_verified':
      return `Marked verified: ${names(ctx, detail.verified)}`;
    case 'write_stage_report':
      return 'Published a stage report';
    case 'stop_harvest':
      return `Stopped and harvested node ${detail.node ?? detail.node_id ?? ''}`;
    case 'message_node':
    case 'send_message':
      return `Sent a steering message to ${detail.node ?? detail.node_id ?? 'a node'}`;
    case 'record':
      return 'Recorded a diagnostic note';
    default:
      return action;
  }
}

function decisionSummary(contract: string, decision: Record<string, any>, ctx: JournalContext): string {
  switch (contract) {
    case 'curate_facts':
      return `Reviewed new facts: ${decision.entries?.length ?? 0} curated, ${decision.skipped?.length ?? 0} skipped, ${decision.failed_paths?.length ?? 0} dead end(s) recorded`;
    case 'rerank_directions':
      return `Re-ranked the pool (${decision.ranking?.length ?? 0} direction(s)${decision.drops?.length ? `, ${decision.drops.length} dropped` : ''})`;
    case 'diagnose_stall':
      return decision.stalled
        ? `Diagnosed node ${decision.node_id ?? ''} as stalled → ${decision.recommendation ?? ''}`
        : `Checked a suspected stall on ${decision.node_id ?? 'a node'} — not stalled`;
    case 'write_stage_report':
      return `Drafted stage report: ${decision.headline ?? ''}`;
    case 'revise_plan':
      return `Revised the plan (${decision.pool_edits?.length ?? 0} pool edit(s), ${decision.new_synthesis_directions?.length ?? 0} new synthesis direction(s))`;
    default:
      return `Judged ${contract}`;
  }
}

/** One-line plain-English rendering of a journal entry. */
export function journalDisplay(entry: JournalEntry, ctx: JournalContext = noCtx): JournalDisplay {
  const p = entry.payload ?? {};
  switch (entry.entry_type) {
    case 'tick': {
      const fired: string[] = p.fired ?? [];
      return fired.length
        ? { kind: 'info', label: 'tick', summary: `Triggers fired: ${fired.join(', ')}` }
        : { kind: 'dim', label: 'tick', summary: 'Observed — nothing to do' };
    }
    case 'decision':
      return {
        kind: 'info',
        label: 'judged',
        summary:
          decisionSummary(p.contract ?? '?', p.decision ?? {}, ctx) +
          ((p.attempts ?? 1) > 1 ? ` (${p.attempts} attempts)` : ''),
      };
    case 'judgment_failed':
      return {
        kind: 'bad',
        label: 'judge failed',
        summary: `${p.contract ?? '?'} gave no valid decision after ${p.attempts ?? '?'} attempts: ${p.error ?? ''}`,
      };
    case 'action_intent':
      return {
        kind: entry.superseded ? 'dim' : 'warn',
        label: 'acting',
        summary: actionSummary(p.action ?? '?', p.params ?? {}, ctx) + (entry.superseded ? '' : ' …'),
      };
    case 'action_outcome':
      return {
        kind: p.ok ? 'ok' : 'bad',
        label: p.ok ? 'done' : 'failed',
        summary: p.ok ? actionSummary(p.action ?? '?', p.detail ?? {}, ctx) : `${p.action}: ${p.error ?? 'failed'}`,
      };
    case 'action_refused':
      return { kind: 'bad', label: 'refused', summary: `${p.action ?? '?'}: ${p.error ?? 'no actuator'}` };
    default:
      return { kind: 'dim', label: entry.entry_type, summary: '' };
  }
}

/** Report id (artifacts/reports/<id>/report.md) referenced by a journal entry, if any. */
export function journalReportId(entry: JournalEntry): string | null {
  if (entry.entry_type !== 'action_outcome' || entry.payload?.action !== 'write_stage_report') return null;
  const path: unknown = entry.payload?.detail?.path;
  if (typeof path !== 'string') return null;
  const m = path.match(/artifacts\/reports\/([^/]+)\//);
  return m ? m[1] : null;
}

/** Hide journal noise by default: idle ticks and superseded intents. */
export function isJournalNoise(entry: JournalEntry): boolean {
  if (entry.entry_type === 'tick' && !(entry.payload?.fired ?? []).length) return true;
  if (entry.entry_type === 'action_intent' && entry.superseded) return true;
  return false;
}
