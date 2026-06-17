/**
 * Shared list-section chrome: a titled card holding rows.
 *
 * Every "title + list of rows" block (direction pool, family nodes, boundary
 * map, supervision feed, future sections) composes these three pieces so new
 * sections inherit layout, hover and empty-state styling for free.
 */
import type { ReactNode } from 'react';

export function SectionCard({ title, action, className, children }: {
  title: ReactNode;
  /** Optional right-aligned control (e.g. a fold toggle). */
  action?: ReactNode;
  className?: string;
  children: ReactNode;
}) {
  return (
    <section className={`section-card${className ? ` ${className}` : ''}`}>
      <div className="section-card-title">
        {title}
        {action}
      </div>
      {children}
    </section>
  );
}

export function SectionEmpty({ children }: { children: ReactNode }) {
  return <div className="dim section-empty">{children}</div>;
}

/** One flex row inside a SectionCard; dim folds superseded/stale entries. */
export function ListRow({ children, dim, style }: {
  children: ReactNode;
  dim?: boolean;
  style?: React.CSSProperties;
}) {
  return (
    <div className={`list-row${dim ? ' list-row--dim' : ''}`} style={style}>
      {children}
    </div>
  );
}
