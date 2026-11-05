<script setup>
import { onMounted, reactive, ref } from 'vue'
import { api } from '../lib/api.js'
import { PROVIDERS, keyErrorText } from '../lib/providers.js'

const emit = defineEmits(['close', 'changed'])

const saved = ref({}) // provider -> { last4, updated_at }
const drafts = reactive({})
const busy = reactive({})
const errors = reactive({})

async function load() {
  const { keys } = await api.keys()
  saved.value = Object.fromEntries(keys.map((k) => [k.provider, k]))
}

async function save(provider) {
  const key = (drafts[provider] ?? '').trim()
  if (!key) return
  busy[provider] = true
  errors[provider] = ''
  try {
    await api.storeKey(provider, key)
    drafts[provider] = ''
    await load()
    emit('changed')
  } catch (e) {
    errors[provider] = keyErrorText(e)
  } finally {
    busy[provider] = false
  }
}

async function remove(provider) {
  busy[provider] = true
  errors[provider] = ''
  try {
    await api.deleteKey(provider)
    await load()
    emit('changed')
  } catch (error) {
    errors[provider] = keyErrorText(error)
  } finally {
    busy[provider] = false
  }
}

function formatDate(ms) {
  return new Date(ms).toLocaleDateString('zh-CN')
}

onMounted(load)
</script>

<template>
  <div class="overlay" @click.self="emit('close')">
    <section class="dialog" role="dialog" aria-labelledby="settings-title">
      <h2 id="settings-title">模型 API key</h2>
      <p class="hint">AI 回复你的消息时，使用你自己的 key 付费。key 加密保存，之后只显示末 4 位。</p>
      <div v-for="provider in PROVIDERS" :key="provider.id" class="key-row">
        <div class="key-head">
          <strong>{{ provider.name }}</strong>
          <span v-if="saved[provider.id]" class="hint">
            已保存 …{{ saved[provider.id].last4 }}（{{ formatDate(saved[provider.id].updated_at) }}）
          </span>
          <span v-else class="hint">未填写</span>
        </div>
        <form class="inline-form" @submit.prevent="save(provider.id)">
          <input
            v-model="drafts[provider.id]"
            type="password"
            autocomplete="off"
            :placeholder="saved[provider.id] ? '输入新的 key 以替换' : '粘贴 API key'"
            :aria-label="`${provider.name} API key`"
          />
          <button :disabled="busy[provider.id]">{{ busy[provider.id] ? '验证中' : '保存' }}</button>
          <button
            v-if="saved[provider.id]"
            type="button"
            class="danger"
            :disabled="busy[provider.id]"
            @click="remove(provider.id)"
          >
            删除
          </button>
        </form>
        <p v-if="errors[provider.id]" class="error">{{ errors[provider.id] }}</p>
      </div>
      <div class="dialog-actions">
        <button class="primary" @click="emit('close')">完成</button>
      </div>
    </section>
  </div>
</template>
