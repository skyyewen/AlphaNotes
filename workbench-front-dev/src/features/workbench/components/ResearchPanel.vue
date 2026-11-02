<script setup lang="ts">
import { computed } from 'vue';
import { ArrowRight, Check, CircleHelp, Copy, FileCheck2, FlaskConical, LoaderCircle, MessageSquareText, Send, Square, Workflow } from '@lucide/vue';
import { useResearchStore } from '@/features/research/store';
import MarkdownContent from '@/shared/components/MarkdownContent.vue';
import { Badge } from '@/shared/components/ui/badge';
import { Button } from '@/shared/components/ui/button';
import { Textarea } from '@/shared/components/ui/textarea';

const props = defineProps<{ apiEnabled: boolean; notice: string }>();
defineEmits<{ submit: []; stop: []; confirm: []; demo: []; copy: [] }>();
const store = useResearchStore();
const labels = { idle: '待研究', running: '研究中', completed: '已完成 · 未生成归档稿', draft_ready: '待人工确认', saving: '正在保存', saved: '已保存', error: '研究未完成' } as const;
const submitDisabled = computed(() => !props.apiEnabled || store.isRunning || !store.question.trim());
const prompts = ['对照我的规则，哪些条件还缺证据？', '区分这份资料中的事实、预期和推断', '本次研究的支持证据与反证分别是什么？'];
</script>

