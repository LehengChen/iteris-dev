/** Dashboard data routes: facts graph, activity feed, frontier map, task pool, evolve. */
import type { FastifyInstance } from 'fastify';
import fs from 'fs';
import path from 'path';
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

const REPORT_FILE_NAMES = new Set([
  'main.pdf',
  'main.tex',
  'evidence.json',
  'references.json',
  'template.lock.json',
  'template.assets.json',
  'author_draft.md',
  'feedback.md',
  'REVISION_LOG.md',
]);

function normalizeProjectRel(raw: string): string | null {
  const normalized = path.posix.normalize(raw.replace(/\\/g, '/'));
  if (normalized === '.' || normalized.startsWith('../') || path.posix.isAbsolute(normalized)) {
    return null;
  }
  return normalized;
}

function resolveReportFile(projectPath: string, raw: string): string | null {
  const rel = normalizeProjectRel(raw);
  if (!rel || !rel.startsWith('reports/')) return null;
  if (!REPORT_FILE_NAMES.has(path.posix.basename(rel))) return null;
  const full = path.resolve(projectPath, ...rel.split('/'));
  if (full !== projectPath && !full.startsWith(projectPath + path.sep)) return null;
  let real: string;
  let realRoot: string;
  try {
    real = fs.realpathSync(full);
    realRoot = fs.realpathSync(projectPath);
  } catch {
    return null;
  }
  if (real !== realRoot && !real.startsWith(realRoot + path.sep)) return null;
  return real;
}

function contentTypeFor(file: string): string {
  if (file.endsWith('.pdf')) return 'application/pdf';
  if (file.endsWith('.json')) return 'application/json; charset=utf-8';
  if (file.endsWith('.tex') || file.endsWith('.md')) return 'text/plain; charset=utf-8';
  return 'application/octet-stream';
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
    {
      url: '/api/report-workspaces',
      ttl: 8000,
      args: ['tool', 'ui', 'report-workspaces', project, '--json'],
    },
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

  const reportCache = new Map<string, { at: number; data: unknown }>();
  fastify.get('/api/report-workspace', async (req, reply) => {
    const query = req.query as Record<string, unknown>;
    const id = query?.id;
    const version = query?.version;
    if (typeof id !== 'string' || id.length === 0) {
      return reply.status(400).send({ error: 'missing required query parameter: id' });
    }
    const cacheKey = `${id}#${typeof version === 'string' ? version : ''}`;
    const hit = reportCache.get(cacheKey);
    if (hit && Date.now() - hit.at < PARAM_TTL_MS) return hit.data;
    const args = ['tool', 'ui', 'report-workspace', project, '--report-id', id, '--json'];
    if (typeof version === 'string' && version.length > 0) args.push('--version', version);
    try {
      const data = await bridge.json(args);
      if (reportCache.size >= PARAM_CACHE_MAX && !reportCache.has(cacheKey)) {
        const oldest = reportCache.keys().next().value;
        if (oldest !== undefined) reportCache.delete(oldest);
      }
      reportCache.set(cacheKey, { at: Date.now(), data });
      return data;
    } catch (e: any) {
      return reply.status(500).send({ error: e.message });
    }
  });

  fastify.get('/api/report-file', async (req, reply) => {
    const rel = (req.query as Record<string, unknown>)?.path;
    if (typeof rel !== 'string' || rel.length === 0) {
      return reply.status(400).send({ error: 'missing required query parameter: path' });
    }
    const full = resolveReportFile(project, rel);
    if (!full || !fs.existsSync(full)) return reply.status(404).send({ error: 'Not found' });
    reply.header('Content-Type', contentTypeFor(full));
    return reply.send(fs.createReadStream(full));
  });
}
