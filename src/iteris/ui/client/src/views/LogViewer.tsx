import { useEffect, useState } from 'react';
import { useStreams } from '../hooks/useApi';
import { useDeepLink } from '../hooks/useDeepLink';
import { useLogStream } from '../hooks/useLogStream';
import { StreamSidebar } from '../components/StreamSidebar';
import { StructuredLog } from '../components/StructuredLog';
import { logAdapterMeta } from '../lib/logAdapters';
import type { Stream } from '../types';

export function LogViewer() {
  const { data: streams = [] } = useStreams();
  const [selected, setSelected] = useState<Stream | null>(null);

  // Deep link: /logs?stream=<id> (e.g. a judge agent_run from the Evolve view).
  const streamParam = useDeepLink(
    'stream',
    (id) => streams.some((s) => s.id === id),
    (id) => setSelected(streams.find((s) => s.id === id)!),
  );

  // Auto-select the live main loop (or first stream) on first load.
  useEffect(() => {
    if (selected || !streams.length || streamParam) return;
    const live = streams.find((s) => s.live) || streams[0];
    setSelected(live);
  }, [streams, selected, streamParam]);

  // Keep the selected stream's metadata fresh as polling updates it.
  const current = selected ? streams.find((s) => s.id === selected.id) || selected : null;
  const { entries, streaming } = useLogStream(current);
  const adapter = current ? logAdapterMeta(current.adapter) : null;

  return (
    <div className="log-viewer">
      <StreamSidebar streams={streams} selected={current} onSelect={setSelected} />
      <div className="log-main">
        {current ? (
          <>
            <div className="log-toolbar">
              <span className="log-title">{current.title}</span>
              {adapter && (
                <span className="log-adapter-badge" style={{ color: adapter.tone }}>
                  {adapter.label}
                </span>
              )}
              {current.model && <span className="log-model">{current.model}</span>}
              {streaming && <span className="live-badge">● live</span>}
              <span className="log-path">{current.path}</span>
            </div>
            <StructuredLog entries={entries} adapter={current.adapter} />
          </>
        ) : (
          <div className="log-empty">Select a stream.</div>
        )}
      </div>
    </div>
  );
}
