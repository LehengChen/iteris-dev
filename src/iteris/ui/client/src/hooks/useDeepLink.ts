/**
 * One-shot deep-link selection from a URL search param.
 *
 * Used by /facts?focus=<node-id> and /logs?stream=<stream-id>: when the
 * param appears (or changes) and resolves against the loaded collection,
 * `onMatch` fires exactly once for that value — later user interaction is
 * never overridden by the stale param.
 */
import { useEffect, useRef } from 'react';
import { useSearchParams } from 'react-router-dom';

export function useDeepLink(param: string, resolve: (value: string) => boolean, onMatch: (value: string) => void) {
  const [searchParams] = useSearchParams();
  const applied = useRef<string | null>(null);
  const value = searchParams.get(param);
  useEffect(() => {
    if (value && applied.current !== value && resolve(value)) {
      applied.current = value;
      onMatch(value);
    }
    // resolve/onMatch are intentionally unstable (inline closures); the
    // applied ref guarantees one-shot semantics regardless.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value, resolve]);
  return value;
}
