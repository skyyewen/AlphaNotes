<script setup lang="ts">
import { computed } from 'vue';
import { BookOpen, FileSearch, Grid2X2, LockKeyhole, Quote } from '@lucide/vue';
import { useResearchStore } from '@/features/research/store';
import { isObject } from '@/features/research/types';
import { Badge } from '@/shared/components/ui/badge';
const store = useResearchStore(); const evidence = computed(() => store.selectedEvidence);
const snapshot = computed(() => store.marketSnapshot);
const latest = computed(() => snapshot.value && Array.isArray(snapshot.value.data) && isObject(snapshot.value.data[0]) ? snapshot.value.data[0] : null);
const range = computed(() => snapshot.value && isObject(snapshot.value.actual_range) ? snapshot.value.actual_range : null);
const units = computed(() => snapshot.value && isObject(snapshot.value.units) ? snapshot.value.units : null);
</script>
<template>
  <aside class="evidence-panel" aria-label="证据与数据快照">
    <div class="flex items-center justify-between gap-2 border-b pb-3"><h2 class="flex items-center gap-2 text-xs font-semibold"><FileSearch class="size-4 text-blue-600" />原文证据</h2><Badge variant="outline">{{ evidence ? ({ wiki: 'Wiki 规则', market: '行情快照', document: '资料片段' }[evidence.source_type]) : '待选择' }}</Badge></div>
    <section v-if="evidence" class="mt-4 rounded-lg border bg-muted/30 p-3.5" aria-live="polite">
      <div class="flex items-start gap-2"><Quote class="mt-0.5 size-4 shrink-0 text-blue-600" /><h3 class="break-words text-xs font-semibold">{{ evidence.source }}</h3></div>
      <div class="mt-3 flex flex-wrap gap-1.5 text-[11px] text-muted-foreground"><span class="rounded bg-blue-50 px-1.5 py-0.5 font-mono text-blue-700">[{{ evidence.citation_id }}]</span><span>{{ evidence.heading_path }}</span></div>
      <blockquote class="mt-3 whitespace-pre-wrap break-words rounded-md border bg-card p-3 text-xs leading-7">{{ evidence.excerpt }}</blockquote>
      <dl class="mt-3 grid gap-2 text-[11px]">
        <template v-if="evidence.source_type === 'document'"><div><dt class="text-muted-foreground">登记公开／形成日</dt><dd>{{ evidence.available_date || evidence.available_at || '未提供' }}</dd></div><div><dt class="text-muted-foreground">入库时间</dt><dd class="break-all">{{ evidence.ingested_at || '未提供' }}</dd></div><p class="text-muted-foreground">登记日期未经系统独立核验，不等同于已核实公开日。</p></template>
        <template v-else-if="evidence.source_type === 'wiki'"><div><dt class="text-muted-foreground">本次规则版本</dt><dd>{{ evidence.version }}</dd></div><p class="text-muted-foreground">规则版本与资料公开日分别保留，当前选择不代表已采用或验证有效。</p></template>
        <template v-else><div><dt class="text-muted-foreground">实际行情日期</dt><dd>{{ evidence.actual_range?.start_date || '未提供' }} ～ {{ evidence.actual_range?.end_date || '未提供' }}</dd></div><div><dt class="text-muted-foreground">复权口径</dt><dd>{{ evidence.adjustment === 'none' ? '未复权' : evidence.adjustment || '未提供' }}</dd></div></template>
      </dl>
      <p v-if="store.viewMode === 'demo'" class="mt-3 text-[11px] text-warning">示例片段，不是真实研究证据。</p>
    </section>
    <div v-else class="flex flex-col items-center gap-3 px-4 py-14 text-center"><BookOpen class="size-8 text-slate-300" /><p class="text-xs font-medium">结论旁边，就是它的依据</p><p class="text-[11px] leading-relaxed text-muted-foreground">点击回答中的引用，查看原文和该次使用的日期、版本。</p></div>
    <div class="mt-7 flex items-center gap-2 border-b pb-3"><Grid2X2 class="size-4 text-blue-600" /><h2 class="text-xs font-semibold">本次行情快照</h2></div>
    <div v-if="snapshot" class="mt-4 grid grid-cols-2 gap-2.5 text-xs"><div class="rounded-lg border p-3"><p class="text-[10px] text-muted-foreground">最近返回收盘价</p><p class="mt-2 font-mono text-lg">{{ latest?.close ?? '未提供' }}</p><p class="text-[10px] text-muted-foreground">{{ latest?.date }} · {{ units?.price || '单位未提供' }}</p></div><div class="rounded-lg border p-3"><p class="text-[10px] text-muted-foreground">实际行情截至</p><p class="mt-2 font-mono">{{ range?.end_date || '未提供' }}</p><p class="mt-1 text-[10px] text-muted-foreground">{{ snapshot.adjustment === 'none' ? '未复权' : snapshot.adjustment }} · {{ snapshot.count }}条</p></div><p class="col-span-2 break-all text-[10px] text-muted-foreground">来源：{{ snapshot.source_file }}<br />实际区间：{{ range?.start_date || '未提供' }} ～ {{ range?.end_date || '未提供' }}</p></div>
    <p v-else class="mt-4 rounded-lg border p-4 text-xs text-muted-foreground">本次未返回行情快照。均线、估值等未由工具计算的指标保持未知。</p>
    <p class="mt-3 flex gap-2 rounded-md bg-muted/60 p-3 text-[11px] leading-relaxed text-muted-foreground"><LockKeyhole class="mt-0.5 size-3.5 shrink-0" />{{ store.viewMode === 'history' ? '正在查看归档时的证据与数据，不读取最新原文。' : '确认归档保存本次原文、版本与行情输入，缺失值不由模型记忆补齐。' }}</p>
  </aside>
</template>
<style scoped>
.evidence-panel { min-width:0; border-left:1px solid hsl(var(--border)); background:hsl(var(--card)); padding:20px 16px; }
@media(max-width:1280px) and (min-width:1051px) { .evidence-panel { padding:18px 12px; } }
@media(max-width:1050px) { .evidence-panel { border-left:0; border-top:1px solid hsl(var(--border)); } }
</style>
