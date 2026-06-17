/** Dashboard data routes: facts graph, activity feed, frontier map, task pool, evolve. */
import type { FastifyInstance } from 'fastify';
import type { ProjectPaths } from '../types.js';
import { createBridge, type IterisBridge } from '../iteris.js';
import { cached } from '../cache.js';

const PARAM_TTL_MS = 4000;
const PARAM_CACHE_MAX = 200;

/**
 * GET <url>?<param>=<value> → `iteris <buildArgs(value)>`, with a small
 * bounded per-value cache (the keyless cached() helper does not apply).
 */
function registerParamRoute(
  fastify: FastifyInstance,
  bridge: IterisBridge,
  url: string,
  param: string,
  buildArgs: (value: string) => string[],
): void {
  const cache = new Map<string, { at: number; data: unknown }>();
  fastify.get(url, async (req, reply) => {
    const value = (req.query as Record<string, unknown>)?.[param];
    if (typeof value !== 'string' || value.length === 0) {
      return reply.status(400).send({ error: `missing required query parameter: ${param}` });
    }
    const hit = cache.get(value);
    if (hit && Date.now() - hit.at < PARAM_TTL_MS) return hit.data;
    try {
      const data = await bridge.json(buildArgs(value));
      if (cache.size >= PARAM_CACHE_MAX && !cache.has(value)) {
        const oldest = cache.keys().next().value;
        if (oldest !== undefined) cache.delete(oldest);
      }
      cache.set(value, { at: Date.now(), data });
      return data;
    } catch (e: any) {
      return reply.status(500).send({ error: e.message });
    }
  });
}

export function register(fastify: FastifyInstance, paths: ProjectPaths): void {
  const bridge = createBridge(paths.projectPath);
  const project = paths.projectPath;

  // TTLs sit at ~80% of the client polling interval for each endpoint
  // (facts/activity/tasks/supervision poll at 5s, frontier/evolve at 10s),
  // so one CLI spawn serves every connected tab per tick.
  const endpoints: Array<{ url: string; ttl: number; args: string[] }> = [
    { url: '/api/facts', ttl: 4000, args: ['tool', 'ui', 'facts', project, '--json'] },
    { url: '/api/activity', ttl: 4000, args: ['tool', 'ui', 'activity', project, '--json', '--limit', '80'] },
    { url: '/api/frontier', ttl: 8000, args: ['tool', 'frontier', 'show', project, '--json'] },
    { url: '/api/tasks', ttl: 4000, args: ['tool', 'task', 'pool', 'show', project, '--json'] },
    { url: '/api/evolve', ttl: 8000, args: ['tool', 'ui', 'evolve', project, '--json'] },
    { url: '/api/supervision', ttl: 4000, args: ['tool', 'ui', 'supervision', project, '--json', '--limit', '120'] },
    { url: '/api/reports', ttl: 8000, args: ['tool', 'ui', 'reports', project, '--json'] },
    { url: '/api/family', ttl: 8000, args: ['tool', 'ui', 'family', project, '--json'] },
  ];

  for (const { url, ttl, args } of endpoints) {
    const get = cached(ttl, () => bridge.json(args));
    fastify.get(url, async (_req, reply) => {
      try {
        return await get();
      } catch (e: any) {
        return reply.status(500).send({ error: e.message });
      }
    });
  }

  registerParamRoute(fastify, bridge, '/api/fact', 'id', (id) => [
    'tool', 'ui', 'fact', project, '--fact-id', id, '--json',
  ]);
  registerParamRoute(fastify, bridge, '/api/direction', 'id', (id) => [
    'tool', 'ui', 'direction', project, '--direction-id', id, '--json',
  ]);
  registerParamRoute(fastify, bridge, '/api/evolve-node', 'id', (id) => [
    'tool', 'ui', 'node', project, '--node-id', id, '--json',
  ]);
}
