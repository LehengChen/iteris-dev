import { useEffect, useMemo, useState } from 'react';
import { useFactDetail, useReportWorkspaceDetail, useReportWorkspaces } from '../hooks/useApi';
import { timeAgo } from '../lib/format';
import { Tag } from '../components/Tag';
import { ListRow, SectionCard, SectionEmpty } from '../components/SectionCard';
import { Markdown } from '../components/Markdown';
import type { ReportEvidence, ReportFact, ReportReferenceEntry, ReportReferences, ReportWorkspaceItem } from '../types';

function reportFileUrl(path?: string): string {
  return path ? `/api/report-file?path=${encodeURIComponent(path)}` : '';
}

function reportExportUrl(reportId?: string, version?: string, kind?: 'pdf' | 'source-zip'): string {
  if (!reportId || !version || !kind) return '';
  const params = new URLSearchParams({ id: reportId, version, kind });
  return `/api/report-export?${params.toString()}`;
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
        <Tag kind={item.pdf_exists ? 'ok' : 'warn'}>{item.pdf_exists ? 'PDF' : 'draft'}</Tag>
        <span className="row-title row-title--wrap">{item.title ?? item.report_id}</span>
      </ListRow>
      <div className="report-row-sub">
        <code>{item.report_id}</code>
        <span>{item.evidence_mode}</span>
        <span>{timeAgo(item.updated_at)}</span>
      </div>
    </button>
  );
}

interface OrderedReference {
  id: string;
  label: string;
  entry: ReportReferenceEntry;
  fact?: ReportFact;
  factLabel?: string;
}

function orderedReferences(
  evidence?: ReportEvidence | null,
  references?: ReportReferences | null,
  citedKeys?: string[],
): OrderedReference[] {
  const facts = evidence?.facts ?? [];
  const factById = new Map<string, ReportFact>();
  for (const fact of facts) {
    if (fact.fact_id) factById.set(fact.fact_id, fact);
  }
  const entryByKey = new Map<string, ReportReferenceEntry>();
  for (const entry of references?.entries ?? []) {
    if (entry.key) entryByKey.set(entry.key, entry);
  }
  const factLabelByKey = new Map<string, string>();
  for (const item of references?.fact_labels ?? []) {
    if (item.citation_key) factLabelByKey.set(item.citation_key, item.label || factLabel(item.fact_id ?? ''));
  }
  const out: OrderedReference[] = [];
  referenceKeyOrder(references, citedKeys).forEach((key, index) => {
    const entry = entryByKey.get(key);
    if (!entry) return;
    const factId = entry.fact_id || '';
    const item: OrderedReference = { id: key, label: `R${index + 1}`, entry };
    const fact = factId ? factById.get(factId) : undefined;
    const label = factLabelByKey.get(key);
    if (fact) item.fact = fact;
    if (label) item.factLabel = label;
    out.push(item);
  });
  return out;
}

function referenceKeyOrder(references?: ReportReferences | null, citedKeys?: string[]): string[] {
  const out: string[] = [];
  const seen = new Set<string>();
  const push = (key?: string) => {
    if (key && !seen.has(key)) {
      seen.add(key);
      out.push(key);
    }
  };
  if (citedKeys?.length) {
    citedKeys.forEach(push);
    return out;
  }
  for (const section of references?.sections ?? []) {
    (section.cite_keys ?? []).forEach(push);
  }
  if (out.length > 0) return out;
  for (const item of references?.fact_labels ?? []) push(item.citation_key);
  return out;
}

function titleForEntry(item: OrderedReference): string {
  return item.fact?.claim_summary ?? item.entry.fields?.title ?? item.entry.path ?? item.entry.key ?? item.id;
}

function metadataText(item: OrderedReference): string {
  const fields = item.entry.fields ?? {};
  return [
    fields.title ? `**Title:** ${fields.title}` : '',
    fields.howpublished ? `**Source:** ${fields.howpublished}` : '',
    fields.note ? `**Note:** ${fields.note}` : '',
    item.entry.path ? `**Path:** \`${item.entry.path}\`` : '',
    item.entry.request_id ? `**Request:** \`${item.entry.request_id}\`` : '',
  ].filter(Boolean).join('\n\n') || 'Reference metadata unavailable.';
}

