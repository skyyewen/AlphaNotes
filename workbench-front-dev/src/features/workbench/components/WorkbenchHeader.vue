<script setup lang="ts">
import { ChartNoAxesCombined, RotateCcw, X } from '@lucide/vue';
import { useResearchStore } from '@/features/research/store';
import { Button } from '@/shared/components/ui/button';
import { Input } from '@/shared/components/ui/input';
import { Badge } from '@/shared/components/ui/badge';
defineProps<{ apiEnabled: boolean }>();
defineEmits<{ reset: []; demo: [] }>();
const store = useResearchStore();
</script>
<template>
  <header class="workbench-header">
    <div class="flex shrink-0 items-center gap-2.5"><div class="flex size-8 items-center justify-center rounded-lg bg-primary text-primary-foreground"><ChartNoAxesCombined class="size-5" aria-hidden="true" /></div><span class="text-base font-semibold tracking-tight">AlphaNotes</span></div>
    <div class="context-fields" aria-label="研究上下文">
      <label class="context-field"><span>研究标的</span><Input :model-value="store.context.symbol" placeholder="601318.SH" :disabled="store.isRunning || store.viewMode === 'history'" aria-label="研究标的" @update:model-value="value => store.updateContext({ symbol: String(value) })" /></label>
      <label class="context-field"><span>资料截止日 · 可空</span><Input type="date" :model-value="store.context.materials_cutoff_date" :disabled="store.isRunning || store.viewMode === 'history'" aria-label="资料截止日" @update:model-value="value => store.updateContext({ materials_cutoff_date: String(value) })" /></label>
      <label class="context-field"><span>行情截止日 · 可空</span><Input type="date" :model-value="store.context.market_cutoff_date" :disabled="store.isRunning || store.viewMode === 'history'" aria-label="行情截止日" @update:model-value="value => store.updateContext({ market_cutoff_date: String(value) })" /></label>
      <div class="context-field min-w-0"><span>对照策略 · 可选</span><div class="flex min-h-8 items-center justify-between gap-2 rounded-md border border-input bg-card px-2.5"><span class="min-w-0 flex-1 truncate text-xs" :title="`${store.context.strategy_id} ${store.context.strategy_version}`">{{ store.context.strategy_id ? `${store.context.strategy_id} / ${store.context.strategy_version}` : '从左侧策略 Wiki 选择' }}</span><button v-if="store.context.strategy_id && !store.isRunning && store.viewMode !== 'history'" aria-label="清除对照策略" class="shrink-0 rounded transition-colors duration-150 hover:bg-blue-50 hover:text-blue-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" @click="store.updateContext({ strategy_id: '', strategy_version: '' })"><X class="size-3.5" /></button></div></div>
    </div>
    <div class="header-actions"><Badge :variant="store.viewMode === 'demo' ? 'warning' : 'outline'">{{ store.viewMode === 'demo' ? '示例预览' : store.viewMode === 'history' ? '历史只读' : apiEnabled ? '本地研究' : 'API 未开启' }}</Badge><Button variant="ghost" size="icon" aria-label="新建研究" @click="$emit('reset')"><RotateCcw class="size-4" aria-hidden="true" /></Button></div>
  </header>
</template>
<style scoped>
.workbench-header { display:flex; align-items:center; gap:14px; overflow-x:auto; padding:14px 20px; background:hsl(var(--card)); border-bottom:1px solid hsl(var(--border)); }
.context-fields { display:flex; align-items:center; flex-shrink:0; gap:12px; }
.context-field { display:flex; align-items:center; flex-shrink:0; gap:8px; font-size:10px; color:hsl(var(--muted-foreground)); }
.context-field > span:first-child { flex-shrink:0; white-space:nowrap; }
.context-field :deep(.ui-input) { width:140px; height:32px; font-size:12px; }
.context-field:last-child > div { width:220px; }
.header-actions { display:flex; align-items:center; flex-shrink:0; gap:8px; height:32px; margin-left:auto; }
@media(max-width:650px) { .workbench-header { padding:12px; } }
</style>
