/** Shared formatting helpers for the dashboard views. */

/** "32s ago" / "5m ago" / "3h ago" / "2d ago"; empty input → "—". */
export function timeAgo(ts?: string | null): string {
  if (!ts) return '—';
  const ms = Date.now() - new Date(ts).getTime();
  if (Number.isNaN(ms)) return '—';
  const s = Math.max(0, Math.floor(ms / 1000));
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 48) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

/** True when the timestamp falls within the last `minutes` minutes. */
export function isFresh(ts?: string | null, minutes = 10): boolean {
  if (!ts) return false;
  const ms = Date.now() - new Date(ts).getTime();
  return !Number.isNaN(ms) && ms >= 0 && ms < minutes * 60_000;
}

/** Fact lifecycle status → semantic CSS suffix (badge-<x>, fact-node--<x>). */
export function statusKind(status?: string | null): string {
  switch (status) {
    case 'verified':
      return 'ok';
    case 'reviewed':
      return 'info';
    case 'submitted':
      return 'warn';
    case 'rejected':
      return 'bad';
    default:
      return 'dim'; // draft / unknown
  }
}

/** Task status → semantic CSS suffix. */
export function taskKind(status?: string | null): string {
  switch (status) {
    case 'done':
      return 'ok';
    case 'running':
      return 'info';
    case 'ready':
    case 'review':
      return 'warn';
    case 'blocked':
    case 'rejected':
      return 'bad';
    default:
      return 'dim'; // paused / unknown
  }
}

/** Frontier status → semantic CSS suffix. */
export function frontierKind(status?: string | null): string {
  switch (status) {
    case 'promising':
    case 'active':
      return 'ok';
    case 'blocked':
      return 'bad';
    case 'stale':
      return 'dim';
    default:
      return 'info';
  }
}

/** Count items by a key, preserving first-seen order. */
export function countBy<T>(items: T[], key: (item: T) => string): Array<[string, number]> {
  const counts = new Map<string, number>();
  for (const item of items) {
    const k = key(item);
    counts.set(k, (counts.get(k) ?? 0) + 1);
  }
  return [...counts.entries()];
}

/** Trailing segment of a fact id, e.g. "fact:proj:lemma-1:2026…" → "lemma-1". */
export function factShortName(factId: string): string {
  const parts = factId.split(':');
  // Drop a trailing timestamp segment (8 digits + 'T'…) if present.
  const last = parts[parts.length - 1];
  const core = /^\d{8}T/.test(last) ? parts.slice(0, -1) : parts;
  return core[core.length - 1] || factId;
}