function ReferenceRow({ item, open, onToggle }: {
  item: OrderedReference;
  open: boolean;
  onToggle: () => void;
}) {
  const factId = item.entry.fact_id ?? item.fact?.fact_id ?? '';
  const detail = useFactDetail(open && factId ? factId : null);
  const body = item.fact?.body ?? detail.data?.fact?.body;
  const loadingText = detail.isLoading ? 'Loading fact statement...' : 'Fact statement unavailable.';
  return (
    <div className={`report-ref-fact${open ? ' report-ref-fact--open' : ''}`}>
      <button className="report-ref-fact-head" onClick={onToggle}>
        <span className="report-ref-label">{item.label}</span>
        <span className="report-ref-main">
          <span className="report-ref-title" title={titleForEntry(item)}>{titleForEntry(item)}</span>
          <span className="report-ref-meta">
            <em>{item.entry.kind ?? 'reference'}</em>
            {item.factLabel && <em>{item.factLabel}</em>}
            {item.entry.key && <code title={item.entry.key}>{item.entry.key}</code>}
          </span>
        </span>
      </button>
      {open && (
        <div className="report-ref-body">
          <dl>
            <span>
              <dt>kind</dt>
              <dd>{item.entry.kind ?? 'reference'}</dd>
            </span>
            <span>
              <dt>role</dt>
              <dd>{item.entry.role ?? 'none'}</dd>
            </span>
            <span>
              <dt>file</dt>
              <dd>{item.entry.path ?? item.fact?.path ?? 'unknown'}</dd>
            </span>
            {item.fact && (
              <>
                <span>
                  <dt>status</dt>
                  <dd>{item.fact.status ?? 'unknown'}</dd>
                </span>
                <span>
                  <dt>verification</dt>
                  <dd>{item.fact.verification ?? item.entry.request_id ?? 'none'}</dd>
                </span>
              </>
            )}
          </dl>
          <div className="report-ref-statement report-ref-statement--md">
            {factId ? (
              body ? <Markdown text={body} /> : <span className="dim">{loadingText}</span>
            ) : (
              <Markdown text={metadataText(item)} />
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function EvidenceSidebar({
  evidence,
  references,
  citedKeys,
}: {
  evidence?: ReportEvidence | null;
  references?: ReportReferences | null;
  citedKeys?: string[];
}) {
  const items = orderedReferences(evidence, references, citedKeys);
  const [openRefId, setOpenRefId] = useState<string | null>(items[0]?.id ?? null);
  useEffect(() => {
    setOpenRefId(items[0]?.id ?? null);
  }, [evidence?.report_id, evidence?.version, references?.include_internal, citedKeys?.join('|')]);
  return (
    <aside className="report-side">
      <SectionCard title="References">
        {evidence?.answer?.verified_positive_result && (
          <div className="report-side-block report-side-block--summary">
            <div className="report-side-title">Verified result</div>
            <p className="report-side-text">{evidence.answer.verified_positive_result}</p>
          </div>
        )}
        <div className="report-side-block report-side-block--refs">
          <div className="report-side-title">Cited references · {items.length}</div>
          <div className="report-ref-list">
            {items.length === 0 && <span className="dim">No cited references recorded.</span>}
            {items.map((item) => (
              <ReferenceRow
                key={item.id}
                item={item}
                open={openRefId === item.id}
                onToggle={() => setOpenRefId(openRefId === item.id ? null : item.id)}
              />
            ))}
          </div>
        </div>
      </SectionCard>
    </aside>
  );
}

function ExportMenu({
  reportId,
  version,
  pdfExists,
  sourceExists,
}: {
  reportId?: string;
  version?: string;
  pdfExists?: boolean;
  sourceExists?: boolean;
}) {
  const pdfUrl = reportExportUrl(reportId, version, 'pdf');
  const sourceUrl = reportExportUrl(reportId, version, 'source-zip');
  return (
    <details className="report-export-menu">
      <summary>Export</summary>
      <div className="report-export-popover">
        {pdfExists && pdfUrl ? (
          <a href={pdfUrl}>Download PDF</a>
        ) : (
          <span className="report-export-disabled">Download PDF</span>
        )}
        {sourceExists && sourceUrl ? (
          <a href={sourceUrl}>Download Source ZIP</a>
        ) : (
          <span className="report-export-disabled">Download Source ZIP</span>
        )}
      </div>
    </details>
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
        <SectionCard title={`Report workspaces · ${data?.report_count ?? 0}`}>
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
          <div className="report-pdf-head-main">
            <h2>{workspace?.report?.title ?? selected?.title ?? 'Reports'}</h2>
            <code>{current?.main_tex ?? selected?.main_tex ?? 'reports'}</code>
          </div>
          <div className="report-pdf-actions">
            {versions.length > 0 && (
              <label className="report-version-control">
                <span>Report version</span>
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
              </label>
            )}
            <ExportMenu
              reportId={workspace?.report?.report_id ?? selected?.report_id}
              version={workspace?.selected_version ?? current?.version ?? selected?.current_version}
              pdfExists={current?.pdf_exists}
              sourceExists={current?.main_tex_exists}
            />
          </div>
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
        citedKeys={workspace?.cited_keys}
      />
    </div>
  );
}
