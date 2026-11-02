<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { BookOpen, FileText, FolderOpen, History, RefreshCw, Search, Upload } from '@lucide/vue';
import { useResearchStore } from '@/features/research/store';
import { fetchDocuments, fetchRecords, fetchStrategies, uploadDocument } from '@/features/research/api';
import type { DocumentItem, RecordItem, StrategyItem } from '@/features/research/types';
import { ApiError } from '@/shared/api/errors';
import { Input } from '@/shared/components/ui/input';
import { Select } from '@/shared/components/ui/select';
import { Button } from '@/shared/components/ui/button';
import { Badge } from '@/shared/components/ui/badge';

const props = defineProps<{ apiEnabled: boolean; refreshKey: number }>();
defineEmits<{ openRecord: [runId: string] }>();
const store = useResearchStore();
const tabs = [{ id: 'fundamental', label: '基本面库' }, { id: 'strategy', label: '策略 Wiki' }, { id: 'records', label: '复盘记录' }] as const;
const tab = ref<(typeof tabs)[number]['id']>('fundamental'); const search = ref('');
const documents = ref<DocumentItem[]>([]); const strategies = ref<StrategyItem[]>([]); const records = ref<RecordItem[]>([]);
const selectedDocument = ref<DocumentItem | null>(null); const error = ref(''); const loading = ref(false);
const uploadOpen = ref(false); const uploading = ref(false); const file = ref<File | null>(null); const input = ref<HTMLInputElement | null>(null);
const uploadSymbol = ref(''); const uploadType = ref('disclosure'); const uploadDate = ref(''); const uploadMessage = ref('');
let alive = true; let loadVersion = 0;
const filteredDocuments = computed(() => documents.value.filter(item => `${item.filename} ${item.symbol}`.toLowerCase().includes(search.value.toLowerCase())));
const filteredStrategies = computed(() => strategies.value.filter(item => `${item.title} ${item.strategy_id} ${item.version}`.toLowerCase().includes(search.value.toLowerCase())));
const filteredRecords = computed(() => records.value.filter(item => `${item.question} ${item.symbol} ${item.strategy_version || ''}`.toLowerCase().includes(search.value.toLowerCase())));
const demoSources = computed(() => store.sources.filter(item => item.kind === tab.value));
async function refresh() {
  if (!props.apiEnabled || !alive) return;
  const version = ++loadVersion; loading.value = true; error.value = '';
  const results = await Promise.allSettled([fetchDocuments(), fetchStrategies(), fetchRecords()]);
  if (!alive || version !== loadVersion) return;
  const [docs, wiki, history] = results;
  if (docs.status === 'fulfilled' && Array.isArray(docs.value)) documents.value = docs.value;
  if (wiki.status === 'fulfilled' && wiki.value.ok && Array.isArray(wiki.value.strategies)) strategies.value = wiki.value.strategies;
  if (history.status === 'fulfilled' && Array.isArray(history.value)) records.value = history.value;
  if (results.some(result => result.status === 'rejected')) error.value = '部分列表读取失败，请检查后端后刷新；不会用示例资料替代。';
  loading.value = false;
}
function chooseFile(event: Event) { file.value = (event.target as HTMLInputElement).files?.[0] || null; uploadMessage.value = ''; }
function toggleUpload() { uploadOpen.value = !uploadOpen.value; uploadSymbol.value ||= store.context.symbol; }
async function upload() {
  if (!file.value || !uploadSymbol.value.trim() || uploading.value) return;
  uploading.value = true; uploadMessage.value = '';
  try {
    const result = await uploadDocument({ filename: file.value.name, content: await file.value.text(), symbol: uploadSymbol.value.trim(), source_type: uploadType.value, available_date: uploadDate.value || null });
    uploadMessage.value = result.action === 'SKIP' ? '内容未变化，已保留原资料。' : '资料已上传，列表状态以后端为准。';
    file.value = null; if (input.value) input.value.value = ''; await refresh();
  } catch (cause) { uploadMessage.value = cause instanceof ApiError ? cause.message : '文件读取或上传失败。'; }
  finally { uploading.value = false; }
}
function chooseStrategy(item: StrategyItem) { store.updateContext({ strategy_id: item.strategy_id, strategy_version: item.version }); }
onMounted(refresh); watch(() => props.refreshKey, refresh); watch(() => props.apiEnabled, refresh);
onBeforeUnmount(() => { alive = false; loadVersion += 1; });
</script>
<template>
  <aside class="source-panel" aria-label="资料与研究记录">
    <div class="space-y-3 border-b p-3">
      <div class="grid grid-cols-3 rounded-lg bg-muted p-1" role="group" aria-label="资料分类"><button v-for="item in tabs" :key="item.id" type="button" class="source-tab rounded-md px-1 py-1.5 text-[11px] transition-[background-color,color,box-shadow] duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1 active:scale-[0.98]" :class="tab === item.id ? 'bg-card font-medium text-foreground shadow-sm hover:bg-blue-50 hover:text-blue-700' : 'text-muted-foreground hover:bg-card/80 hover:text-foreground'" :aria-pressed="tab === item.id" @click="tab = item.id; search = ''">{{ item.label }}</button></div>
      <div class="flex gap-1"><Input v-model="search" class="h-8 min-w-0 flex-1 text-xs" placeholder="筛选当前列表…" aria-label="筛选资料"><template #suffix><Search class="size-3.5 text-muted-foreground" aria-hidden="true" /></template></Input><Button size="icon" variant="ghost" :disabled="!apiEnabled || loading" aria-label="刷新资料列表" @click="refresh"><RefreshCw class="size-3.5" :class="loading ? 'animate-spin' : ''" /></Button></div>
      <Button v-if="tab === 'fundamental'" variant="outline" class="w-full border-dashed text-xs" :disabled="!apiEnabled || store.isRunning || store.viewMode === 'demo'" @click="toggleUpload"><Upload class="mr-1.5 size-3.5" />上传 Markdown</Button>
      <p v-else-if="tab === 'strategy'" class="flex gap-1 text-[11px] text-muted-foreground"><BookOpen class="size-3.5 shrink-0" />只读策略 Wiki，点击选择本次对照版本。</p>
      <p v-else class="text-[11px] text-muted-foreground">仅展示后端已确认归档的完整快照。</p>
      <form v-if="uploadOpen && tab === 'fundamental' && store.viewMode !== 'demo'" class="space-y-2 rounded-lg border p-2" @submit.prevent="upload">
        <Input v-model="uploadSymbol" aria-label="上传资料标的" placeholder="资料标的，如601318.SH" :disabled="uploading" class="text-xs" />
        <Select v-model="uploadType" aria-label="资料类型" :disabled="uploading" class="text-xs"><option value="disclosure">财报／公告</option><option value="research_report">研报</option><option value="personal_note">个人笔记</option><option value="ai_generated">AI生成资料</option></Select>
        <label class="block text-[10px] text-muted-foreground">登记公开／形成日（未知可空）<Input v-model="uploadDate" type="date" aria-label="资料登记日期" :disabled="uploading" class="mt-1 text-xs" /></label>
        <label class="relative flex h-8 w-full min-w-0 items-center justify-between gap-2.5 rounded-md border border-input bg-card px-2.5 text-[11px] transition-colors duration-150 focus-within:border-ring focus-within:ring-1 focus-within:ring-ring" :class="uploading ? 'cursor-not-allowed bg-muted opacity-60' : 'cursor-pointer hover:border-blue-300 hover:bg-accent/40'"><span class="min-w-0 truncate" :title="file?.name">{{ file?.name || '选择 Markdown 文件' }}</span><Upload class="size-3.5 shrink-0 text-muted-foreground" aria-hidden="true" /><input ref="input" type="file" accept=".md,text/markdown" aria-label="选择 Markdown 文件" class="absolute inset-0 h-full w-full opacity-0" :class="uploading ? 'cursor-not-allowed' : 'cursor-pointer'" :disabled="uploading" @change="chooseFile" /></label>
        <Button type="submit" size="sm" class="w-full" :disabled="!file || !uploadSymbol.trim() || uploading">{{ uploading ? '正在上传…' : '提交资料' }}</Button>
        <p v-if="uploadMessage" class="text-[11px] leading-relaxed" role="status">{{ uploadMessage }}</p>
      </form>
      <p v-if="error" class="text-[11px] leading-relaxed text-destructive" role="alert">{{ error }}</p>
    </div>
    <div class="flex-1 space-y-2 p-3">
      <template v-if="store.viewMode === 'demo'"><button v-for="item in demoSources" :key="item.id" class="source-card w-full rounded-lg border p-3 text-left" @click="store.selectCitation(item.citation_id)"><p class="text-xs">{{ item.title }}</p><Badge variant="warning" class="mt-2">界面示例</Badge></button><p v-if="!demoSources.length" class="p-4 text-xs text-muted-foreground">示例不会保存为复盘记录。</p></template>
      <template v-else-if="tab === 'fundamental' && filteredDocuments.length">
        <button v-for="item in filteredDocuments" :key="item.document_id" class="source-card w-full rounded-lg border p-3 text-left" :class="selectedDocument?.document_id === item.document_id ? 'border-blue-300' : ''" @click="selectedDocument = item"><span class="flex gap-2"><FileText class="mt-0.5 size-4 shrink-0 text-blue-600" /><span class="break-words text-xs font-medium">{{ item.filename }}</span></span><span class="mt-2 block text-[11px] text-muted-foreground">{{ item.symbol }} · {{ item.available_date || '登记日期未知' }}</span><Badge :variant="item.status === 'READY' ? 'outline' : 'warning'" class="mt-2">{{ item.status }}</Badge></button>
        <div v-if="selectedDocument" class="rounded-lg border bg-muted/40 p-3 text-[11px] leading-relaxed"><p class="font-medium">资料信息</p><p>类型：{{ selectedDocument.source_type }}</p><p>片段：{{ selectedDocument.child_count }} · Parent：{{ selectedDocument.parent_count }}</p><p>入库：{{ selectedDocument.ingested_at.slice(0, 10) }}</p><p class="mt-2 text-muted-foreground">这里只查看资料元数据。报告引用在研究完成后按实际检索证据展示。</p><Button size="sm" variant="outline" class="mt-2" :disabled="store.isRunning" @click="store.updateContext({ symbol: selectedDocument.symbol })">使用此标的研究</Button></div>
      </template>
      <template v-else-if="tab === 'strategy' && filteredStrategies.length"><button v-for="item in filteredStrategies" :key="item.document_id" class="source-card w-full rounded-lg border p-3 text-left" :class="store.context.strategy_id === item.strategy_id && store.context.strategy_version === item.version ? 'border-blue-300 bg-blue-50/40' : ''" :disabled="store.isRunning || store.viewMode === 'history'" @click="chooseStrategy(item)"><p class="break-words text-xs font-medium">{{ item.title }}</p><p class="mt-2 text-[11px] text-muted-foreground">{{ item.strategy_id }} / {{ item.version }}</p><Badge variant="outline" class="mt-2">{{ item.status || '规则版本' }}</Badge><p v-if="item.verification_scope" class="mt-2 text-[10px] leading-relaxed text-muted-foreground">{{ item.verification_scope }}</p></button></template>
      <template v-else-if="tab === 'records' && filteredRecords.length"><button v-for="item in filteredRecords" :key="item.run_id" class="source-card w-full rounded-lg border p-3 text-left" :class="store.recordId === item.run_id ? 'border-blue-300' : ''" :disabled="store.isRunning" @click="$emit('openRecord', item.run_id)"><p class="line-clamp-3 break-words text-xs font-medium">{{ item.question }}</p><p class="mt-2 text-[11px] text-muted-foreground">{{ item.symbol }} · {{ item.strategy_version || '未选策略' }}</p><p class="mt-1 text-[10px] text-muted-foreground">确认：{{ item.confirmed_at }}</p><Badge variant="outline" class="mt-2">已归档</Badge></button></template>
      <div v-else class="source-empty"><History v-if="tab === 'records'" class="size-7 text-slate-300" /><FolderOpen v-else class="size-7 text-slate-300" /><p class="text-xs font-medium">{{ !apiEnabled ? '真实接口尚未开启' : loading ? '正在读取列表…' : search ? '没有匹配内容' : tab === 'records' ? '暂无已归档研究' : tab === 'strategy' ? '暂无可用策略版本' : '暂无基本面资料' }}</p><p class="text-center text-[11px] leading-relaxed text-muted-foreground">{{ tab === 'records' ? '研究成功后确认保存，即可在此回看原快照。' : tab === 'strategy' ? '请核对后端策略目录配置。' : '上传 Markdown 后可由 Agent 检索原文。' }}</p></div>
    </div>
    <div class="border-t p-3 text-[11px] text-muted-foreground">{{ store.viewMode === 'demo' ? '示例资料仅用于界面预览' : `${documents.length}份资料 · ${strategies.length}个策略版本 · ${records.length}条归档` }}</div>
  </aside>
</template>
<style scoped>
.source-panel { display:flex; flex-direction:column; min-width:0; background:hsl(var(--card)); border-right:1px solid hsl(var(--border)); }
.source-card { transition:background-color 150ms ease, border-color 150ms ease, box-shadow 150ms ease; }
.source-card:not(:disabled):hover { border-color:rgb(147 197 253); background:hsl(var(--accent) / .5); }
.source-card:focus-visible { outline:2px solid hsl(var(--ring)); outline-offset:2px; }
.source-card:disabled { opacity:.6; }
.source-empty { display:flex; flex-direction:column; align-items:center; gap:10px; padding:38px 6px; }
@media(pointer:coarse) { .source-tab { min-height:44px; } }
@media(max-width:1050px) { .source-panel { border-right:0; border-bottom:1px solid hsl(var(--border)); } .source-empty { padding:18px 6px; } }
</style>
