import { useEffect, useRef, useState, useCallback } from 'react';
import type { LogEntry, Snapshot, Stream } from '../types';

const POLL_INTERVAL = 3000;

/**
 * Live tail for a selected stream.
 *
 * Strategy: immediate REST snapshot for a fast first paint, then a WebSocket
 * that sends its own baseline {type:'snapshot'} followed by ordered appends —
 * a single channel, so baseline and stream never have to be reconciled.
 * Falls back to 3s REST polling whenever the WebSocket is down (never
 * connected, or dropped later, e.g. on a server restart).
 */
export function useLogStream(stream: Stream | null) {
  const [entries, setEntries] = useState<LogEntry[]>([]);
  const [streaming, setStreaming] = useState(false);

  const entriesRef = useRef<LogEntry[]>([]);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  // True once the WS baseline applied and the socket is healthy: from then on
  // the WS owns the data, and late/parallel REST responses must not clobber it.
  const wsLiveRef = useRef(false);

  const applySnapshot = useCallback((snap: Snapshot) => {
    entriesRef.current = snap.entries ?? [];
    setEntries(entriesRef.current);
  }, []);

  useEffect(() => {
    if (!stream) {
      setEntries([]);
      setStreaming(false);
      return;
    }

    let cancelled = false;
    entriesRef.current = [];
    wsLiveRef.current = false;
    setEntries([]);

    const fetchSnapshot = async () => {
      try {
        const params = new URLSearchParams();
        if (stream.adapter) params.set('adapter', stream.adapter);
        const qs = params.toString();
        const res = await fetch(`/api/logs/${stream.path}${qs ? `?${qs}` : ''}`);
        if (!res.ok || cancelled) return;
        const snap = (await res.json()) as Snapshot;
        if (cancelled || wsLiveRef.current) return; // stream switched / WS owns the data
        applySnapshot(snap);
      } catch {
        /* ignore */
      }
    };

    const startPolling = () => {
      if (pollRef.current) return;
      pollRef.current = setInterval(() => {
        if (!cancelled) void fetchSnapshot();
      }, POLL_INTERVAL);
    };
    const stopPolling = () => {
      if (pollRef.current) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
    };

    void fetchSnapshot(); // fast first paint; the WS baseline supersedes it

    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const ws = new WebSocket(
      `${proto}//${location.host}/api/log-stream/${stream.path}${
        stream.adapter ? `?adapter=${encodeURIComponent(stream.adapter)}` : ''
      }`,
    );

    ws.onmessage = (ev) => {
      if (cancelled) return;
      let raw: any;
      try {
        raw = JSON.parse(ev.data);
      } catch {
        return;
      }
      if (raw.type === 'snapshot') {
        applySnapshot(raw as Snapshot);
        wsLiveRef.current = true;
        stopPolling();
        return;
      }
      if (raw.type === 'ready') {
        setStreaming(true);
        return;
      }
      if (raw.type === 'error') return;
      if (raw.ts) {
        entriesRef.current = [...entriesRef.current, raw as LogEntry];
        setEntries(entriesRef.current);
      }
    };
    const fallBack = () => {
      if (cancelled) return;
      setStreaming(false);
      wsLiveRef.current = false;
      startPolling();
    };
    ws.onclose = fallBack;
    ws.onerror = fallBack;

    return () => {
      cancelled = true;
      ws.onclose = null;
      ws.onerror = null;
      ws.close();
      stopPolling();
    };
  }, [stream, applySnapshot]);

  return { entries, streaming };
}
