<script setup>
import { nextTick, onMounted, ref } from 'vue'

defineProps({
  title: { type: String, required: true },
  wide: { type: Boolean, default: false },
})
const emit = defineEmits(['close'])

const dialog = ref(null)
onMounted(async () => {
  dialog.value.showModal?.()
  await nextTick()
  dialog.value.querySelector('input, textarea, select')?.focus()
})
</script>

<template>
  <dialog ref="dialog" class="dialog" :class="{ wide }" :aria-label="title" @cancel.prevent="emit('close')">
    <h2>{{ title }}</h2>
    <slot />
  </dialog>
</template>
