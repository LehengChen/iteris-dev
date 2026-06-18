import { Link } from 'react-router-dom';
import { useMemo, useState } from 'react';
import { useReportWorkspaceDetail, useReportWorkspaces } from '../hooks/useApi';
import { timeAgo } from '../lib/format';
import { Tag } from '../components/Tag';
import { ListRow, SectionCard, SectionEmpty } from '../components/SectionCard';
import type { ReportEvidence, ReportFileRecord, ReportVersion, ReportWorkspaceItem } from '../types';

function reportFileUrl(path?: string): string {
  return path ? `/api/report-file?path=${encodeURIComponent(path)}` : '';
}

function factLabel(id: string): string {
  const parts = id.split(':');
  const last = parts[parts.length - 1];
  return /^\d{8}T/.test(last) ? parts[parts.length - 2] ?? id : last || id;
}

function FileLink({ file, label }: { file?: ReportFileRecord | null; label: string }) {
  if (!file?.exists) return null;
  return (
    <a className="report-file-link" href={reportFileUrl(file.path)} target="_blank" rel="noreferrer">
      {label}
    </a>
  );
}

function VersionFiles({ current }: { current?: ReportVersion | null }) {
  if (!current) return null;
  const files = [
    ['PDF', current.pdf, current.pdf_exists],
    ['TeX', current.main_tex, current.main_tex_exists],
    ['evidence.json', current.evidence, current.evidence_exists],
    ['references.json', current.references, current.references_exists],
  ] as const;
  return (
    <div className="report-file-strip">
      {files.map(([label, path, exists]) =>
        exists ? (
          <a key={label} className="report-file-link" href={reportFileUrl(path)} target="_blank" rel="noreferrer">
            {label}
          </a>
        ) : (
          <span key={label} className="report-file-link report-file-link--missing">{label}</span>
        ),
      )}
    </div>
  );
}

function ReportRow({ item, selected, onSelect }: {
  item: ReportWorkspaceItem;
  selected: boolean;
  onSelect: () => void;
}) {
  return (
    <button className={`report-row${selected ? ' report-row--selected' : ''}`} onClick={onSelect}>
      <ListRow>
        <Tag kind={item.pdf_exists ? 'ok' : 'warn'}>{item.current_version ?? 'draft'}</Tag>
        <span className="row-title row-title--wrap">{item.title ?? item.report_id}</span>
        <span className="row-meta dim">{timeAgo(item.updated_at)}</span>
      </ListRow>
      <div className="report-row-sub">
        <code>{item.report_id}</code>
        <span>{item.evidence_mode}</span>
        <span>{item.version_count ?? 0} version{item.version_count === 1 ? '' : 's'}</span>
      </div>
    </button>
  );
}

function EvidenceSidebar({
  evidence,
  current,
  notice,
  authorDraft,
}: {
  evidence?: ReportEvidence | null;
  current?: ReportVersion | null;
  notice?: string;
  authorDraft?: ReportFileRecord;
}) {
  const facts = evidence?.facts ?? [];
  const sourcePaths = evidence?.source_paths ?? [];
  const artifacts = evidence?.checked_artifacts ?? [];
  return (
    <aside className="report-side">
      <SectionCard title="Evidence & references">
        {notice && (
          <div className="report-notice">
            <Tag kind="warn">portable</Tag>
            <span>{notice}</span>
          </div>
        )}
        <div className="report-side-block">
          <div className="report-side-title">Version files</div>
          <VersionFiles current={current} />
          <FileLink file={authorDraft} label="author_draft.md" />
        </div>
        {evidence?.answer?.verified_positive_result && (
          <div className="report-side-block">
            <div className="report-side-title">Verified result</div>
            <p className="report-side-text">{evidence.answer.verified_positive_result}</p>
          </div>
        )}
        <div className="report-side-block">
          <div className="report-side-title">Checked facts · {facts.length}</div>
          <div className="report-fact-list">
            {facts.length === 0 && <span className="dim">No checked facts recorded.</span>}
            {facts.map((fact) =>
              fact.fact_id ? (
                <Link
                  key={fact.fact_id}
                  className="report-fact-chip"
                  to={`/facts?focus=${encodeURIComponent(fact.fact_id)}`}
                >
                  <span>{factLabel(fact.fact_id)}</span>
                  <small>{fact.claim_summary}</small>
                </Link>
              ) : null,
            )}
          </div>
        </div>
        <div className="report-side-block">
          <div className="report-side-title">Source paths · {sourcePaths.length}</div>
          <PathList items={sourcePaths.map((item) => ({ label: item.role, path: item.path, exists: item.exists }))} />
        </div>
        <div className="report-side-block">
          <div className="report-side-title">Checked artifacts · {artifacts.length}</div>
          <PathList items={artifacts.map((item) => ({ label: item.kind, path: item.path, exists: true }))} />
        </div>
      </SectionCard>
    </aside>
  );
}

