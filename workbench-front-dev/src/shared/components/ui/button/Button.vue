<script setup lang="ts">
import { Primitive, type PrimitiveProps } from 'reka-ui';
import type { HTMLAttributes } from 'vue';

import { cn } from '@/shared/lib/cn';

import { buttonVariants, type ButtonVariants } from '.';

interface Props extends PrimitiveProps {
  variant?: ButtonVariants['variant'];
  size?: ButtonVariants['size'];
  class?: HTMLAttributes['class'];
  disabled?: boolean;
  type?: 'button' | 'submit' | 'reset';
}

const props = withDefaults(defineProps<Props>(), {
  as: 'button',
  type: 'button',
  disabled: false,
});

function guardDisabledClick(event: MouseEvent): void {
  if (!props.disabled) return;
  event.preventDefault();
  event.stopImmediatePropagation();
}
</script>

<template>
  <Primitive
    data-slot="button"
    :as="as"
    :as-child="asChild"
    :type="as === 'button' && !asChild ? type : undefined"
    :disabled="disabled || undefined"
    :aria-disabled="disabled || undefined"
    :data-disabled="disabled || undefined"
    :class="cn(buttonVariants({ variant, size }), props.class)"
    @click="guardDisabledClick"
  >
    <slot />
  </Primitive>
</template>
