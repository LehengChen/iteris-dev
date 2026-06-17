/**
 * Log routes: snapshot (REST) + live tail (WebSocket).
 *
 * The WebSocket sends a baseline snapshot first, then tails appended bytes —
 * one ordered channel, so the client never has to reconcile a REST snapshot
 * with in-flight WS messages. All streams are structured executor JSONL:
 * {type:'snapshot'} with normalized entries (cut at a line boundary), then
 * per-line normalized LogEntries.
 */
import type { FastifyInstance } from 'fastify';
import fs from 'fs';
import path from 'path';
import type { ProjectPaths } from '../types.js';
import { createBridge } from '../iteris.js';
import { Normalizer } from '../normalizer.js';

/** Max normalized entries shipped in a structured snapshot (REST or WS baseline). */
const STRUCTURED_TAIL_ENTRIES = 2000;
type StructuredLogAdapter = 'codex' | 'claude';

/**
 * Resolve a project-relative log path, rejecting traversal outside the root.
 * Resolves symlinks before the containment check: per-run CODEX_HOME dirs
 * hold symlinks into ~/.codex (auth.json!), and those must not be readable
 * through this server.
 */
function resolveLogPath(projectPath: string, rel: string): string | null {
  const normalized = path.normalize(rel).replace(/^(\.\.[/\\])+/, '');
  const full = path.resolve(projectPath, normalized);
  if (full !== projectPath && !full.startsWith(projectPath + path.sep)) return null;
  let real: string;
  let realRoot: string;
  try {
    real = fs.realpathSync(full);
    realRoot = fs.realpathSync(projectPath);
  } catch {
    return null; // missing file or broken link
  }
  if (real !== realRoot && !real.startsWith(realRoot + path.sep)) return null;
  return real;
}

function inferLogAdapter(rel: string): StructuredLogAdapter {
  const normalized = rel.replace(/\\/g, '/');
  return normalized.endsWith('.jsonl') && (normalized.startsWith('projects/') || normalized.includes('/projects/'))
    ? 'claude'
    : 'codex';
}

function snapshotArgs(
  rel: string,
  projectPath: string,
  opts: { maxBytes?: number; adapter?: string },
): string[] {
  const args = ['tool', 'ui', 'snapshot', rel, '--json', projectPath];
  if (opts.maxBytes != null) args.push('--max-bytes', String(opts.maxBytes));
  args.push('--tail-entries', String(STRUCTURED_TAIL_ENTRIES));
  if (opts.adapter) args.push('--adapter', opts.adapter);
  return args;
}

/** Read [start, end) as a Buffer; resolves with whatever was read on error. */
function readRange(file: string, start: number, end: number): Promise<Buffer> {
  return new Promise((resolve) => {
    if (end <= start) return resolve(Buffer.alloc(0));
    const chunks: Buffer[] = [];
    const stream = fs.createReadStream(file, { start, end: end - 1 });
    stream.on('data', (c) => chunks.push(c as Buffer));
    stream.on('end', () => resolve(Buffer.concat(chunks)));
    stream.on('error', () => resolve(Buffer.concat(chunks)));
  });
}

/**
 * Offset of the start of the (possibly partial) last line within the first
 * `size` bytes — i.e. one past the last '\n', or 0. Scans backwards so huge
 * files don't get read in full.
 */
async function findLineStart(file: string, size: number): Promise<number> {
  const CHUNK = 64 * 1024;
  let end = size;
  while (end > 0) {
    const start = Math.max(0, end - CHUNK);
    const buf = await readRange(file, start, end);
    const idx = buf.lastIndexOf(0x0a);
    if (idx >= 0) return start + idx + 1;
    end = start;
  }
  return 0;
}

