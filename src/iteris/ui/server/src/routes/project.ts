/** Project / status / streams routes. */
import type { FastifyInstance } from 'fastify';
import type { ProjectPaths } from '../types.js';
import { createBridge } from '../iteris.js';
import { cached } from '../cache.js';

export function register(fastify: FastifyInstance, paths: ProjectPaths): void {
  const bridge = createBridge(paths.projectPath);

  // Used by the launcher to confirm the right server answered on a port.
  fastify.get('/api/project', async () => ({
    projectPath: paths.projectPath,
    iterisPath: paths.iterisPath,
  }));

  // TTLs ~80% of the client polling interval: status polls at 5s, streams at 3s.
  const getStatus = cached(4000, () => bridge.json(['status', paths.projectPath, '--json']));
  const getStreams = cached(2500, () =>
    bridge.json(['tool', 'ui', 'streams', paths.projectPath, '--json']),
  );

  // Pass-through to `iteris status --json`.
  fastify.get('/api/status', async (_req, reply) => {
    try {
      return await getStatus();
    } catch (e: any) {
      return reply.status(500).send({ error: e.message });
    }
  });

  // Discover all observable streams (main pane + agent/verify runs).
  fastify.get('/api/streams', async (_req, reply) => {
    try {
      return await getStreams();
    } catch (e: any) {
      return reply.status(500).send({ error: e.message });
    }
  });
}
