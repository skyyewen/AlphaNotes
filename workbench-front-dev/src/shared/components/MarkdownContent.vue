<script setup lang="ts">
import { computed, h, type VNodeChild } from 'vue';
import MarkdownIt from 'markdown-it';

const props = withDefaults(defineProps<{ content: string; citationIds?: string[] }>(), { citationIds: () => [] });
const emit = defineEmits<{ selectCitation: [id: string] }>();
const markdown = new MarkdownIt({ html: false, breaks: true }).disable('image');
type Token = ReturnType<typeof markdown.parse>[number];

function renderTokens(tokens: Token[], insideLink = false): VNodeChild[] {
  const output: VNodeChild[] = [];
  const stack = [output];
  let linkDepth = insideLink ? 1 : 0;
  for (const token of tokens) {
    if (token.hidden) continue;
    const children = stack[stack.length - 1]!;
    if (token.nesting === 1) {
      const nested: VNodeChild[] = [];
      const attrs = Object.fromEntries(token.attrs || []);
      if (token.tag === 'a') {
        attrs.target = '_blank'; attrs.rel = 'noopener noreferrer'; linkDepth += 1;
      }
      const node = h(token.tag, attrs, nested);
      children.push(token.tag === 'table' ? h('div', { class: 'markdown-table' }, [node]) : node);
      stack.push(nested);
    } else if (token.nesting === -1) {
      stack.pop();
      if (token.tag === 'a') linkDepth -= 1;
    } else if (token.type === 'inline') {
      children.push(...renderTokens(token.children || [], linkDepth > 0));
    } else if (token.type === 'text') {
      // Only plain text becomes a citation: code and link labels keep their original content.
      const parts = linkDepth ? [token.content] : token.content.split(/(\[R[1-9]\d*-[1-9]\d*\])/g);
      for (const part of parts) {
        const id = part.slice(1, -1);
        children.push(!linkDepth && /^\[R[1-9]\d*-[1-9]\d*\]$/.test(part) && props.citationIds.includes(id)
          ? h('button', { type: 'button', class: 'markdown-citation', 'aria-label': `查看引用 ${id}`, onClick: () => emit('selectCitation', id) }, part)
          : part);
      }
    } else if (token.type === 'code_inline') {
      children.push(h('code', token.content));
    } else if (token.type === 'fence' || token.type === 'code_block') {
      children.push(h('pre', [h('code', token.content)]));
    } else if (token.type === 'softbreak' || token.type === 'hardbreak') {
      children.push(h('br'));
    } else if (token.type === 'hr') {
      children.push(h('hr'));
    }
  }
  return output;
}
const nodes = computed(() => renderTokens(markdown.parse(props.content, {})));
const MarkdownBody = () => nodes.value;
</script>

<template>
  <div class="markdown-content"><MarkdownBody /></div>
</template>

<style scoped>
.markdown-content { min-width:0; overflow-wrap:anywhere; color:hsl(var(--foreground)); font-size:12px; line-height:1.9; }
.markdown-content :deep(> :first-child) { margin-top:0; }
.markdown-content :deep(> :last-child) { margin-bottom:0; }
.markdown-content :deep(p), .markdown-content :deep(ul), .markdown-content :deep(ol), .markdown-content :deep(blockquote), .markdown-content :deep(pre), .markdown-content :deep(.markdown-table) { margin:0.85em 0; }
.markdown-content :deep(h1), .markdown-content :deep(h2), .markdown-content :deep(h3), .markdown-content :deep(h4), .markdown-content :deep(h5), .markdown-content :deep(h6) { margin:1.4em 0 0.6em; font-weight:600; line-height:1.5; }
.markdown-content :deep(h1) { font-size:18px; }
.markdown-content :deep(h2) { font-size:16px; }
.markdown-content :deep(h3) { font-size:14px; }
.markdown-content :deep(strong) { font-weight:600; }
.markdown-content :deep(ul) { list-style:disc; padding-left:1.6em; }
.markdown-content :deep(ol) { list-style:decimal; padding-left:1.6em; }
.markdown-content :deep(li > p) { margin:0.3em 0; }
.markdown-content :deep(blockquote) { border-left:3px solid hsl(var(--border)); padding-left:12px; color:hsl(var(--muted-foreground)); }
.markdown-content :deep(a) { color:hsl(var(--accent-foreground)); text-decoration:underline; text-underline-offset:3px; }
.markdown-content :deep(code) { border-radius:4px; background:hsl(var(--muted)); padding:2px 4px; font-size:11px; }
.markdown-content :deep(pre) { overflow-x:auto; border-radius:6px; background:hsl(var(--muted)); padding:12px; white-space:pre; overflow-wrap:normal; }
.markdown-content :deep(pre code) { padding:0; background:transparent; }
.markdown-content :deep(.markdown-table) { max-width:100%; overflow-x:auto; }
.markdown-content :deep(table) { width:100%; border-collapse:collapse; }
.markdown-content :deep(th), .markdown-content :deep(td) { min-width:90px; border:1px solid hsl(var(--border)); padding:7px 10px; text-align:left; }
.markdown-content :deep(th) { background:hsl(var(--muted)); font-weight:600; }
.markdown-content :deep(hr) { margin:1.2em 0; border-top:1px solid hsl(var(--border)); }
.markdown-content :deep(.markdown-citation) { display:inline-flex; margin:0 2px; border-radius:4px; background:hsl(var(--accent)); padding:0 6px; color:hsl(var(--accent-foreground)); font-family:Consolas,monospace; font-size:11px; font-weight:500; }
.markdown-content :deep(.markdown-citation:hover) { filter:brightness(0.95); }
.markdown-content :deep(.markdown-citation:focus-visible) { outline:2px solid hsl(var(--ring)); outline-offset:2px; }
</style>
