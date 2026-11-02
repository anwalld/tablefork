<script setup>
import { computed, onBeforeUnmount, ref } from 'vue'
import { api } from '../lib/api.js'
import { authErrorText, countdown, waitSeconds, waitText } from '../lib/auth-errors.js'

const emit = defineEmits(['authenticated'])

const mode = ref('login')
const username = ref('')
const password = ref('')
const error = ref('')
const busy = ref(false)
// Seconds the server asked us to wait before the next attempt.
const waitLeft = ref(0)
let stopCountdown = () => {}

const isLogin = computed(() => mode.value === 'login')

function switchMode() {
  mode.value = isLogin.value ? 'register' : 'login'
  error.value = ''
}

function startWait(seconds) {
  stopCountdown()
  stopCountdown = countdown(seconds, (left) => {
    waitLeft.value = left
    error.value = left > 0 ? waitText(left) : ''
  })
}

async function submit() {
  busy.value = true
  error.value = ''
  try {
    if (!isLogin.value) await api.register(username.value, password.value)
    emit('authenticated', await api.login(username.value, password.value))
  } catch (e) {
    const seconds = waitSeconds(e)
    if (seconds > 0) startWait(seconds)
    else error.value = authErrorText(e)
  } finally {
    busy.value = false
  }
}

onBeforeUnmount(() => stopCountdown())
</script>

<template>
  <main class="auth">
    <form class="auth-card" @submit.prevent="submit">
      <h1>{{ isLogin ? '登录' : '注册' }}</h1>
      <label>
        用户名
        <input v-model="username" autocomplete="username" required />
      </label>
      <label>
        密码
        <input
          v-model="password"
          type="password"
          :autocomplete="isLogin ? 'current-password' : 'new-password'"
          required
        />
      </label>
      <p v-if="!isLogin" class="hint">密码至少 10 个字符，不能是常见密码，也不能与用户名相同。</p>
      <p v-if="error" class="error" role="alert">{{ error }}</p>
      <button class="primary" :disabled="busy || waitLeft > 0">
        {{ isLogin ? '登录' : '注册并登录' }}
      </button>
      <button type="button" class="link" @click="switchMode">
        {{ isLogin ? '没有账号？去注册' : '已有账号？去登录' }}
      </button>
    </form>
  </main>
</template>
