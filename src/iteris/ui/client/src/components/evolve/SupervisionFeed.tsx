/**
 * The supervisor's journal as a live feed, newest first, translated into
 * plain English (lib/evolve.journalDisplay). Rows with a judge agent run
 * deep-link into Logs; stage-report rows open the report drawer; clicking
 * any row opens a drawer with the raw payload for auditing.
 * Idle ticks and completed intents are folded behind a toggle.
 */
import { useState } from 'react';
import { Link } from 'react-router-dom';
import { isJournalNoise, journalDisplay, journalReportId, type JournalContext } from '../../lib/evolve';
import { timeAgo } from '../../lib/format';
import { Tag } from '../Tag';
import { ListRow, SectionCard, SectionEmpty } from '../SectionCard';
import { DrawerBody, DrawerMeta, DrawerTitle, GraphDrawer } from '../graph/GraphDrawer';
import type { JournalEntry } from '../../types';

function JournalDrawer({ entry, ctx, onClose }: {
  entry: JournalEntry;
  ctx: JournalContext;
  onClose: () => void;
}) {
  const d = journalDisplay(entry, ctx);
  const p = entry.payload ?? {};
  return (
    <GraphDrawer head={<Tag kind={d.kind}>{d.label}</Tag>} onClose={onClose}>
      <DrawerTitle title={d.summary} subtitle={entry.entry_id} />
      <DrawerMeta
        rows={[
          ['type', entry.entry_type],
          ['trigger', p.trigger ?? '—'],
          ['contract', p.contract ?? '—'],
          ['action', p.action ?? '—'],
          ['when', timeAgo(entry.ts)],
          ['agent run', entry.agent_run ?? '—', true],
        ]}
      />
      {entry.agent_run && (
        <div className="fact-drawer-links">
          <div className="fact-drawer-sub">Evidence</div>
          <Link className="fact-link" to={`/logs?stream=${encodeURIComponent(entry.agent_run)}`}>
            open judge agent log ↗
          </Link>
        </div>
      )}
      <DrawerBody text={JSON.stringify(entry.payload, null, 2)} />
    </GraphDrawer>
  );
}

export function SupervisionFeed({ items, ctx, onOpenReport }: {
  items: JournalEntry[];
  ctx: JournalContext;
  onOpenReport: (reportId: string) => void;
}) {
  const [showAll, setShowAll] = useState(false);
  const [openId, setOpenId] = useState<string | null>(null);
  const visible = showAll ? items : items.filter((e) => !isJournalNoise(e));
  const hidden = items.length - visible.length;
  const open = items.find((e) => e.entry_id === openId) ?? null;
  return (
    <SectionCard
      title="Supervision journal"
      className="section-card--feed"
      action={
        (hidden > 0 || showAll) && (
          <button className="feed-toggle" onClick={() => setShowAll(!showAll)}>
            {showAll ? 'fold noise' : `show ${hidden} folded`}
          </button>
        )
      }
    >
      <div className="feed">
        {visible.length === 0 && <SectionEmpty>No journal entries yet.</SectionEmpty>}
        {visible.map((e) => {
          const d = journalDisplay(e, ctx);
          const reportId = journalReportId(e);
          return (
            <button key={e.entry_id} className="feed-row-btn" onClick={() => setOpenId(e.entry_id)}>
              <ListRow dim={e.superseded}>
                <Tag kind={d.kind}>{d.label}</Tag>
                <span className="row-title row-title--secondary" title={d.summary}>
                  {d.summary}
                </span>
                {reportId && (
                  <span
                    className="feed-link"
                    role="link"
                    onClick={(ev) => {
                      ev.stopPropagation();
                      onOpenReport(reportId);
                    }}
                  >
                    report ↗
                  </span>
                )}
                {e.agent_run && (
                  <Link
                    className="feed-link"
                    to={`/logs?stream=${encodeURIComponent(e.agent_run)}`}
                    title={e.agent_run}
                    onClick={(ev) => ev.stopPropagation()}
                  >
                    log ↗
                  </Link>
                )}
                <span className="row-meta dim">{timeAgo(e.ts)}</span>
              </ListRow>
            </button>
          );
        })}
      </div>
      {open && <JournalDrawer entry={open} ctx={ctx} onClose={() => setOpenId(null)} />}
    </SectionCard>
  );
}
