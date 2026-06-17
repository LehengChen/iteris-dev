/**
 * Shared building blocks for the right-hand detail drawers.
 *
 * Every drawer (fact, answer, direction, report, journal entry) composes the
 * same shell: status tag + close button, title/subtitle, a meta grid, link
 * sections that focus other nodes, and a body (preformatted or markdown).
 * The shell is viewport-fixed and horizontally resizable via its left-edge
 * handle; the chosen width persists across sessions.
 *
 * Open/close state lives in each feature component, but the shell coordinates
 * globally: mounting a drawer closes every other mounted drawer, so at most
 * one is visible at a time. Escape closes the current drawer.
 */
import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import { Markdown } from '../Markdown';

/**
 * Module-level registry of mounted drawer instances. Each entry is a stable
 * per-instance token whose `close` always calls the latest onClose (held in a
 * ref, so closures never go stale). When a drawer mounts it broadcasts
 * "opened" and every *other* registered drawer closes itself; the token
 * identity check makes the broadcast a no-op on self, which also keeps React
 * StrictMode's mount→unmount→mount double-invocation from closing the drawer
 * that just opened (the token lives in a ref, so both effect runs share it).
 */
interface DrawerToken {
  close: () => void;
}
const mountedDrawers = new Set<DrawerToken>();

function announceOpen(self: DrawerToken) {
  for (const other of mountedDrawers) {
    if (other !== self) other.close();
  }
}

const WIDTH_KEY = 'iteris.drawer.width';
const MIN_W = 320;
const maxW = () => Math.round(window.innerWidth * 0.7);

function useDrawerWidth(): [number, (w: number) => void] {
  const [width, setWidth] = useState(() => {
    const saved = Number(localStorage.getItem(WIDTH_KEY));
    return Number.isFinite(saved) && saved >= MIN_W ? Math.min(saved, maxW()) : 440;
  });
  useEffect(() => {
    localStorage.setItem(WIDTH_KEY, String(width));
  }, [width]);
  return [width, setWidth];
}

export function GraphDrawer({ head, onClose, children }: {
  head: ReactNode;
  onClose: () => void;
  children: ReactNode;
}) {
  const [width, setWidth] = useDrawerWidth();

  // Latest onClose without re-running the mount effect when callers pass a
  // fresh closure on every render.
  const onCloseRef = useRef(onClose);
  useEffect(() => {
    onCloseRef.current = onClose;
  });

  // One token per component instance, stable across StrictMode's double
  // effect invocation.
  const tokenRef = useRef<DrawerToken | null>(null);
  if (tokenRef.current === null) {
    tokenRef.current = { close: () => onCloseRef.current() };
  }

  useEffect(() => {
    const token = tokenRef.current!;
    mountedDrawers.add(token);
    announceOpen(token);
    return () => {
      mountedDrawers.delete(token);
    };
  }, []);

  // Escape closes the drawer; at most one is mounted thanks to announceOpen.
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !e.defaultPrevented) onCloseRef.current();
    };
    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, []);

  const onPointerDown = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      e.preventDefault();
      const target = e.currentTarget;
      target.setPointerCapture(e.pointerId);
      const move = (ev: PointerEvent) => {
        setWidth(Math.min(Math.max(MIN_W, window.innerWidth - ev.clientX - 12), maxW()));
      };
      const up = () => {
        target.removeEventListener('pointermove', move);
        target.removeEventListener('pointerup', up);
      };
      target.addEventListener('pointermove', move);
      target.addEventListener('pointerup', up);
    },
    [setWidth],
  );
  return (
    <aside className="fact-drawer" style={{ width }}>
      <div className="drawer-resize" onPointerDown={onPointerDown} title="Drag to resize" />
      <div className="fact-drawer-head">
        {head}
        <button className="fact-drawer-close" onClick={onClose} title="Close">×</button>
      </div>
      {children}
    </aside>
  );
}

export function DrawerTitle({ title, subtitle }: { title: ReactNode; subtitle?: ReactNode }) {
  return (
    <>
      <h3 className="fact-drawer-title">{title}</h3>
      {subtitle && <code className="fact-drawer-id">{subtitle}</code>}
    </>
  );
}

export function DrawerMeta({ rows }: { rows: Array<[label: string, value: ReactNode, breakAll?: boolean]> }) {
  return (
    <dl className="fact-drawer-meta">
      {rows.map(([label, value, breakAll]) => (
        <span key={label} style={{ display: 'contents' }}>
          <dt>{label}</dt>
          <dd className={breakAll ? 'break' : undefined}>{value ?? '—'}</dd>
        </span>
      ))}
    </dl>
  );
}

export interface DrawerLink {
  id: string;
  label: string;
  /** Present → clickable focus link; absent → inert external reference. */
  onClick?: () => void;
  title?: string;
}

export function DrawerLinks({ title, links }: { title: string; links: DrawerLink[] }) {
  if (links.length === 0) return null;
  return (
    <div className="fact-drawer-links">
      <div className="fact-drawer-sub">{title}</div>
      {links.map((link) =>
        link.onClick ? (
          <button key={link.id} className="fact-link" onClick={link.onClick} title={link.title}>
            {link.label}
          </button>
        ) : (
          <span key={link.id} className="fact-link fact-link--external" title={link.title}>
            {link.label}
          </span>
        ),
      )}
    </div>
  );
}

export function DrawerBody({ text, markdown = false }: { text: string; markdown?: boolean }) {
  if (markdown) {
    return (
      <div className="fact-drawer-body fact-drawer-body--md">
        <Markdown text={text} />
      </div>
    );
  }
  return <pre className="fact-drawer-body">{text}</pre>;
}
