/**
 * Bridge to the Iteris Python CLI.
 *
 * The Node server is intentionally thin: all discovery / live-detection /
 * snapshot / Codex-event normalization lives in Python and is reached by
 * shelling out to `iteris tool ui ... --json`. This keeps the `--json` CLI
 * as the single contract and avoids duplicating parsing logic in TS.
 */
import { execFile } from 'child_process';

export interface IterisBridge {
  /** Run an `iteris` subcommand and parse its stdout as JSON. */
  json<T = unknown>(args: string[]): Promise<T>;
}

export function createBridge(projectPath: string): IterisBridge {
  return {
    json<T = unknown>(args: string[]): Promise<T> {
      return new Promise<T>((resolve, reject) => {
        execFile(
          'iteris',
          args,
          { cwd: projectPath, maxBuffer: 64 * 1024 * 1024, timeout: 30000 },
          (err, stdout, stderr) => {
            if (err) {
              reject(new Error(`iteris ${args.join(' ')} failed: ${stderr || err.message}`));
              return;
            }
            try {
              resolve(JSON.parse(stdout) as T);
            } catch (e) {
              reject(new Error(`Could not parse JSON from: iteris ${args.join(' ')}`));
            }
          },
        );
      });
    },
  };
}
