<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { api } from '../lib/api.js'
import { addRequest, providerName } from '../lib/providers.js'
import { requestErrorText } from '../lib/request-errors.js'
import AppDialog from './AppDialog.vue'
import PersonaEditor from './PersonaEditor.vue'

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
const showMyAi = ref(false)
const models = ref([])
const model = ref('claude')
const roleName = ref('')
const rolePersona = ref('')
const creating = ref(false)
const roleBusy = ref(false)
const editing = ref(null)
const editText = ref('')
const roleEditing = ref(null)
const roleText = ref('')
const inviting = ref(null)
const invitationPersona = ref('')
// Keys of the actions whose requests are still running, so their buttons
// show progress instead of looking unresponsive.
const busy = ref(new Set())

async function run(key, action) {
  if (busy.value.has(key)) return
  busy.value = new Set(busy.value).add(key)
  try { await action() }
  finally {
    const next = new Set(busy.value)
    next.delete(key)
    busy.value = next
  }
}
const isBusy = (key) => busy.value.has(key)

function toggleMyAi() {
  showMyAi.value = !showMyAi.value
}
async function openCreate() {
  roleName.value = ''
  rolePersona.value = ''
  creating.value = true
  try {
    models.value = (await api.aiModels()).models
    if (!models.value.some((item) => item.model_slug === model.value)) model.value = models.value[0]?.model_slug ?? ''
  } catch (e) { emit('notice', requestErrorText(e, '模型目录加载失败')) }
}
async function createRole() {
  if (roleBusy.value || !roleName.value.trim()) return
  roleBusy.value = true
  try {
    await api.createAiRole(model.value, roleName.value.trim(), rolePersona.value)
    creating.value = false
    roleName.value = ''
    rolePersona.value = ''
    showMyAi.value = true
    await loadAgents()
    emit('notice', '私人AI已创建，可以邀请进房间了')
  } catch (e) { emit('notice', requestErrorText(e, '创建失败')) }
  finally { roleBusy.value = false }
}
function editRolePersona(agent) {
  roleEditing.value = agent
  roleText.value = agent.persona ?? ''
}
async function saveRolePersona() {
  const target = roleEditing.value
  if (!target || roleBusy.value) return
  roleBusy.value = true
  try {
    await api.editRolePersona(target.id, roleText.value)
    roleEditing.value = null
    await loadAgents()
    emit('notice', '默认人设已保存，之后邀请时自动填入')
  } catch (e) { emit('notice', requestErrorText(e, '人设保存失败')) }
  finally { roleBusy.value = false }
}
function editPersona(member) {
  editing.value = { ...member, roomId: props.roomId }
  editText.value = member.persona ?? ''
}
async function savePersona() {
  const target = editing.value
  if (!target || roleBusy.value) return
  roleBusy.value = true
  try {
    await api.editAiPersona(target.roomId, target.id, editText.value, target.persona_version)
    editing.value = null
    emit('notice', '人设已保存，从下一轮回复开始使用')
    emit('changed')
  } catch (e) {
    emit('notice', requestErrorText(e, e.code === 'persona_conflict' ? '人设已更新，请重新打开后编辑' : '人设保存失败'))
  } finally { roleBusy.value = false }
}
const showBans = ref(false)
const bannedUsers = ref([])
const banName = ref('')
const bansLoading = ref(false)
const moderationBusy = ref(false)

const myRole = computed(() => props.members.find((m) => m.id === props.me.member_id)?.role ?? 'member')
const isOwner = computed(() => myRole.value === 'owner')
const canManage = computed(() => myRole.value === 'owner' || myRole.value === 'admin')
const inRoom = computed(() => new Set(props.members.filter((m) => m.type === 'ai').map((m) => m.slug)))
const addable = computed(() => agents.value.filter((a) => !inRoom.value.has(a.slug)))
const selected = computed(() => addable.value.find((a) => a.slug === chosen.value) ?? null)
// Preselect the first AI that can be invited; a placeholder adds a useless step.
watch(addable, (list) => {
  if (list.some((a) => a.slug === chosen.value)) return
  chosen.value = (list.find((a) => a.has_key) ?? list[0])?.slug ?? ''
}, { immediate: true })

const ROLE_NAMES = { owner: '群主', admin: '管理员' }

