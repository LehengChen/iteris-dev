import { useEffect, useMemo, useState } from 'react';
import { useFactDetail, useReportWorkspaceDetail, useReportWorkspaces } from '../hooks/useApi';
import { timeAgo } from '../lib/format';
import { Tag } from '../components/Tag';
import { ListRow, SectionCard, SectionEmpty } from '../components/SectionCard';
import type { ReportEvidence, ReportFact, ReportReferences, ReportVersion, ReportWorkspaceItem } from '../types';

function reportFileUrl(path?: string): string {
  return path ? `/api/report-file?path=${encodeURIComponent(path)}` : '';
}

function factLabel(id: string): string {
  const parts = id.split(':');
  const last = parts[parts.length - 1];
  return /^\d{8}T/.test(last) ? parts[parts.length - 2] ?? id : last || id;
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

interface OrderedFact {
  fact: ReportFact;
  label: string;
  citationKey?: string;
}

function orderedReferenceFacts(evidence?: ReportEvidence | null, references?: ReportReferences | null): OrderedFact[] {
  const facts = evidence?.facts ?? [];
  const factById = new Map<string, ReportFact>();
  for (const fact of facts) {
    if (fact.fact_id) factById.set(fact.fact_id, fact);
  }
  const out: OrderedFact[] = [];
  const seen = new Set<string>();
  for (const item of references?.fact_labels ?? []) {
    if (!item.fact_id || !factById.has(item.fact_id)) continue;
    seen.add(item.fact_id);
    out.push({
      fact: factById.get(item.fact_id)!,
      label: item.label || factLabel(item.fact_id),
      citationKey: item.citation_key,
    });
  }
  facts.forEach((fact, index) => {
    if (!fact.fact_id || seen.has(fact.fact_id)) return;
    out.push({ fact, label: `F${index + 1}` });
  });
  return out;
}

function ReferenceFactRow({ item, open, onToggle }: {
  item: OrderedFact;
  open: boolean;
  onToggle: () => void;
}) {
  const factId = item.fact.fact_id ?? '';
  const detail = useFactDetail(open && factId ? factId : null);
  const body = item.fact.body ?? detail.data?.fact?.body;
  return (
    <div className={`report-ref-fact${open ? ' report-ref-fact--open' : ''}`}>
      <button className="report-ref-fact-head" onClick={onToggle}>
        <span className="report-ref-label">{item.label}</span>
        <span className="report-ref-main">
          <span>{item.fact.claim_summary ?? factId}</span>
          {item.citationKey && <code>{item.citationKey}</code>}
        </span>
      </button>
      {open && (
        <div className="report-ref-body">
          <dl>
            <span>
              <dt>status</dt>
              <dd>{item.fact.status ?? 'unknown'}</dd>
            </span>
            <span>
              <dt>verification</dt>
              <dd>{item.fact.verification ?? 'none'}</dd>
            </span>
            <span>
              <dt>file</dt>
              <dd>{item.fact.path ?? 'unknown'}</dd>
            </span>
          </dl>
          <pre>{body ?? (detail.isLoading ? 'Loading fact statement...' : 'Fact statement unavailable.')}</pre>
        </div>
      )}
    </div>
  );
}

function EvidenceSidebar({
  evidence,
  references,
}: {
  evidence?: ReportEvidence | null;
  references?: ReportReferences | null;
}) {
  const facts = orderedReferenceFacts(evidence, references);
  const [openFactId, setOpenFactId] = useState<string | null>(facts[0]?.fact.fact_id ?? null);
  useEffect(() => {
    setOpenFactId(facts[0]?.fact.fact_id ?? null);
  }, [evidence?.report_id, evidence?.version, references?.include_internal]);
  return (
    <aside className="report-side">
      <SectionCard title="References">
        {evidence?.answer?.verified_positive_result && (
          <div className="report-side-block">
            <div className="report-side-title">Verified result</div>
            <p className="report-side-text">{evidence.answer.verified_positive_result}</p>
          </div>
        )}
        <div className="report-side-block">
          <div className="report-side-title">Facts in reference order · {facts.length}</div>
          <div className="report-ref-list">
            {facts.length === 0 && <span className="dim">No checked facts recorded.</span>}
            {facts.map((fact) => (
              <ReferenceFactRow
                key={fact.fact.fact_id ?? fact.label}
                item={fact}
                open={openFactId === fact.fact.fact_id}
                onToggle={() => setOpenFactId(openFactId === fact.fact.fact_id ? null : fact.fact.fact_id ?? null)}
              />
            ))}
          </div>
        </div>
      </SectionCard>
    </aside>
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
        references={workspace?.references}
      />
    </div>
  );
}
