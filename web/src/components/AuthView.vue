<script setup>
import { computed, onBeforeUnmount, ref } from 'vue'
import { api } from '../lib/api.js'
import { authErrorText, countdown, waitSeconds, waitText } from '../lib/auth-errors.js'

const emit = defineEmits(['authenticated'])

const mode = ref('login')
const username = ref('')
const password = ref('')
const passwordConfirm = ref('')
const securityQuestion = ref('')
const securityAnswer = ref('')
const answer = ref('')
const question = ref('')
const error = ref('')
const busy = ref(false)
const waitLeft = ref(0)
let stopCountdown = () => {}

const isLogin = computed(() => mode.value === 'login')
const isRegister = computed(() => mode.value === 'register')
const isForgot = computed(() => mode.value === 'forgot')

function switchTo(next) {
  mode.value = next
  error.value = ''
  question.value = ''
  answer.value = ''
  password.value = ''
  passwordConfirm.value = ''
}

function startWait(seconds) {
  stopCountdown()
  stopCountdown = countdown(seconds, (left) => {
    waitLeft.value = left
    error.value = left > 0 ? waitText(left) : ''
  })
}

function passwordsMatch() {
  return password.value === passwordConfirm.value
}

async function fetchQuestion() {
  busy.value = true
  error.value = ''
  try {
    const data = await api.passwordRecoveryQuestion(username.value)
    question.value = data.question
    if (!data.question) error.value = '该用户名不存在或未设置安全问题'
  } catch (e) {
    error.value = authErrorText(e)
  } finally {
    busy.value = false
  }
}

async function submit() {
  busy.value = true
  error.value = ''
  try {
    if (isRegister.value) {
      if (!passwordsMatch()) {
        error.value = '两次输入的密码不一致'
        return
      }
      await api.register(username.value, password.value, {
        password_confirm: passwordConfirm.value,
        security_question: securityQuestion.value,
        security_answer: securityAnswer.value,
      })
      emit('authenticated', await api.login(username.value, password.value))
      return
    }
    if (isForgot.value) {
      if (!question.value) {
        await fetchQuestion()
        return
      }
      if (!passwordsMatch()) {
        error.value = '两次输入的密码不一致'
        return
      }
      await api.passwordRecoveryReset(username.value, answer.value, password.value, passwordConfirm.value)
      error.value = ''
      switchTo('login')
      error.value = '密码已重置，请用新密码登录'
      return
    }
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
      <h1>{{ isLogin ? '登录' : isRegister ? '注册' : '找回密码' }}</h1>
      <label>
        用户名
        <input v-model="username" autocomplete="username" required />
      </label>
      <label>
        {{ isForgot ? '新密码' : '密码' }}
        <input
          v-model="password"
          type="password"
          :autocomplete="isLogin ? 'current-password' : 'new-password'"
          required
        />
      </label>
      <label v-if="isRegister || isForgot">
        重新输入密码
        <input v-model="passwordConfirm" type="password" autocomplete="new-password" required />
      </label>
      <template v-if="isRegister">
        <p class="hint">密码至少 10 个字符，不能是常见密码，也不能与用户名相同。</p>
        <label>
          安全问题
          <input v-model="securityQuestion" autocomplete="off" placeholder="例如：你的第一只宠物叫什么？" required />
        </label>
        <label>
          安全答案
          <input v-model="securityAnswer" autocomplete="off" required />
        </label>
        <p class="hint">安全问题用于忘记密码时找回，答案会加密保存，不会明文展示。</p>
      </template>
      <template v-if="isForgot">
        <p v-if="!question" class="hint">输入用户名后获取你注册时设置的安全问题。</p>
        <div v-else class="recovery-question">
          <p class="hint">{{ question }}</p>
          <label>
            答案
            <input v-model="answer" autocomplete="off" required />
          </label>
        </div>
      </template>
      <p v-if="error" class="error" role="alert">{{ error }}</p>
      <button class="primary" :disabled="busy || waitLeft > 0">
        {{ isForgot ? (question ? '重置密码' : '获取安全问题') : isLogin ? '登录' : '注册并登录' }}
      </button>
      <button v-if="isLogin" type="button" class="link" @click="switchTo('forgot')">忘记密码？</button>
      <button type="button" class="link" @click="switchTo(isLogin ? 'register' : 'login')">
        {{ isForgot ? '返回登录' : isLogin ? '没有账号？去注册' : '已有账号？去登录' }}
      </button>
    </form>
  </main>
</template>