function canModerate(member) {
  if (member.type !== 'human' || member.id === props.me.member_id) return false
  return (isOwner.value && ['admin', 'member'].includes(member.role)) ||
    (myRole.value === 'admin' && member.role === 'member')
}

function managementError(error) {
  if (error.code === 'forbidden') return '你没有权限管理该成员'
  if (error.code === 'user_not_found') return '找不到这个用户'
  if (error.code === 'invalid_argument') return '不能对自己或非人类成员执行此操作'
  return requestErrorText(error, '操作失败，请稍后重试')
}

async function loadBans() {
  if (!canManage.value) return
  const roomId = props.roomId
  bansLoading.value = true
  try {
    const result = await api.listBans(roomId)
    if (roomId === props.roomId && canManage.value) bannedUsers.value = result.bans
  } catch (error) {
    if (roomId === props.roomId) emit('notice', managementError(error))
  } finally {
    if (roomId === props.roomId) bansLoading.value = false
  }
}

async function toggleBans() {
  showBans.value = !showBans.value
  if (showBans.value) await loadBans()
}

async function moderate(action, username, verb) {
  if (moderationBusy.value || !username || !window.confirm(`确定${verb} ${username}？`)) return
  moderationBusy.value = true
  try {
    await action(props.roomId, username)
    banName.value = ''
    emit('notice', `已${verb} ${username}`)
    emit('changed')
    if (showBans.value) await loadBans()
  } catch (error) {
    emit('notice', managementError(error))
  } finally {
    moderationBusy.value = false
  }
}

async function unban(username) {
  if (moderationBusy.value) return
  moderationBusy.value = true
  try {
    await api.unbanUser(props.roomId, username)
    await loadBans()
    emit('notice', `已解除对 ${username} 的封禁`)
    emit('changed')
  } catch (error) {
    emit('notice', managementError(error))
  } finally {
    moderationBusy.value = false
  }
}

watch(() => props.roomId, () => {
  editing.value = null
  inviting.value = null
  invitationPersona.value = ''
  showBans.value = false
  bannedUsers.value = []
  bansLoading.value = false
  banName.value = ''
})
watch(canManage, (allowed) => {
  if (!allowed) {
    showBans.value = false
    bannedUsers.value = []
  }
})

async function loadAgents() {
  agents.value = (await api.aiAgents()).agents
}

async function loadRequests() {
  requests.value = canManage.value ? (await api.aiRequests(props.roomId)).requests : []
}

function openInvite() {
  const agent = selected.value
  if (!agent) return
  if (!agent.has_key) {
    emit('notice', `请先在设置里填写 ${providerName(agent.provider)} 的 API key`)
    emit('open-settings')
    return
  }
  inviting.value = agent
  invitationPersona.value = agent.persona ?? ''
}

async function addAi() {
  const agent = inviting.value
  if (!agent || roleBusy.value) return
  roleBusy.value = true
  try {
    const { status } = await api.requestAi(props.roomId, agent.id ?? agent.slug, invitationPersona.value)
    emit('notice', status === 'joined' ? `${agent.name} 已加入` : '已提交，等待群主或管理员审批')
    inviting.value = null
    invitationPersona.value = ''
    emit('changed')
  } catch (e) {
    emit('notice', requestErrorText(e, e.code === 'api_key_required' ? `请先填写 ${providerName(e.provider)} 的 API key` : '操作失败'))
  } finally { roleBusy.value = false }
}

function review(slug, approve) {
  return run(`review:${slug}`, async () => {
    try {
      await (approve ? api.approveAi : api.rejectAi)(props.roomId, slug)
      emit('notice', approve ? '已批准' : '已拒绝')
    } catch (error) {
      emit('notice', requestErrorText(error, '这条申请已被处理'))
    }
    await loadRequests()
    emit('changed')
  })
}

function act(action, member, done) {
  return run(`act:${member.id}`, async () => {
    try {
      await action(props.roomId, member.type === 'ai' ? (member.id ?? member.slug) : member.name)
      if (done) emit('notice', done)
      emit('changed')
    } catch (e) {
      emit('notice', requestErrorText(e, e.code === 'forbidden' ? '只有群主或管理员能这样做' : '操作失败'))
    }
  })
}

