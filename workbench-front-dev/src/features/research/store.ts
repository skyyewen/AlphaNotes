import { computed, ref } from 'vue';
import { defineStore } from 'pinia';
import { DEMO_ANSWER, DEMO_EVIDENCE, DEMO_SOURCES } from './demo';
import { isObject, type Evidence, type ResearchContext, type ResearchResult, type ResearchStatus, type SourceItem, type ToolStep } from './types';

const initialContext = (): ResearchContext => ({
  symbol: '', instrument_type: 'stock', materials_cutoff_date: '', market_cutoff_date: '', strategy_id: '', strategy_version: '',
});
const text = (value: unknown, fallback = '') => typeof value === 'string' ? value : fallback;
function displaySteps(events: Array<Record<string, unknown>>): ToolStep[] {
  const output: ToolStep[] = []; const counts: Record<string, number> = {}; const pending = new Map<string, ToolStep>();
  for (const event of events) {
    const kind = text(event.step, 'step'); const tool = text(event.tool);
    const label = tool || ({ model: '模型查证', validate: '引用检查' } as Record<string, string>)[kind] || kind;
    const status = ['running', 'completed', 'failed'].includes(String(event.status)) ? event.status as ToolStep['status'] : 'failed';
    const key = `${kind}:${tool}`;
    let step = status === 'running' ? undefined : pending.get(key);
    if (!step) { counts[kind] = (counts[kind] ?? 0) + 1; step = { id: `${kind}_${counts[kind]}`, label, status }; output.push(step); }
    step.status = status;
    if (typeof event.duration_ms === 'number') step.duration_ms = event.duration_ms;
    if (status === 'running') pending.set(key, step); else pending.delete(key);
  }
  return output;
}
export const useResearchStore = defineStore('research', () => {
  const context = ref<ResearchContext>(initialContext()); const frozenContext = ref<ResearchContext | null>(null);
  const viewMode = ref<'live' | 'demo' | 'history'>('live'); const status = ref<ResearchStatus>('idle');
  const question = ref(''); const submittedQuestion = ref(''); const answer = ref('');
  const runId = ref<string | null>(null); const recordId = ref<string | null>(null); const archiveStatus = ref<string | null>(null);
  const startedAt = ref(''); const finishedAt = ref(''); const confirmedAt = ref('');
  const citations = ref<Record<string, Evidence>>({}); const sources = ref<SourceItem[]>([]); const steps = ref<ToolStep[]>([]);
  const selectedCitation = ref<string | null>(null); const marketSnapshot = ref<Record<string, unknown> | null>(null);
  const errorMessage = ref(''); const errorCode = ref(''); const transportComplete = ref(false); let generation = 0;
  const isRunning = computed(() => status.value === 'running' || status.value === 'saving');
  const canConfirm = computed(() => viewMode.value === 'live' && status.value === 'draft_ready'
    && transportComplete.value && archiveStatus.value === 'completed' && Boolean(runId.value));
  const selectedEvidence = computed(() => selectedCitation.value ? citations.value[selectedCitation.value] : undefined);
  function clearRun() {
    generation += 1; frozenContext.value = null; status.value = 'idle'; submittedQuestion.value = answer.value = '';
    runId.value = recordId.value = selectedCitation.value = null; archiveStatus.value = null;
    startedAt.value = finishedAt.value = confirmedAt.value = ''; citations.value = {}; sources.value = []; steps.value = []; marketSnapshot.value = null;
    errorMessage.value = errorCode.value = ''; transportComplete.value = false; return generation;
  }
  function resetResearch() { clearRun(); viewMode.value = 'live'; }
  function updateContext(patch: Partial<ResearchContext>) {
    if (isRunning.value) return false;
    resetResearch(); context.value = { ...context.value, ...patch }; return true;
  }
  function beginResearch() {
    const ticket = clearRun(); viewMode.value = 'live'; frozenContext.value = { ...context.value };
    submittedQuestion.value = question.value.trim(); status.value = 'running'; return ticket;
  }
  function isCurrent(ticket: number) { return ticket === generation; }
  function currentTicket() { return generation; }
  function hydrate(result: ResearchResult) {
    runId.value = result.run_id; submittedQuestion.value = result.question; answer.value = result.answer;
    context.value = {
      symbol: result.context.symbol, instrument_type: 'stock', materials_cutoff_date: result.context.materials_cutoff_date || '',
      market_cutoff_date: result.context.market_cutoff_date || '', strategy_id: result.context.strategy_id || '', strategy_version: result.context.strategy_version || '',
    };
    frozenContext.value = { ...context.value }; startedAt.value = result.started_at; finishedAt.value = result.finished_at || ''; confirmedAt.value = result.confirmed_at || '';
    archiveStatus.value = result.archive_status || null;
    const mapped: Record<string, Evidence> = {};
    for (const [id, item] of Object.entries(result.citations)) {
      const kind = item.source_kind === 'wiki' || item.source_type === 'wiki' ? 'wiki' : item.source_kind === 'market' ? 'market' : 'document';
      mapped[id] = { citation_id: id, source: item.source, excerpt: item.excerpt, heading_path: text(item.heading_path, '工具证据'),
        source_type: kind, version: text(item.version, '未提供'), available_date: text(item.available_date), ingested_at: text(item.ingested_at),
        actual_range: isObject(item.actual_range) ? item.actual_range : undefined,
        units: isObject(item.units) ? item.units : undefined, adjustment: text(item.adjustment) };
    }
    citations.value = mapped; selectedCitation.value = Object.keys(mapped)[0] || null; steps.value = displaySteps(result.steps); marketSnapshot.value = null;
    for (const call of result.tool_snapshots) {
      if (call.tool === 'query_stock_data' && isObject(call.result) && isObject(call.result.snapshot)) marketSnapshot.value = call.result.snapshot;
    }
    transportComplete.value = true;
  }
  function acceptResult(result: ResearchResult, ticket: number) {
    if (!isCurrent(ticket) || viewMode.value !== 'live' || status.value !== 'running') return false;
    if (result.status !== 'success') { failRun('研究未成功完成，本次不能归档。', ticket); return false; }
    hydrate(result); status.value = result.archive_status === 'completed' ? 'draft_ready' : result.archive_status === 'archived' ? 'saved' : 'completed';
    if (result.archive_status === 'archived') recordId.value = result.run_id;
    return true;
  }
  function beginHistory() { const ticket = clearRun(); viewMode.value = 'history'; return ticket; }
  function acceptHistory(result: ResearchResult, ticket: number) {
    if (!isCurrent(ticket) || viewMode.value !== 'history' || result.status !== 'success' || result.archive_status !== 'archived') return false;
    hydrate(result); status.value = 'saved'; recordId.value = result.run_id; return true;
  }
  function failRun(message: string, ticket: number, code = 'research_failed') {
    if (!isCurrent(ticket)) return;
    status.value = 'error'; transportComplete.value = false; errorMessage.value = message; errorCode.value = code;
  }
  function markSaving() { if (!canConfirm.value) return false; status.value = 'saving'; return true; }
  function markSaved(id: string, confirmed = '') {
    if (status.value !== 'saving' || id !== runId.value) return false;
    recordId.value = id; archiveStatus.value = 'archived'; confirmedAt.value = confirmed; status.value = 'saved'; return true;
  }
  function saveFailed() { if (status.value === 'saving') status.value = 'draft_ready'; }
  function previewDemo() {
    clearRun(); viewMode.value = 'demo'; submittedQuestion.value = '结合资料与我的规则，列出已有证据、反证和暂时无法判断的条件。';
    answer.value = DEMO_ANSWER; citations.value = structuredClone(DEMO_EVIDENCE); sources.value = structuredClone(DEMO_SOURCES); selectedCitation.value = 'R1-1';
  }
  function selectCitation(id: string) { if (!citations.value[id]) return false; selectedCitation.value = id; return true; }
  return { context, frozenContext, viewMode, status, question, submittedQuestion, answer, runId, recordId, archiveStatus, startedAt, finishedAt, confirmedAt,
    citations, sources, steps, selectedCitation, selectedEvidence, marketSnapshot, errorMessage, errorCode, transportComplete,
    isRunning, canConfirm, resetResearch, updateContext, beginResearch, isCurrent, currentTicket, acceptResult, beginHistory, acceptHistory,
    failRun, markSaving, markSaved, saveFailed, previewDemo, selectCitation };
});
