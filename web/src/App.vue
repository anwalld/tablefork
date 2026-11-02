<script setup>
import { onMounted, ref } from 'vue'
import { api } from './lib/api.js'
import AuthView from './components/AuthView.vue'
import ChatView from './components/ChatView.vue'

const user = ref(null)
const ready = ref(false)

onMounted(async () => {
  try {
    user.value = await api.me()
  } catch {
    user.value = null
  }
  ready.value = true
})

async function logout() {
  await api.logout().catch(() => {})
  user.value = null
}
</script>

<template>
  <template v-if="ready">
    <ChatView v-if="user" :key="user.id" :user="user" @logout="logout" />
    <AuthView v-else @authenticated="user = $event" />
  </template>
</template>