<template>
  <main class="research-panel" aria-label="研究工作区">
    <div class="research-content">
      <div class="mb-6 flex items-center justify-between gap-3"><div><h1 class="text-sm font-semibold">{{ store.viewMode === 'history' ? '历史研究快照' : '研究工作区' }}</h1><p class="mt-1 text-[11px] text-muted-foreground">把资料、规则和判断依据放在同一处。</p></div><Badge :variant="store.viewMode === 'demo' ? 'warning' : 'outline'">{{ store.viewMode === 'demo' ? '界面示例 · 未执行工具' : store.viewMode === 'history' ? '已归档 · 只读回放' : labels[store.status] }}</Badge></div>

      <template v-if="!store.answer && !store.isRunning && !store.submittedQuestion">
        <div class="welcome-panel">
          <div class="mb-5 flex size-12 items-center justify-center rounded-xl border bg-card text-blue-600"><MessageSquareText class="size-6" aria-hidden="true" /></div>
          <h2 class="text-lg font-semibold tracking-tight">从一个明确的研究问题开始</h2>
          <p class="mt-3 max-w-sm text-center text-xs leading-7 text-muted-foreground">填写股票代码和问题即可开始；资料日期、行情日期与策略分别可选。回答会保留引用，并明确无法判断的部分。</p>
          <div class="mt-7 w-full max-w-md space-y-2"><button v-for="prompt in prompts" :key="prompt" type="button" class="flex w-full items-center justify-between gap-3 rounded-lg border bg-card px-4 py-3 text-left text-xs text-slate-600 transition-colors hover:border-blue-200 hover:text-blue-700" @click="store.question = prompt"><span>{{ prompt }}</span><ArrowRight class="size-3.5 shrink-0 text-slate-400" aria-hidden="true" /></button></div>
          <Button variant="ghost" class="mt-5 text-xs text-blue-600" @click="$emit('demo')"><FlaskConical class="mr-1.5 size-3.5" aria-hidden="true" />查看界面示例</Button>
        </div>
      </template>

      <template v-else>
        <div class="mb-5 ml-auto max-w-xl rounded-xl rounded-tr-sm bg-primary px-4 py-3 text-xs leading-7 text-primary-foreground">{{ store.submittedQuestion }}</div>
        <div class="mb-4 rounded-lg border bg-card px-3.5 py-2.5 text-[11px] text-muted-foreground">
          <div class="flex items-center gap-2"><Workflow class="size-4 shrink-0 text-blue-600" aria-hidden="true" /><span v-if="store.viewMode === 'demo'">示例流程：查数 → 检索原文 → 对照规则 → 汇总未知项</span><span v-else-if="!store.steps.length">正在研究，完成后显示实际工具记录…</span><span v-else>{{ store.steps.map(step => `${step.label}${step.duration_ms === undefined ? '' : ` (${step.duration_ms}ms)`}`).join(' → ') }}</span></div>
        </div>
        <section class="rounded-xl border bg-card p-5" aria-label="研究报告">
          <div class="mb-4 flex flex-wrap items-center justify-between gap-3 border-b pb-4"><div class="flex items-center gap-2.5"><div class="flex size-8 items-center justify-center rounded-lg bg-blue-50 text-blue-600"><FileCheck2 class="size-4" aria-hidden="true" /></div><div><h2 class="text-sm font-semibold">研究报告与规则核对</h2><p class="mt-0.5 text-[11px] text-muted-foreground">{{ store.viewMode === 'demo' ? '示例资料 · 无真实行情与财务数据' : '以工具证据和冻结上下文为依据' }}</p></div></div><Button variant="outline" size="sm" :disabled="!store.answer" @click="$emit('copy')"><Copy class="mr-1.5 size-3" aria-hidden="true" />复制</Button></div>
          <div v-if="store.startedAt" class="mb-4 space-y-1 text-[10px] text-muted-foreground"><p>研究开始（UTC）：{{ store.startedAt }}</p><p v-if="store.finishedAt">研究结束（UTC）：{{ store.finishedAt }}</p><p v-if="store.confirmedAt">确认归档（UTC）：{{ store.confirmedAt }}</p></div>
          <div v-if="store.isRunning && !store.answer" class="flex items-center gap-2 py-7 text-xs text-muted-foreground"><LoaderCircle class="size-4 animate-spin" aria-hidden="true" />正在调用工具与核对证据，完成后一次显示报告与引用…</div>
          <MarkdownContent v-else :content="store.answer" :citation-ids="Object.keys(store.citations)" @select-citation="store.selectCitation" />
          <div v-if="store.viewMode === 'demo'" class="mt-5 overflow-hidden rounded-lg border"><div class="grid grid-cols-[1fr_auto] gap-3 border-b bg-muted/60 px-3 py-2 text-[11px] font-medium"><span>核对项目 · 示例</span><span>当前状态</span></div><div v-for="item in ['真实规则版本', '行情与财务快照', '资料中的经营变化']" :key="item" class="flex items-center justify-between gap-3 border-b px-3 py-2.5 text-xs last:border-0"><span>{{ item }}</span><span class="flex items-center gap-1 text-warning"><CircleHelp class="size-3" aria-hidden="true" />无法判断</span></div></div>
          <div class="mt-5 flex flex-wrap items-center justify-between gap-3 border-t pt-4"><span class="break-all text-[11px] text-muted-foreground">{{ store.viewMode === 'demo' ? '示例不会保存为真实研究' : store.status === 'saved' ? `记录 ${store.recordId}` : '后端已保存完整草稿后才能确认归档' }}</span><Button v-if="store.viewMode !== 'history'" :disabled="!apiEnabled || !store.canConfirm" @click="$emit('confirm')"><Check class="mr-1.5 size-3.5" aria-hidden="true" />{{ store.status === 'saving' ? '保存中…' : store.status === 'saved' ? '已保存' : '确认保存复盘' }}</Button></div>
        </section>
      </template>
      <div v-if="store.errorMessage" class="mt-4 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-xs leading-relaxed text-destructive" role="alert">{{ store.errorMessage }}<span v-if="store.errorCode" class="ml-2 font-mono text-[10px]">{{ store.errorCode }}</span></div>
      <p v-if="notice" class="mt-3 text-xs text-blue-700" role="status">{{ notice }}</p>
    </div>

    <div v-if="store.viewMode === 'history'" class="composer text-xs text-muted-foreground">历史记录只读。需要继续研究时，点击顶部“新建研究”，将另起一次请求。</div>
    <form v-else class="composer" @submit.prevent="$emit('submit')">
      <label for="research-question" class="mb-2 block text-[11px] font-medium text-muted-foreground">研究问题</label>
      <div class="rounded-lg border bg-background p-2.5 focus-within:border-blue-400"><Textarea id="research-question" v-model="store.question" class="min-h-[68px] resize-y border-0 bg-transparent p-0 text-xs shadow-none focus-visible:ring-0" placeholder="提出研究问题，例如：哪些结论已有证据，哪些还需要继续核实？" :disabled="store.isRunning" @keydown.ctrl.enter.prevent="!submitDisabled && $emit('submit')" /><div class="mt-2 flex flex-wrap items-center justify-between gap-2"><span class="text-[10px] text-muted-foreground">{{ apiEnabled ? '同步研究 · Ctrl + Enter 发送 · 停止等待不代表后台取消' : '真实API未开启 · 可先查看示例' }}</span><Button v-if="store.isRunning" variant="outline" size="sm" @click="$emit('stop')"><Square class="mr-1.5 size-3" aria-hidden="true" />停止等待</Button><Button v-else type="submit" size="sm" :disabled="submitDisabled"><Send class="mr-1.5 size-3.5" aria-hidden="true" />开始研究</Button></div></div>
    </form>
  </main>
</template>

<style scoped>
.research-panel { display:flex; flex-direction:column; min-width:0; min-height:0; background:hsl(var(--background)); }
.research-content { flex:1; min-height:0; overflow-y:auto; padding:24px; }
.welcome-panel { display:flex; flex-direction:column; align-items:center; padding:60px 0 45px; }
.composer { flex-shrink:0; padding:14px 20px 18px; border-top:1px solid hsl(var(--border)); background:hsl(var(--card)); }
@media(max-width:1050px) { .research-content { overflow-y:visible; } }
@media(max-width:600px) { .research-content { padding:18px 12px; } .welcome-panel { padding:30px 0; } .composer { padding:12px; } }
</style>
