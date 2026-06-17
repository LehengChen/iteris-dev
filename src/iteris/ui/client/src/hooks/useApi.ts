/**
 * Polling data hooks, generated from one endpoint registry.
 *
 * Adding a data endpoint = one row in ENDPOINTS + one exported hook line.
 * All polling hooks share the same behavior: fixed interval, keeps polling
 * in background tabs (the server coalesces + caches, so polls are cheap),
 * and a 10s fetch timeout so a stuck request can't wedge the query.
 */
import { useQuery } from '@tanstack/react-query';
import type {
  ActivityItem,
  AnswerInfo,
  DirectionDetail,
  EvolveState,
  Fact,
  FamilyMemory,
  FrontierIndex,
  IterisStatus,
  JournalEntry,
  NodeDetail,
  ReportItem,
  Stream,
  TaskPool,
} from '../types';

async function fetchJson<T>(url: string): Promise<T> {
  const res = await fetch(url, { signal: AbortSignal.timeout(10_000) });
  if (!res.ok) throw new Error(`${url} → HTTP ${res.status}`);
  try {
    return (await res.json()) as T;
  } catch {
    throw new Error(`${url} → invalid JSON response`);
  }
}

/** One row per polled endpoint; intervals are mirrored by the server cache TTLs. */
const ENDPOINTS = {
  streams: { url: '/api/streams', interval: 3000 },
  status: { url: '/api/status', interval: 5000 },
  facts: { url: '/api/facts', interval: 5000 },
  activity: { url: '/api/activity', interval: 5000 },
  frontier: { url: '/api/frontier', interval: 10000 },
  tasks: { url: '/api/tasks', interval: 5000 },
  evolve: { url: '/api/evolve', interval: 10000 },
  supervision: { url: '/api/supervision', interval: 5000 },
  reports: { url: '/api/reports', interval: 10000 },
  family: { url: '/api/family', interval: 10000 },
} as const;

function usePolling<T>(key: keyof typeof ENDPOINTS) {
  const { url, interval } = ENDPOINTS[key];
  return useQuery<T>({
    queryKey: [key],
    queryFn: () => fetchJson<T>(url),
    refetchInterval: interval,
    refetchIntervalInBackground: true,
  });
}

export const useStreams = () => usePolling<Stream[]>('streams');
export const useStatus = () => usePolling<IterisStatus>('status');
export const useFacts = () => usePolling<{ facts: Fact[]; answer?: AnswerInfo }>('facts');
export const useActivity = () => usePolling<{ items: ActivityItem[] }>('activity');
export const useFrontier = () => usePolling<FrontierIndex>('frontier');
export const useTasks = () => usePolling<TaskPool>('tasks');
export const useEvolve = () => usePolling<EvolveState>('evolve');
export const useSupervision = () => usePolling<{ items: JournalEntry[] }>('supervision');
export const useReports = () => usePolling<{ items: ReportItem[] }>('reports');
export const useFamily = () => usePolling<FamilyMemory>('family');

/** On-demand fact detail (markdown body) for the drawer; cached per id. */
export function useFactDetail(factId: string | null) {
  return useQuery<{ fact: Fact | null }>({
    queryKey: ['fact', factId],
    queryFn: () => fetchJson(`/api/fact?id=${encodeURIComponent(factId!)}`),
    enabled: factId !== null,
    staleTime: 60_000,
  });
}

/** On-demand direction detail (intent markdown + lineage) for the tree drawer. */
export function useDirectionDetail(directionId: string | null) {
  return useQuery<DirectionDetail>({
    queryKey: ['direction', directionId],
    queryFn: () => fetchJson(`/api/direction?id=${encodeURIComponent(directionId!)}`),
    enabled: directionId !== null,
    staleTime: 60_000,
  });
}

/** On-demand node outcome (result summary, final answer, curated claims). */
export function useNodeDetail(nodeId: string | null) {
  return useQuery<NodeDetail>({
    queryKey: ['evolve-node', nodeId],
    queryFn: () => fetchJson(`/api/evolve-node?id=${encodeURIComponent(nodeId!)}`),
    enabled: nodeId !== null,
    staleTime: 60_000,
  });
}
