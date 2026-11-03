<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { api } from '../lib/api.js'
import { addRequest, providerName } from '../lib/providers.js'

const props = defineProps({
  roomId: { type: Number, required: true },
  me: { type: Object, required: true },
  members: { type: Array, required: true },
  // Bumped by the chat view when a request arrives over the socket.
  requestEvent: { type: Object, default: null },
})
const emit = defineEmits(['changed', 'notice', 'open-settings', 'close'])

const agents = ref([])
const requests = ref([])
const chosen = ref('')

const myRole = computed(() => props.members.find((m) => m.id === props.me.member_id)?.role ?? 'member')
const isOwner = computed(() => myRole.value === 'owner')
const canManage = computed(() => myRole.value === 'owner' || myRole.value === 'admin')
const inRoom = computed(() => new Set(props.members.filter((m) => m.type === 'ai').map((m) => m.slug)))
const addable = computed(() => agents.value.filter((a) => !inRoom.value.has(a.slug)))

const ROLE_NAMES = { owner: '群主', admin: '管理员' }

async function loadAgents() {
  agents.value = (await api.aiAgents()).agents
}

async function loadRequests() {
  requests.value = canManage.value ? (await api.aiRequests(props.roomId)).requests : []
}

async function addAi() {
  const agent = agents.value.find((a) => a.slug === chosen.value)
  if (!agent) return
  if (!agent.has_key) {
    emit('notice', `请先在设置里填写 ${providerName(agent.provider)} 的 API key`)
    emit('open-settings')
    return
  }
  try {
    const { status } = await api.requestAi(props.roomId, agent.slug)
    emit('notice', status === 'joined' ? `${agent.name} 已加入` : '已提交，等待群主或管理员审批')
    chosen.value = ''
    emit('changed')
  } catch (e) {
    emit('notice', e.code === 'api_key_required' ? `请先填写 ${providerName(e.provider)} 的 API key` : '操作失败')
  }
}

async function review(slug, approve) {
  try {
    await (approve ? api.approveAi : api.rejectAi)(props.roomId, slug)
  } catch {
    emit('notice', '这条申请已被处理')
  }
  await loadRequests()
  emit('changed')
}

async function act(action, member) {
  try {
    await action(props.roomId, member.slug ?? member.name)
    emit('changed')
  } catch (e) {
    emit('notice', e.code === 'forbidden' ? '只有群主或管理员能这样做' : '操作失败')
  }
}

watch(() => props.requestEvent, (frame) => {
  if (frame) requests.value = addRequest(requests.value, frame)
})
watch(() => [props.roomId, myRole.value], loadRequests)
watch(() => props.members, loadRequests)

onMounted(() => {
  loadAgents()
  loadRequests()
})

defineExpose({ loadAgents })
</script>

<template>
  <aside class="members-panel" aria-label="成员">
    <div class="panel-head">
      <h2>成员</h2>
      <button class="link" @click="emit('close')">关闭</button>
    </div>
    <ul class="member-list">
      <li v-for="member in members" :key="member.id">
        <span>{{ member.name }}</span>
        <span v-if="member.type === 'ai'" class="tag">AI</span>
        <span v-if="ROLE_NAMES[member.role]" class="tag">{{ ROLE_NAMES[member.role] }}</span>
        <span class="member-actions">
          <template v-if="isOwner && member.type === 'human' && member.id !== me.member_id">
            <button class="link" @click="act(api.transferOwner, member)">转让群主</button>
            <button v-if="member.role === 'admin'" class="link" @click="act(api.dismissAdmin, member)">取消管理员</button>
            <button v-else class="link" @click="act(api.appointAdmin, member)">设为管理员</button>
          </template>
          <button
            v-if="canManage && member.type === 'ai'"
            class="link danger"
            @click="act(api.removeAi, member)"
          >
            移出
          </button>
        </span>
      </li>
    </ul>

    <form v-if="addable.length" class="inline-form" @submit.prevent="addAi">
      <select v-model="chosen" aria-label="选择 AI">
        <option value="" disabled>邀请 AI</option>
        <option v-for="agent in addable" :key="agent.slug" :value="agent.slug">
          {{ agent.name }}{{ agent.has_key ? '' : `（需要 ${providerName(agent.provider)} key）` }}
        </option>
      </select>
      <button :disabled="!chosen">{{ canManage ? '邀请' : '申请' }}</button>
    </form>

    <section v-if="canManage && requests.length" class="requests">
      <h3>待审批（{{ requests.length }}）</h3>
      <div v-for="request in requests" :key="request.slug" class="request">
        <span>{{ agents.find((a) => a.slug === request.slug)?.name ?? request.slug }}</span>
        <span v-if="request.requested_by?.name" class="hint">{{ request.requested_by.name }} 申请</span>
        <button class="link" @click="review(request.slug, true)">批准</button>
        <button class="link danger" @click="review(request.slug, false)">拒绝</button>
      </div>
    </section>
  </aside>
</template>
