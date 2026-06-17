/** Subtle dot + text status marker (replaces the louder pill badges). */
export function Tag({ kind, children }: { kind: string; children: React.ReactNode }) {
  return (
    <span className="tag">
      <i className={`dot dot--${kind}`} />
      {children}
    </span>
  );
}
