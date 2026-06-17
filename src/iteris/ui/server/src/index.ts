/**
 * Iteris Dashboard UI Server — entry point.
 *
 * A thin Fastify server that:
 *   - serves the built React SPA (client/dist)
 *   - exposes REST endpoints that pass through to `iteris ... --json`
 *   - tails log files over WebSocket for live streaming
 *
 * Modeled on the Archon dashboard server.
 */
import Fastify from 'fastify';
import staticFiles from '@fastify/static';
import websocket from '@fastify/websocket';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

import { register as registerProject } from './routes/project.js';
import { register as registerLogs } from './routes/logs.js';
import { register as registerData } from './routes/data.js';
import type { ProjectPaths } from './types.js';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

function parseArgs(): { projectPath: string; port: number } {
  const args = process.argv.slice(2);
  let projectPath = process.cwd();
  let port = 8099;
  for (let i = 0; i < args.length; i++) {
    if (args[i] === '--project' && i + 1 < args.length) projectPath = args[++i];
    else if (args[i] === '--port' && i + 1 < args.length) port = parseInt(args[++i], 10);
  }
  return { projectPath: path.resolve(projectPath), port };
}

/** Loopback host names allowed to reach the no-auth dashboard. */
const LOOPBACK = new Set(['localhost', '127.0.0.1', '::1', '[::1]']);

/** Strip the optional :port from a Host/Origin host, keeping IPv6 brackets. */
function hostnameOf(hostHeader: string): string {
  const m = hostHeader.match(/^(\[[^\]]+\]|[^:]+)(?::\d+)?$/);
  return m ? m[1] : hostHeader;
}

export async function createServer(options: { projectPath: string; port: number }) {
  const { projectPath, port } = options;

  const paths: ProjectPaths = {
    projectPath,
    iterisPath: path.join(projectPath, '.iteris'),
    logsPath: path.join(projectPath, '.iteris', 'logs'),
  };

  // forceCloseConnections: destroy long-lived log-stream websockets on
  // close() so Ctrl+C shutdown is instant instead of waiting for tabs.
  const fastify = Fastify({ logger: false, forceCloseConnections: true });
  await fastify.register(websocket);

  // The dashboard has no auth and serves project data (facts, answers, logs),
  // so only same-machine browser tabs may reach it. Reject a non-loopback Host
  // (DNS-rebinding) and any cross-origin request (a site the user has open must
  // not read /api/* or open the log-stream WebSocket). onRequest runs before the
  // WS upgrade in @fastify/websocket v11, so this one hook covers REST and WS.
  fastify.addHook('onRequest', async (req, reply) => {
    if (!LOOPBACK.has(hostnameOf(req.headers.host ?? ''))) {
      return reply.code(403).send({ error: 'forbidden host' });
    }
    const origin = req.headers.origin;
    if (origin) {
      let ok = false;
      try {
        ok = LOOPBACK.has(hostnameOf(new URL(origin).host));
      } catch {
        ok = false;
      }
      if (!ok) return reply.code(403).send({ error: 'cross-origin forbidden' });
    }
  });

  const clientBuildPath = path.join(__dirname, '../../client/dist');
  if (fs.existsSync(clientBuildPath)) {
    await fastify.register(staticFiles, { root: clientBuildPath, prefix: '/' });
    fastify.setNotFoundHandler((req, reply) => {
      if (req.url.startsWith('/api/')) return reply.status(404).send({ error: 'Not found' });
      return reply.sendFile('index.html');
    });
  }

  registerProject(fastify, paths);
  registerLogs(fastify, paths);
  registerData(fastify, paths);

  // Loopback only: the dashboard has no auth and serves project logs, so it
  // must never be reachable from the network. IPv6 loopback is the fallback
  // for the rare IPv4-less host.
  try {
    await fastify.listen({ port, host: '127.0.0.1' });
  } catch (e: any) {
    if (e?.code === 'EAFNOSUPPORT' || e?.code === 'EADDRNOTAVAIL') {
      await fastify.listen({ port, host: '::1' });
    } else {
      throw e;
    }
  }
  return fastify;
}

if (import.meta.url === `file://${process.argv[1]}`) {
  const { projectPath, port } = parseArgs();
  console.log(`Iteris UI → http://127.0.0.1:${port}  (project: ${projectPath})`);
  createServer({ projectPath, port })
    .then((fastify) => {
      let shuttingDown = false;
      const shutdown = async (sig: string) => {
        if (shuttingDown) return;
        shuttingDown = true;
        console.log(`\n[iteris-ui] Received ${sig}, closing server…`);
        const watchdog = setTimeout(() => {
          console.error('[iteris-ui] Shutdown watchdog fired — forcing exit.');
          process.exit(0);
        }, 1500);
        watchdog.unref();
        try {
          await fastify.close();
        } catch (err) {
          console.error('[iteris-ui] Error during shutdown:', err);
        }
        process.exit(0);
      };
      process.on('SIGTERM', () => void shutdown('SIGTERM'));
      process.on('SIGINT', () => void shutdown('SIGINT'));
    })
    .catch((err) => {
      if (err?.code === 'EADDRINUSE') {
        // Distinct exit code so the launcher advances to the next port
        // instead of treating a lost port race as a launch failure.
        console.error(`[iteris-ui] Port ${port} is already in use.`);
        process.exit(98);
      }
      console.error(err);
      process.exit(1);
    });
}
