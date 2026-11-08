<script setup>
import { computed, ref } from 'vue'
import { PERSONA_LIMIT, PERSONA_TEMPLATE, byteLength, renderMarkdown } from '../lib/markdown.js'

const text = defineModel({ type: String, default: '' })
defineProps({ label: { type: String, default: '人设' } })

const tab = ref('edit')
const fileError = ref('')
const used = computed(() => byteLength(text.value))
const tooLong = computed(() => used.value > PERSONA_LIMIT)
const preview = computed(() => renderMarkdown(text.value))

async function importFile(event) {
  const file = event.target.files?.[0]
  event.target.value = ''
  if (!file) return
  fileError.value = ''
  if (file.size > PERSONA_LIMIT) {
    fileError.value = '文件超过 16KB'
    return
  }
  text.value = await file.text()
  tab.value = 'edit'
}

function useTemplate() {
  if (!text.value.trim() || window.confirm('用模板替换当前内容？')) text.value = PERSONA_TEMPLATE
}
</script>

<template>
  <div class="persona-editor">
    <div class="persona-tools" role="tablist">
      <button type="button" role="tab" :class="{ active: tab === 'edit' }" :aria-selected="tab === 'edit'" @click="tab = 'edit'">编辑</button>
      <button type="button" role="tab" :class="{ active: tab === 'preview' }" :aria-selected="tab === 'preview'" @click="tab = 'preview'">预览</button>
      <span class="spacer" />
      <button type="button" class="link" @click="useTemplate">模板</button>
      <label class="link file-button">导入 .md<input type="file" accept=".md,.markdown,.txt,text/markdown,text/plain" aria-label="导入人设文件" @change="importFile" /></label>
    </div>
    <textarea v-if="tab === 'edit'" v-model="text" class="persona-text" :aria-label="label"
      placeholder="支持 Markdown，例如用标题和列表写性格、说话方式" />
    <div v-else-if="!text.trim()" class="persona-preview" aria-label="人设预览"><p class="hint">还没有内容</p></div>
    <!-- Sanitized by DOMPurify in renderMarkdown. -->
    <div v-else class="persona-preview" aria-label="人设预览" v-html="preview" />
    <p class="hint" :class="{ error: tooLong }">{{ used }} / {{ PERSONA_LIMIT }} 字节<span v-if="fileError"> · {{ fileError }}</span></p>
  </div>
</template>
