<script setup lang="ts">
import { ref, type HTMLAttributes } from 'vue';
import { ChevronDown } from '@lucide/vue';
import { cn } from '@/shared/lib/cn';

defineOptions({ inheritAttrs: false });
const props = withDefaults(defineProps<{ class?: HTMLAttributes['class']; disabled?: boolean }>(), { disabled: false });
const modelValue = defineModel<string>();
const element = ref<HTMLSelectElement | null>(null);
function openPicker(event: MouseEvent) {
  if (props.disabled || event.target === element.value) return;
  element.value?.focus();
  try { element.value?.showPicker(); }
  catch { /* The native select remains available by keyboard and its text area. */ }
}
</script>

<template>
  <div data-slot="select" :data-disabled="disabled" :data-invalid="$attrs['aria-invalid']" :class="cn('ui-select flex h-8 w-full min-w-0 items-center justify-between gap-2 rounded-md border border-input bg-card px-2.5 text-xs text-foreground transition-colors duration-150 ease-out focus-within:border-ring focus-within:ring-1 focus-within:ring-ring data-[disabled=false]:hover:border-slate-400 data-[disabled=true]:cursor-not-allowed data-[disabled=true]:bg-muted data-[disabled=true]:opacity-60 data-[invalid=true]:border-destructive', props.class)" @click="openPicker">
    <select ref="element" v-model="modelValue" v-bind="$attrs" :disabled="disabled" class="h-full w-full min-w-0 flex-1 cursor-pointer appearance-none border-0 bg-transparent p-0 text-inherit outline-none disabled:cursor-not-allowed"><slot /></select>
    <ChevronDown class="pointer-events-none size-3.5 shrink-0 text-muted-foreground" aria-hidden="true" />
  </div>
</template>
