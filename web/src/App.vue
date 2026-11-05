<script setup>
import { onMounted, ref, watch } from 'vue'
import { api } from './lib/api.js'
import { keepSessionAlive } from './lib/session-refresh.js'
import { authErrorText } from './lib/auth-errors.js'
import AuthView from './components/AuthView.vue'
import ChatView from './components/ChatView.vue'

const user = ref(null)
const ready = ref(false)
const logoutError = ref('')
const loggingOut = ref(false)

// While logged in, a trusted device keeps its session from expiring.
let stopRefresh = () => {}
watch(user, (current) => {
  stopRefresh()
  stopRefresh = current ? keepSessionAlive(api.refresh) : () => {}
})

onMounted(async () => {
  try {
    user.value = await api.me()
  } catch {
    user.value = null
  }
  ready.value = true
})

async function logout() {
  if (loggingOut.value) return
  loggingOut.value = true
  logoutError.value = ''
  try {
    await api.logout()
    user.value = null
  } catch (error) {
    logoutError.value = authErrorText(error)
  } finally {
    loggingOut.value = false
  }
}
</script>

<template>
  <template v-if="ready">
    <ChatView v-if="user" :key="user.id" :user="user" :logout-error="logoutError" :logging-out="loggingOut" @logout="logout" @session-ended="user = null" />
    <AuthView v-else @authenticated="user = $event" />
  </template>
</template>
