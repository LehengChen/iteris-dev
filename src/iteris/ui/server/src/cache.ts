/**
 * Coalesce + briefly cache a bridge call. Each call spawns a full Python CLI
 * (~hundreds of ms), and every connected tab polls these endpoints every few
 * seconds — without this, N tabs means N interpreter launches per tick.
 */
export function cached<T>(ttlMs: number, fetcher: () => Promise<T>): () => Promise<T> {
  let value: { at: number; data: T } | null = null;
  let inflight: Promise<T> | null = null;
  return async () => {
    if (value && Date.now() - value.at < ttlMs) return value.data;
    if (!inflight) {
      inflight = fetcher()
        .then((data) => {
          value = { at: Date.now(), data };
          return data;
        })
        .finally(() => {
          inflight = null;
        });
    }
    return inflight;
  };
}
