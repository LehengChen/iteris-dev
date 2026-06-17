/**
 * Supervisor stage reports — the human-readable milestone narrative.
 * Each headline is already a one-line situation summary; opening a row shows
 * the full markdown in the shared resizable drawer. Selection is controlled
 * by the parent so journal rows can open a report too.
 */
import { useReports } from '../../hooks/useApi';
import { timeAgo } from '../../lib/format';
import { Tag } from '../Tag';
import { ListRow, SectionCard, SectionEmpty } from '../SectionCard';
import { DrawerBody, DrawerMeta, DrawerTitle, GraphDrawer } from '../graph/GraphDrawer';

export function ReportsList({ openId, onOpen }: {
  openId: string | null;
  onOpen: (reportId: string | null) => void;
}) {
  const items = useReports().data?.items ?? [];
  const open = items.find((r) => r.report_id === openId) ?? null;
  return (
    <SectionCard title={`Stage reports · ${items.length}`}>
      {items.length === 0 && <SectionEmpty>No stage reports yet — they appear at milestones.</SectionEmpty>}
      {items.map((r) => (
        <button key={r.report_id} className="feed-row-btn" onClick={() => onOpen(r.report_id)}>
          <ListRow>
            <Tag kind="info">report</Tag>
            <span className="row-title row-title--wrap">{r.headline}</span>
            <span className="row-meta dim">{timeAgo(r.created_at)}</span>
          </ListRow>
        </button>
      ))}
      {open && (
        <GraphDrawer head={<Tag kind="info">stage report</Tag>} onClose={() => onOpen(null)}>
          <DrawerTitle title={open.headline} subtitle={open.path} />
          <DrawerMeta rows={[['written', timeAgo(open.created_at)], ['report id', open.report_id, true]]} />
          <DrawerBody text={open.content} markdown />
        </GraphDrawer>
      )}
    </SectionCard>
  );
}
