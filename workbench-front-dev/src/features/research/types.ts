export interface ResearchContext {
  symbol: string;
  instrument_type: 'stock';
  materials_cutoff_date: string;
  market_cutoff_date: string;
  strategy_id: string;
  strategy_version: string;
}

export interface RawCitation { source: string; excerpt: string; citation_id?: string; [key: string]: unknown }

export interface Evidence {
  citation_id: string; source: string; heading_path: string; excerpt: string;
  source_type: 'document' | 'wiki' | 'market'; version: string; available_at?: string;
  available_date?: string; ingested_at?: string; actual_range?: Record<string, unknown>;
  units?: Record<string, unknown>; adjustment?: string;
}

export interface ResearchResult {
  run_id: string; status: 'success' | 'failed'; question: string;
  context: { symbol: string; instrument_type?: 'stock'; materials_cutoff_date?: string | null;
    market_cutoff_date?: string | null; strategy_id?: string | null; strategy_version?: string | null };
  started_at: string; finished_at: string | null; answer: string;
  citations: Record<string, RawCitation>; tool_snapshots: Array<Record<string, unknown>>;
  steps: Array<Record<string, unknown>>; archive_status?: 'completed' | 'archived' | 'failed';
  confirmed_at?: string | null; error?: { code: string; message?: string } | null;
  [key: string]: unknown;
}

export interface DocumentItem {
  document_id: string; filename: string; symbol: string;
  source_type: 'disclosure' | 'research_report' | 'personal_note' | 'ai_generated';
  available_date: string | null; ingested_at: string; status: string; parent_count: number; child_count: number;
}
export interface StrategyItem {
  document_id: string; strategy_id: string; version: string; title: string;
  status?: string; summary?: string; verification_scope?: string;
}
export interface RecordItem {
  record_id: string; run_id: string; symbol: string; strategy_id: string | null;
  strategy_version: string | null; question: string; started_at: string; confirmed_at: string;
}
export interface SourceItem { id: string; title: string; kind: 'fundamental' | 'strategy'; note: string; citation_id: string }
export interface ToolStep { id: string; label: string; status: 'running' | 'completed' | 'failed'; duration_ms?: number }
export type ResearchStatus = 'idle' | 'running' | 'completed' | 'draft_ready' | 'saving' | 'saved' | 'error';
export type AnswerSegment = { kind: 'text'; value: string } | { kind: 'citation'; value: string };
export function splitAnswerCitations(text: string): AnswerSegment[] {
  return text.split(/(\[R\d+-\d+\])/g).filter(Boolean).map(value => /^\[R\d+-\d+\]$/.test(value)
    ? { kind: 'citation' as const, value: value.slice(1, -1) } : { kind: 'text' as const, value });
}
export function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}
