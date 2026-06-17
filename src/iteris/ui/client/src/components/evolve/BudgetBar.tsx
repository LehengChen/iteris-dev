/** Wall-clock budget + concurrency slots, as one compact strip. */
import type { EvolveState } from '../../types';

export function BudgetBar({ budget }: { budget: NonNullable<EvolveState['budget']> }) {
  const wall = budget.wall_hours ?? 0;
  const spent = budget.spent_hours ?? 0;
  const pct = wall > 0 ? Math.min(100, (spent / wall) * 100) : 0;
  return (
    <div className="budget">
      <div className="budget-track">
        <div
          className={`budget-fill${budget.exhausted ? ' budget-fill--exhausted' : ''}`}
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className="budget-text">
        {spent}h / {wall}h · slots {budget.running ?? 0}/{budget.max_concurrent ?? 0} · nodes{' '}
        {budget.nodes ?? 0}/{budget.max_nodes ?? '∞'}
      </span>
    </div>
  );
}
