import { request } from '@/shared/api/request';
import { ApiError } from '@/shared/api/errors';
import { isObject, type DocumentItem, type RecordItem, type ResearchContext, type ResearchResult, type StrategyItem } from './types';

export const researchApiEnabled = import.meta.env.VITE_ENABLE_RESEARCH_API === 'true';
function invalidResponse(): never {
  throw new ApiError('后端返回的研究快照不完整，本次不能确认归档。', { kind: 'invalid_response', code: 'INVALID_RESEARCH_RESULT' });
}
export function parseResearchResult(value: unknown): ResearchResult {
  if (!isObject(value) || typeof value.run_id !== 'string' || !value.run_id.trim()
    || !['success', 'failed'].includes(String(value.status)) || typeof value.question !== 'string'
    || !isObject(value.context) || typeof value.context.symbol !== 'string'
    || typeof value.started_at !== 'string' || !(value.finished_at === null || typeof value.finished_at === 'string')
    || typeof value.answer !== 'string' || !isObject(value.citations)
    || !Array.isArray(value.tool_snapshots) || !value.tool_snapshots.every(isObject)
    || !Array.isArray(value.steps) || !value.steps.every(isObject)
    || (value.status === 'success' && (typeof value.finished_at !== 'string' || !value.finished_at.trim()))
    || (value.archive_status !== undefined && !['completed', 'archived', 'failed'].includes(String(value.archive_status)))
    || (value.status === 'success' && value.archive_status === 'failed')) invalidResponse();
  for (const [id, citation] of Object.entries(value.citations)) {
    if (!/^R[1-9]\d*-[1-9]\d*$/.test(id) || !isObject(citation)
      || typeof citation.source !== 'string' || !citation.source.trim()
      || typeof citation.excerpt !== 'string' || !citation.excerpt.trim()
      || (citation.citation_id !== undefined && citation.citation_id !== id)) invalidResponse();
  }
  return value as unknown as ResearchResult;
}
export async function startResearch(options: { question: string; context: ResearchContext; signal: AbortSignal }) {
  const context = {
    symbol: options.context.symbol.trim(), instrument_type: 'stock',
    materials_cutoff_date: options.context.materials_cutoff_date || null,
    market_cutoff_date: options.context.market_cutoff_date || null,
    strategy_id: options.context.strategy_id || null, strategy_version: options.context.strategy_version || null,
  };
  return parseResearchResult(await request<unknown>('/api/research/run', {
    method: 'POST', body: { question: options.question, context }, signal: options.signal, timeoutMs: 200_000,
  }));
}
export async function confirmResearch(runId: string) {
  const result = await request<{ record_id: string; run_id: string; status: string; confirmed_at: string }>(
    `/api/research/${encodeURIComponent(runId)}/confirm`, { method: 'POST' },
  );
  if (!result || result.run_id !== runId || result.record_id !== runId || result.status !== 'archived'
    || typeof result.confirmed_at !== 'string') invalidResponse();
  return result;
}
export function fetchDocuments() { return request<DocumentItem[]>('/api/documents'); }
export function uploadDocument(body: { filename: string; content: string; symbol: string; source_type: string; available_date: string | null }) {
  return request<{ action: string; document_id: string }>('/api/documents', { method: 'POST', body, timeoutMs: 200_000 });
}
export function fetchStrategies() { return request<{ ok: boolean; strategies: StrategyItem[] }>('/api/strategies'); }
export function fetchRecords() { return request<RecordItem[]>('/api/records'); }
export async function fetchRecord(runId: string) {
  const result = parseResearchResult(await request<unknown>(`/api/records/${encodeURIComponent(runId)}`));
  if (result.run_id !== runId || result.archive_status !== 'archived' || result.status !== 'success') invalidResponse();
  return result;
}
