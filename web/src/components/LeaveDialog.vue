<script setup>
import { onMounted, ref } from 'vue'

defineProps({
  room: { type: Object, required: true },
  isLastHuman: { type: Boolean, required: true },
  busy: { type: Boolean, default: false },
})
defineEmits(['export', 'confirm', 'cancel'])

const dialog = ref(null)
onMounted(() => dialog.value.showModal())
</script>

<template>
  <dialog ref="dialog" class="dialog" @cancel.prevent="$emit('cancel')">
    <h2>退出“{{ room.name }}”</h2>
    <p>退出后你将无法再查看这个房间。需要保留聊天记录的话，请先导出到本地。</p>
    <p v-if="isLastHuman" class="error">
      你是这个房间最后一名成员。退出后房间和全部聊天记录都会被删除，且不可恢复。
    </p>
    <div class="dialog-actions">
      <button @click="$emit('cancel')">取消</button>
      <button :disabled="busy" @click="$emit('export')">导出聊天记录</button>
      <button class="danger" :disabled="busy" @click="$emit('confirm')">
        {{ isLastHuman ? '删除并退出' : '退出房间' }}
      </button>
    </div>
  </dialog>
</template>