watch(() => props.requestEvent, (frame) => {
  if (frame) loadRequests()
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
        <span v-if="member.type === 'ai'" class="tag">{{ member.model_slug ?? 'AI' }}</span>
        <span v-if="member.type === 'ai'" class="hint">{{ member.is_legacy ? '旧版预置AI' : `created by ${member.creator_name ?? ''}` }}</span>
        <button v-if="member.type === 'ai' && !member.is_legacy && member.creator_user_id === (me.user_id ?? me.id)" class="link" @click="editPersona(member)">人设</button>
        <span v-if="ROLE_NAMES[member.role]" class="tag">{{ ROLE_NAMES[member.role] }}</span>
        <span class="member-actions">
          <template v-if="isOwner && member.type === 'human' && member.id !== me.member_id">
            <button class="link" :disabled="isBusy(`act:${member.id}`)" @click="act(api.transferOwner, member, `已把群主转让给 ${member.name}`)">转让群主</button>
            <button v-if="member.role === 'admin'" class="link" :disabled="isBusy(`act:${member.id}`)" @click="act(api.dismissAdmin, member, `已取消 ${member.name} 的管理员`)">取消管理员</button>
            <button v-else class="link" :disabled="isBusy(`act:${member.id}`)" @click="act(api.appointAdmin, member, `已设 ${member.name} 为管理员`)">设为管理员</button>
          </template>
          <template v-if="canModerate(member)">
            <button class="link danger" :disabled="moderationBusy" :aria-label="`踢出 ${member.name}`"
              @click="moderate(api.kickMember, member.name, '踢出')">踢出</button>
            <button class="link danger" :disabled="moderationBusy" :aria-label="`封禁 ${member.name}`"
              @click="moderate(api.banUser, member.name, '封禁')">封禁</button>
          </template>
          <button
            v-if="canManage && member.type === 'ai'"
            class="link danger"
            :disabled="isBusy(`act:${member.id}`)"
            :aria-busy="isBusy(`act:${member.id}`)"
            @click="act(api.removeAi, member, `${member.name} 已移出`)"
          >
            {{ isBusy(`act:${member.id}`) ? '移出中…' : '移出' }}
          </button>
        </span>
      </li>
    </ul>

    <button v-if="canManage" class="link" @click="toggleBans">封禁列表</button>
    <section v-if="canManage && showBans" aria-label="封禁列表">
      <h3>封禁列表</h3>
      <p v-if="bansLoading" class="hint">加载中…</p>
      <ul v-else class="member-list">
        <li v-for="entry in bannedUsers" :key="entry.username">
          <div>
            <strong>{{ entry.username }}</strong>
            <div class="hint">执行者：{{ entry.banned_by ?? '账号已删除' }}</div>
            <time class="hint">{{ new Date(entry.created_at).toLocaleString('zh-CN') }}</time>
          </div>
          <button class="link" :disabled="moderationBusy" :aria-label="`解封 ${entry.username}`"
            @click="unban(entry.username)">解封</button>
        </li>
      </ul>
      <p v-if="!bansLoading && !bannedUsers.length" class="hint">暂无封禁记录</p>
      <form class="inline-form" @submit.prevent="moderate(api.banUser, banName.trim(), '封禁')">
        <input v-model="banName" aria-label="封禁用户名" placeholder="封禁用户名" :disabled="moderationBusy" />
        <button :disabled="moderationBusy || !banName.trim()">封禁用户</button>
      </form>
    </section>

    <div class="role-row">
      <button class="link" @click="toggleMyAi">我的AI</button>
      <button class="link" @click="openCreate">创建私人AI</button>
    </div>
    <section v-if="showMyAi" aria-label="我的AI">
      <h3>我的AI · 仅自己可邀请</h3>
      <ul class="member-list">
        <li v-for="agent in agents" :key="agent.id ?? agent.slug" class="role-row">
          <span>{{ agent.name }} · {{ agent.model_slug }}</span>
          <button class="link" :aria-label="`${agent.name} 人设`" @click="editRolePersona(agent)">人设</button>
        </li>
      </ul>
      <p v-if="!agents.length" class="hint">还没有私人AI</p>
    </section>

    <form v-if="addable.length" class="inline-form" aria-label="邀请AI" @submit.prevent="openInvite">
      <span v-if="addable.length === 1" class="invite-name">
        {{ addable[0].name }}{{ addable[0].has_key ? '' : `（需要 ${providerName(addable[0].provider)} key）` }}
      </span>
      <select v-else v-model="chosen" aria-label="选择 AI">
        <option v-for="agent in addable" :key="agent.slug" :value="agent.slug" :disabled="!agent.has_key">
          {{ agent.name }}{{ agent.has_key ? '' : `（需要 ${providerName(agent.provider)} key）` }}
        </option>
      </select>
      <button :disabled="!selected">{{ canManage ? '邀请' : '申请' }}</button>
    </form>

    <AppDialog v-if="creating" title="创建私人AI" wide @close="creating = false">
      <form class="dialog-form" aria-label="创建私人AI" @submit.prevent="createRole">
        <select v-model="model" aria-label="底层模型">
          <option v-for="item in models" :key="item.model_slug" :value="item.model_slug">{{ item.name }}</option>
        </select>
        <input v-model="roleName" aria-label="AI角色名称" maxlength="64" placeholder="名称，例如：Claude A" />
        <PersonaEditor v-model="rolePersona" label="默认人设" />
        <div class="dialog-actions">
          <button type="button" @click="creating = false">取消</button>
          <button class="primary" :disabled="roleBusy || !roleName.trim()" :aria-busy="roleBusy">{{ roleBusy ? '创建中…' : '创建' }}</button>
        </div>
      </form>
    </AppDialog>

    <AppDialog v-if="roleEditing" :title="`${roleEditing.name} · 默认人设`" wide @close="roleEditing = null">
      <form class="dialog-form" aria-label="编辑默认人设" @submit.prevent="saveRolePersona">
        <p class="hint">邀请进新房间时自动填入；已在房间里的人设不受影响。</p>
        <PersonaEditor v-model="roleText" label="默认人设" />
        <div class="dialog-actions">
          <button type="button" @click="roleEditing = null">取消</button>
          <button class="primary" :disabled="roleBusy" :aria-busy="roleBusy">{{ roleBusy ? '保存中…' : '保存' }}</button>
        </div>
      </form>
    </AppDialog>

    <AppDialog v-if="inviting" :title="`${canManage ? '邀请' : '申请邀请'} ${inviting.name}`" wide @close="inviting = null">
      <form class="dialog-form" aria-label="邀请AI人设" @submit.prevent="addAi">
        <p class="hint">本房间人设，已按默认人设填好，可以修改或留空。</p>
        <PersonaEditor v-model="invitationPersona" label="邀请房间人设" />
        <div class="dialog-actions">
          <button type="button" @click="inviting = null">取消</button>
          <button class="primary" :disabled="roleBusy" :aria-busy="roleBusy">{{ roleBusy ? '提交中…' : (canManage ? '邀请' : '申请') }}</button>
        </div>
      </form>
    </AppDialog>

    <AppDialog v-if="editing" :title="`${editing.name} · 本房间人设`" wide @close="editing = null">
      <form class="dialog-form" aria-label="编辑房间人设" @submit.prevent="savePersona">
        <PersonaEditor v-model="editText" label="编辑人设正文" />
        <div class="dialog-actions">
          <button type="button" @click="editing = null">取消</button>
          <button class="primary" :disabled="roleBusy" :aria-busy="roleBusy">{{ roleBusy ? '保存中…' : '保存' }}</button>
        </div>
      </form>
    </AppDialog>

    <section v-if="canManage && requests.length" class="requests">
      <h3>待审批（{{ requests.length }}）</h3>
      <div v-for="request in requests" :key="request.slug" class="request">
        <span>{{ request.name ?? agents.find((a) => a.slug === request.slug)?.name ?? request.slug }}</span>
        <span v-if="request.requested_by?.name" class="hint">{{ request.requested_by.name }} 申请</span>
        <button v-if="!request.is_legacy" class="link" :disabled="isBusy(`review:${request.id ?? request.slug}`)" @click="review(request.id ?? request.slug, true)">批准</button>
        <button class="link danger" :disabled="isBusy(`review:${request.id ?? request.slug}`)" @click="review(request.id ?? request.slug, false)">拒绝</button>
      </div>
    </section>
  </aside>
</template>
