<script setup>
import { computed, ref } from 'vue'
import { api, ApiError } from '../lib/api.js'

const emit = defineEmits(['authenticated'])

const mode = ref('login')
const username = ref('')
const password = ref('')
const error = ref('')
const busy = ref(false)

const isLogin = computed(() => mode.value === 'login')

const messages = {
  invalid_credentials: '用户名或密码错误',
  username_taken: '用户名已被占用',
  invalid_argument: '用户名需 3 到 32 个字符，密码至少 8 个字符',
}

function switchMode() {
  mode.value = isLogin.value ? 'register' : 'login'
  error.value = ''
}

async function submit() {
  busy.value = true
  error.value = ''
  try {
    if (!isLogin.value) await api.register(username.value, password.value)
    emit('authenticated', await api.login(username.value, password.value))
  } catch (e) {
    error.value = (e instanceof ApiError && messages[e.code]) || '服务暂时不可用，请稍后再试'
  } finally {
    busy.value = false
  }
}
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
      <p v-if="error" class="error" role="alert">{{ error }}</p>
      <button class="primary" :disabled="busy">{{ isLogin ? '登录' : '注册并登录' }}</button>
      <button type="button" class="link" @click="switchMode">
        {{ isLogin ? '没有账号？去注册' : '已有账号？去登录' }}
      </button>
    </form>
  </main>
</template>
