/** Versioned research reports (`iteris report ...`) for the Reports view. */

export interface ReportWorkspaceItem {
  report_id: string;
  title?: string;
  template?: string;
  style?: string;
  evidence_mode?: 'linked' | 'portable' | string;
  current_version?: string;
  updated_at?: string;
  main_tex?: string;
  pdf?: string;
  pdf_exists?: boolean;
  version_count?: number;
}

export interface ReportWorkspaceList {
  schema_version: string;
  reports_dir?: string;
  report_index?: string;
  fact_index?: string;
  templates?: string[];
  styles?: string[];
  report_count: number;
  items: ReportWorkspaceItem[];
}

export interface ReportVersion {
  version: string;
  created_at?: string;
  template?: string;
  style?: string;
  main_tex: string;
  main_tex_exists: boolean;
  pdf: string;
  pdf_exists: boolean;
  evidence: string;
  evidence_exists: boolean;
  references: string;
  references_exists: boolean;
  template_lock?: string;
  template_assets?: string;
  references_bib?: string;
}

export interface ReportFileRecord {
  path: string;
  exists: boolean;
}

export interface ReportFact {
  fact_id?: string;
  claim_summary?: string;
  path?: string;
  status?: string;
  review_level?: string;
  verification?: string;
  body?: string;
}

export interface ReportEvidence {
  schema_version?: string;
  report_id?: string;
  generated_at?: string;
  version?: string;
  answer?: {
    target_artifact?: string;
    target_exists?: boolean;
    verified_positive_result?: string;
    assembly_verification?: string;
    goal_success_verification?: string;
    goal_success_summary?: string;
  };
  fact_graph?: {
    fact_index?: string;
    fact_count?: number;
    checked_fact_ids?: string[];
  };
  facts?: ReportFact[];
  source_paths?: Array<{ role?: string; path?: string; exists?: boolean }>;
  checked_artifacts?: Array<{ kind?: string; path?: string }>;
  citations?: Record<string, unknown>;
}

export interface ReportReferences {
  schema_version?: string;
  include_internal?: boolean;
  bibliography?: string;
  omitted_reason?: string;
  entries?: Array<{ key?: string; kind?: string; role?: string; path?: string; fields?: Record<string, string> }>;
  fact_labels?: Array<{ label?: string; fact_id?: string; citation_key?: string; path?: string }>;
  sections?: Array<{ section_id?: string; cite_keys?: string[]; uses?: Record<string, unknown> }>;
  fact_graph?: { checked_fact_ids?: string[] };
}

export interface ReportWorkspaceDetail {
  schema_version: string;
  report: (ReportWorkspaceItem & {
    schema_version?: string;
    created_at?: string;
    paths?: Record<string, string>;
    style_profile?: Record<string, unknown>;
  }) | null;
  versions?: ReportVersion[];
  selected_version?: string;
  current?: ReportVersion | null;
  evidence?: ReportEvidence | null;
  references?: ReportReferences | null;
  template_lock?: Record<string, unknown> | null;
  author_draft?: ReportFileRecord;
  feedback?: ReportFileRecord;
  revision_log?: ReportFileRecord;
  notice?: string;
}
