<script setup lang="ts">
import { ref, type HTMLAttributes, type InputHTMLAttributes } from 'vue';
import { CalendarDays } from '@lucide/vue';

import { cn } from '@/shared/lib/cn';

defineOptions({ inheritAttrs: false });

const props = withDefaults(
  defineProps<{
    class?: HTMLAttributes['class'];
    type?: InputHTMLAttributes['type'];
    disabled?: boolean;
  }>(),
  { type: 'text', disabled: false },
);

const modelValue = defineModel<string | number>();
const element = ref<HTMLInputElement | null>(null);

function openDatePicker() {
  if (props.disabled) return;
  element.value?.focus();
  try { element.value?.showPicker(); }
  catch { /* Keep keyboard entry available when the browser cannot open its picker. */ }
}

defineExpose({
  element,
  focus: (options?: FocusOptions) => element.value?.focus(options),
  blur: () => element.value?.blur(),
  select: () => element.value?.select(),
});
</script>

<template>
  <div
    data-slot="input"
    :data-disabled="disabled"
    :data-invalid="$attrs['aria-invalid']"
    :class="cn(
      'ui-input flex h-8 w-full min-w-0 items-center justify-between gap-2 rounded-md border border-input bg-card px-2.5 text-xs text-foreground transition-colors duration-150 ease-out focus-within:border-ring focus-within:ring-1 focus-within:ring-ring data-[disabled=false]:hover:border-slate-400 data-[disabled=true]:cursor-not-allowed data-[disabled=true]:bg-muted data-[disabled=true]:opacity-60 data-[invalid=true]:border-destructive data-[invalid=true]:focus-within:ring-destructive',
      props.class,
    )"
  >
    <input
      ref="element"
      v-model="modelValue"
      v-bind="$attrs"
      :type="type"
      :disabled="disabled"
      class="ui-input-control h-full w-full min-w-0 flex-1 border-0 bg-transparent p-0 text-inherit outline-none placeholder:text-muted-foreground disabled:cursor-not-allowed"
    >
    <span v-if="$slots.suffix" class="pointer-events-none flex shrink-0 items-center text-muted-foreground"><slot name="suffix" /></span>
    <button v-else-if="type === 'date'" type="button" aria-label="打开日历" :disabled="disabled" class="flex shrink-0 items-center rounded text-muted-foreground transition-colors duration-150 hover:text-blue-600 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed" @click="openDatePicker"><CalendarDays class="size-3.5" aria-hidden="true" /></button>
  </div>
</template>

<style scoped>
.ui-input-control[type='date']::-webkit-calendar-picker-indicator { display:none; }
.ui-input-control[type='date']::-webkit-date-and-time-value { text-align:left; }
</style>
