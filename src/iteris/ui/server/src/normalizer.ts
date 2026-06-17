/**
 * Per-socket structured event normalizer.
 *
 * Iteris writes executor-specific JSONL (Codex rollouts, Claude transcripts).
 * These streams are stateful, so we feed raw lines into a long-lived
 * `iteris tool ui normalize --stream --adapter <name>` process and read back
 * unified-schema LogEntry lines. One normalizer per live socket keeps
 * cross-line state correct.
 */
import { spawn, type ChildProcessByStdio } from 'child_process';
import type { Readable, Writable } from 'stream';

export class Normalizer {
  private proc: ChildProcessByStdio<Writable, Readable, null>;
  private dead = false;
  private closing = false;
  private buffer = '';

  constructor(
    projectPath: string,
    adapter: string,
    onEntry: (line: string) => void,
    onError?: (err: Error) => void,
  ) {
    this.proc = spawn('iteris', ['tool', 'ui', 'normalize', '--stream', '--adapter', adapter], {
      cwd: projectPath,
      stdio: ['pipe', 'pipe', 'inherit'],
    });
    // Without this handler a failed spawn (e.g. `iteris` missing from PATH)
    // raises an uncaught 'error' event and takes down the whole server.
    this.proc.on('error', (err) => {
      const wasDead = this.dead;
      this.dead = true;
      if (!wasDead && !this.closing) onError?.(err);
    });
    // An unexpected exit (child crash, OOM-kill) would otherwise freeze the
    // stream silently: writes get dropped while the socket still looks live.
    this.proc.on('exit', (code, signal) => {
      const wasDead = this.dead;
      this.dead = true;
      if (!wasDead && !this.closing) {
        onError?.(new Error(`normalizer exited unexpectedly (code ${code}, signal ${signal})`));
      }
    });
    this.proc.stdin.on('error', () => {
      /* EPIPE after the child died — write() already guards on `dead`. */
    });
    this.proc.stdout.setEncoding('utf-8');
    this.proc.stdout.on('data', (chunk: string) => {
      if (this.closing) return;
      this.buffer += chunk;
      const lines = this.buffer.split('\n');
      this.buffer = lines.pop() || '';
      for (const line of lines) {
        if (line.trim()) onEntry(line);
      }
    });
  }

  /** Feed one raw structured JSONL line. */
  write(rawLine: string): void {
    if (!this.dead && !this.closing && !this.proc.killed && this.proc.stdin.writable) {
      this.proc.stdin.write(rawLine + '\n');
    }
  }

  /** Stop the child and silence any output still in flight. */
  close(): void {
    this.closing = true;
    this.dead = true;
    this.proc.stdout.removeAllListeners('data');
    try {
      this.proc.stdin.end();
      this.proc.kill('SIGTERM');
    } catch {
      /* ignore */
    }
  }
}
