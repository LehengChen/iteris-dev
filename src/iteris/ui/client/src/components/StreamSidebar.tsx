import type { Stream } from '../types';
import { logAdapterMeta } from '../lib/logAdapters';

interface Props {
  streams: Stream[];
  selected: Stream | null;
  onSelect: (s: Stream) => void;
}

const KIND_LABEL: Record<Stream['kind'], string> = {
  pane: 'Main loop',
  agent: 'Sub-agents',
  verify: 'Verifiers',
};

export function StreamSidebar({ streams, selected, onSelect }: Props) {
  const groups: Stream['kind'][] = ['pane', 'agent', 'verify'];

  return (
    <div className="sidebar">
      {groups.map((kind) => {
        const items = streams.filter((s) => s.kind === kind);
        if (!items.length) return null;
        return (
          <div key={kind} className="sidebar-group">
            <div className="sidebar-group-title">{KIND_LABEL[kind]}</div>
            {items.map((s) => (
              <button
                key={s.id}
                className={`stream-item ${selected?.id === s.id ? 'selected' : ''}`}
                onClick={() => onSelect(s)}
              >
                <span className={`dot ${s.live ? 'live' : 'idle'}`} />
                <span className="stream-title">{s.title}</span>
                <span className="stream-adapter" style={{ color: logAdapterMeta(s.adapter).tone }}>
                  {logAdapterMeta(s.adapter).label}
                </span>
                {s.status && <span className="stream-status">{s.status}</span>}
              </button>
            ))}
          </div>
        );
      })}
      {!streams.length && <div className="sidebar-empty">No runs yet.</div>}
    </div>
  );
}