export function register(fastify: FastifyInstance, paths: ProjectPaths): void {
  const bridge = createBridge(paths.projectPath);

  // Snapshot for initial paint / polling fallback — Python normalizes the
  // structured log. Capped so a long run never ships an unbounded payload.
  fastify.get('/api/logs/*', async (req, reply) => {
    const rel = (req.params as Record<string, string>)['*'] || '';
    const query = req.query as Record<string, string>;
    const adapter = query?.adapter;
    const full = resolveLogPath(paths.projectPath, rel);
    if (!full || !fs.existsSync(full)) return reply.status(404).send({ error: 'Not found' });
    try {
      return await bridge.json(snapshotArgs(rel, paths.projectPath, { adapter }));
    } catch (e: any) {
      return reply.status(500).send({ error: e.message });
    }
  });

  // Live tail over WebSocket: baseline snapshot, then appends, in order.
  fastify.get('/api/log-stream/*', { websocket: true }, (socket, req) => {
    const rel = (req.params as Record<string, string>)['*'] || '';
    const query = req.query as Record<string, string>;
    const requestedAdapter = query?.adapter;
    let adapter = requestedAdapter || inferLogAdapter(rel);
    const full = resolveLogPath(paths.projectPath, rel);

    const send = (obj: unknown) => {
      try {
        socket.send(typeof obj === 'string' ? obj : JSON.stringify(obj));
      } catch {
        /* socket gone */
      }
    };

    if (!full || !fs.existsSync(full)) {
      send({ type: 'error', message: 'Not found' });
      socket.close();
      return;
    }

    let closed = false;
    let watcher: fs.FSWatcher | null = null;
    // One normalizer per baseline (created in sendBaseline): a re-baseline
    // gets fresh cross-line state, and closing the old one silences output
    // still in flight so stale entries can't land on top of a new snapshot.
    let normalizer: Normalizer | null = null;
    const makeNormalizer = () =>
      new Normalizer(
        paths.projectPath,
        adapter,
        (line) => send(line),
        (err) => {
          send({ type: 'error', message: `normalizer failed: ${err.message}` });
          // Close so the client notices and falls back to REST polling.
          try {
            socket.close();
          } catch {
            /* ignore */
          }
        },
      );

    let lastSize = 0;
    let carry: Buffer = Buffer.alloc(0); // partial trailing line across reads
    let reading = false;
    let dirty = false;

    const sendBaseline = async (): Promise<void> => {
      let size = 0;
      try {
        size = fs.statSync(full).size;
      } catch {
        /* vanished mid-flight; tail picks up if it reappears */
      }
      // Cut the baseline at a line boundary and tail from that exact byte,
      // so baseline and stream never overlap and no line is half-lost.
      const boundary = await findLineStart(full, size);
      if (boundary === 0) {
        // Nothing complete yet. Don't pass --max-bytes 0 to Python: 0
        // means "whole file" there, which would double the first line
        // once the tail streams it.
        send({ type: 'snapshot', format: 'structured', adapter, entries: [], truncated: false });
      } else {
        try {
          const snap = await bridge.json<Record<string, unknown>>(
            snapshotArgs(rel, paths.projectPath, { maxBytes: boundary, adapter: requestedAdapter }),
          );
          if (typeof snap.adapter === 'string') adapter = snap.adapter;
          send({ type: 'snapshot', ...snap });
        } catch (e: any) {
          send({ type: 'error', message: e.message });
          // No usable baseline — close so the client falls back to REST
          // polling instead of streaming appends on top of stale state.
          try {
            socket.close();
          } catch {
            /* ignore */
          }
        }
      }
      normalizer?.close();
      normalizer = closed ? null : makeNormalizer();
      carry = Buffer.alloc(0);
      lastSize = boundary;
    };

    // Serialized tail pump: one read at a time, re-runs if events arrived
    // mid-read, re-baselines on truncation/rotation instead of going stale.
    const drain = async (): Promise<void> => {
      if (reading || closed) {
        dirty = true;
        return;
      }
      reading = true;
      try {
        do {
          dirty = false;
          let size: number;
          try {
            size = fs.statSync(full).size;
          } catch {
            break;
          }
          if (size < lastSize) {
            await sendBaseline();
            continue;
          }
          if (size === lastSize) continue;
          const chunk = await readRange(full, lastSize, size);
          lastSize = size;
          if (closed) break;
          carry = carry.length ? Buffer.concat([carry, chunk]) : chunk;
          const idx = carry.lastIndexOf(0x0a);
          if (idx >= 0) {
            const complete = carry.subarray(0, idx).toString('utf-8');
            carry = Buffer.from(carry.subarray(idx + 1));
            for (const line of complete.split('\n')) {
              if (line.trim()) normalizer?.write(line);
            }
          }
        } while (dirty && !closed);
      } finally {
        reading = false;
      }
    };

    void (async () => {
      await sendBaseline();
      if (closed) return;
      send({ type: 'ready', size: lastSize });
      watcher = fs.watch(full, () => void drain());
      if (closed) {
        watcher.close();
        return;
      }
      await drain(); // catch appends that landed before the watcher existed
    })();

    socket.on('close', () => {
      closed = true;
      watcher?.close();
      normalizer?.close();
    });
  });
}
