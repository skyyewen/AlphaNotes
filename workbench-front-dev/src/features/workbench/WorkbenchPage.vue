<script setup lang="ts">
import { onBeforeUnmount, ref } from 'vue';
import { ApiError } from '@/shared/api/errors';
import { useResearchStore } from '@/features/research/store';
import { confirmResearch, fetchRecord, researchApiEnabled, startResearch } from '@/features/research/api';
import WorkbenchHeader from './components/WorkbenchHeader.vue';
import SourcePanel from './components/SourcePanel.vue';
import ResearchPanel from './components/ResearchPanel.vue';
import EvidencePanel from './components/EvidencePanel.vue';

const store = useResearchStore(); const notice = ref(''); const recordsRefresh = ref(0);
let controller: AbortController | null = null;
function stopTransport() { controller?.abort(); controller = null; }
function resetResearch() { stopTransport(); store.resetResearch(); notice.value = ''; }
function previewDemo() { stopTransport(); store.previewDemo(); notice.value = ''; }
async function submitResearch() {
  if (!researchApiEnabled || store.isRunning || !store.question.trim()) return;
  if (!/^\d{6}(?:\.(?:SH|SZ|BJ))?$/i.test(store.context.symbol.trim())) { notice.value = '请填写六位股票代码，可带 .SH / .SZ / .BJ 后缀。'; return; }
  if (Boolean(store.context.strategy_id) !== Boolean(store.context.strategy_version)) { notice.value = '策略ID和版本须成对选择，也可以不选策略。'; return; }
  notice.value = ''; stopTransport();
  const localController = new AbortController(); controller = localController;
  const ticket = store.beginResearch();
  try {
    const result = await startResearch({ question: store.submittedQuestion, context: { ...store.frozenContext! }, signal: localController.signal });
    if (store.isCurrent(ticket) && !store.acceptResult(result, ticket)) store.failRun('研究结果未通过完整性检查，不能归档。', ticket, 'INVALID_FINAL_STATE');
    else if (store.isCurrent(ticket) && !store.canConfirm) notice.value = '报告已返回，但后端未确认草稿持久化，暂不能归档。';
  } catch (error) {
    if (!store.isCurrent(ticket)) return;
    store.failRun(error instanceof ApiError ? error.message : '研究请求失败，请检查后端。', ticket, error instanceof ApiError ? error.code : 'research_failed');
  } finally { if (controller === localController) controller = null; }
}
async function saveResearch() {
  if (!researchApiEnabled || !store.canConfirm || !store.runId) return;
  const runId = store.runId; const ticket = store.currentTicket();
  if (!store.markSaving()) return;
  try {
    const result = await confirmResearch(runId);
    if (!store.isCurrent(ticket) || store.runId !== runId) return;
    if (store.markSaved(result.record_id, result.confirmed_at)) { notice.value = '已确认归档，可在左侧复盘记录中回看。'; recordsRefresh.value += 1; }
  } catch {
    if (!store.isCurrent(ticket) || store.runId !== runId) return;
    store.saveFailed(); notice.value = '保存未获后端确认，请重试；重复确认不会生成第二条记录。';
  }
}
async function openRecord(runId: string) {
  if (!researchApiEnabled) return;
  stopTransport(); const ticket = store.beginHistory(); notice.value = '正在读取历史快照…';
  try {
    const result = await fetchRecord(runId);
    if (store.acceptHistory(result, ticket)) notice.value = '历史快照只读展示，未重新调用模型或读取最新原文。';
  } catch (error) {
    if (!store.isCurrent(ticket)) return;
    store.failRun(error instanceof ApiError ? error.message : '读取历史记录失败。', ticket); notice.value = '';
  }
}
async function copyReport() {
  try { await navigator.clipboard.writeText(store.answer); notice.value = '已复制当前报告文本。'; }
  catch { notice.value = '浏览器未允许复制，请选择报告文字复制。'; }
}
onBeforeUnmount(stopTransport);
</script>

<template>
  <div class="workbench-app">
    <WorkbenchHeader :api-enabled="researchApiEnabled" @reset="resetResearch" @demo="previewDemo" />
    <div class="workbench-layout">
      <SourcePanel :api-enabled="researchApiEnabled" :refresh-key="recordsRefresh" @open-record="openRecord" />
      <ResearchPanel :api-enabled="researchApiEnabled" :notice="notice" @submit="submitResearch" @stop="stopTransport" @confirm="saveResearch" @demo="previewDemo" @copy="copyReport" />
      <EvidencePanel />
    </div>
  </div>
</template>

<style scoped>
.workbench-app { height:100dvh; display:flex; flex-direction:column; }
.workbench-layout { flex:1; min-height:0; display:grid; grid-template-columns:260px minmax(0,1fr) 320px; }
.workbench-layout > aside { min-height:0; overflow-y:auto; }
@media(max-width:1280px) and (min-width:1051px) { .workbench-layout { grid-template-columns:230px minmax(0,1fr) 280px; } }
@media(max-width:1050px) { .workbench-app { height:auto; min-height:100dvh; } .workbench-layout { grid-template-columns:minmax(0,1fr); } .workbench-layout > aside { overflow-y:visible; } }
</style>
