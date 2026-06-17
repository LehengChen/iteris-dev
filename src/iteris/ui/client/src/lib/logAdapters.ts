import type { StructuredLogAdapter } from '../types';

export interface LogAdapterMeta {
  id: StructuredLogAdapter;
  label: string;
  tone: string;
}

const ADAPTERS: Record<StructuredLogAdapter, LogAdapterMeta> = {
  codex: { id: 'codex', label: 'Codex', tone: '#2563eb' },
  claude: { id: 'claude', label: 'Claude', tone: '#7c3aed' },
};

export function logAdapterMeta(adapter?: string | null): LogAdapterMeta {
  return ADAPTERS[adapter === 'claude' ? 'claude' : 'codex'];
}