function PathList({ items }: { items: Array<{ label?: string; path?: string; exists?: boolean }> }) {
  if (items.length === 0) return <div className="section-empty dim">None recorded.</div>;
  return (
    <div className="report-path-list">
      {items.map((item, idx) => (
        <div key={`${item.path ?? 'path'}-${idx}`} className="report-path-row">
          <Tag kind={item.exists ? 'info' : 'dim'}>{item.label ?? 'path'}</Tag>
          <code>{item.path}</code>
        </div>
      ))}
    </div>
  );
}

export function Reports() {
  const { data, isLoading, error } = useReportWorkspaces();
  const items = data?.items ?? [];
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [selectedVersion, setSelectedVersion] = useState<string | null>(null);
  const selected = useMemo(
    () => items.find((item) => item.report_id === selectedId) ?? items[0] ?? null,
    [items, selectedId],
  );
  const detail = useReportWorkspaceDetail(selected?.report_id ?? null, selectedVersion);
  const workspace = detail.data;
  const versions = workspace?.versions ?? [];
  const current = workspace?.current ?? null;
  const pdfUrl = current?.pdf_exists ? reportFileUrl(current.pdf) : '';

  if (error) return <div className="view-message">Failed to load reports: {String(error)}</div>;

  return (
    <div className="reports-view">
      <section className="reports-list">
        <SectionCard title={`Reports · ${data?.report_count ?? 0}`}>
          {isLoading && <SectionEmpty>Loading reports…</SectionEmpty>}
          {!isLoading && items.length === 0 && <SectionEmpty>No reports yet.</SectionEmpty>}
          {items.map((item) => (
            <ReportRow
              key={item.report_id}
              item={item}
              selected={item.report_id === selected?.report_id}
              onSelect={() => {
                setSelectedId(item.report_id);
                setSelectedVersion(null);
              }}
            />
          ))}
        </SectionCard>
      </section>

      <section className="report-pdf-pane">
        <div className="report-pdf-head">
          <div>
            <h2>{workspace?.report?.title ?? selected?.title ?? 'Reports'}</h2>
            <code>{current?.main_tex ?? selected?.main_tex ?? 'reports'}</code>
          </div>
          {versions.length > 0 && (
            <select
              className="report-version-select"
              value={workspace?.selected_version ?? ''}
              onChange={(event) => setSelectedVersion(event.target.value)}
            >
              {versions.map((version) => (
                <option key={version.version} value={version.version}>
                  {version.version}
                </option>
              ))}
            </select>
          )}
        </div>
        {pdfUrl ? (
          <iframe className="report-pdf-frame" title="Report PDF" src={pdfUrl} />
        ) : (
          <div className="report-pdf-empty">
            <Tag kind="warn">pdf missing</Tag>
            <span>{selected ? 'PDF has not been built for this version.' : 'No report selected.'}</span>
          </div>
        )}
      </section>

      <EvidenceSidebar
        evidence={workspace?.evidence}
        current={current}
        notice={workspace?.notice}
        authorDraft={workspace?.author_draft}
      />
    </div>
  );
}
