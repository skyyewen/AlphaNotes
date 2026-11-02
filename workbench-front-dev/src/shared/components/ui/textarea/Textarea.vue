<script setup lang="ts">
import { ref, type HTMLAttributes } from 'vue';

import { cn } from '@/shared/lib/cn';

defineOptions({ inheritAttrs: false });

const props = withDefaults(
  defineProps<{
    class?: HTMLAttributes['class'];
    disabled?: boolean;
  }>(),
  { disabled: false },
);

const modelValue = defineModel<string>();
const element = ref<HTMLTextAreaElement | null>(null);

defineExpose({
  element,
  focus: (options?: FocusOptions) => element.value?.focus(options),
  blur: () => element.value?.blur(),
  select: () => element.value?.select(),
});
</script>

<template>
  <textarea
    ref="element"
    v-model="modelValue"
    v-bind="$attrs"
    data-slot="textarea"
    :disabled="disabled"
    :class="cn(
      'ui-textarea flex min-h-20 w-full min-w-0 rounded-md border border-input bg-card px-2.5 py-2 text-xs leading-relaxed text-foreground transition-colors placeholder:text-muted-foreground focus-visible:border-ring focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:cursor-not-allowed disabled:bg-muted disabled:opacity-60 aria-invalid:border-destructive aria-invalid:focus-visible:ring-destructive',
      props.class,
    )"
  />
</template>
