<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, shallowRef, watch } from 'vue'
import { api, ApiError } from '../lib/api.js'
import { requestErrorText } from '../lib/request-errors.js'
import { AiDrafts } from '../lib/ai-drafts.js'
import { ChatSocket } from '../lib/chat-socket.js'
import { leave, prepareLeave } from '../lib/leave.js'
import { insertMention, matchingMembers, mentionQuery } from '../lib/mentions.js'
import { RoomStream } from '../lib/room-stream.js'
import { fetchAllRooms } from '../lib/rooms.js'
import { exportRoom } from '../lib/transcript.js'
import LeaveDialog from './LeaveDialog.vue'
import MembersPanel from './MembersPanel.vue'
import SettingsDialog from './SettingsDialog.vue'

const ROOM_POLL_MS = 10000

const props = defineProps({
  user: { type: Object, required: true },
  logoutError: { type: String, default: '' },
  loggingOut: { type: Boolean, default: false },
})
const emit = defineEmits(['logout', 'session-ended'])

const rooms = ref([])
const activeId = ref(null)
const removedRooms = reactive(new Map())
const autoSelectRoom = ref(true)
let roomRequest = 0
const connected = ref(false)
const notice = ref('')
const newRoomName = ref('')
const inviteName = ref('')
const draft = ref('')
const selectedMentions = ref([])
const messagesEl = ref(null)
const leaving = shallowRef(null)
const leaveBusy = ref(false)

const members = ref([])
const showMembers = ref(false)
const showSettings = ref(false)
const requestEvent = shallowRef(null)
const membersPanel = ref(null)
const composerEl = ref(null)
const caret = ref(0)
const aiState = ref({ paused: false, generations: [] })
const aiControlBusy = ref(false)
const canManageAi = computed(() => ['owner', 'admin'].includes(members.value.find(m => m.id === props.user.member_id)?.role))
function canStopAi(id) {
  return canManageAi.value || aiState.value.generations.some(g => g.id === id && g.payer_member_id === props.user.member_id)
}
async function loadAiState() {
  const id = activeId.value
  if (!id || activeRoom.value?.status !== 'joined' || activeRemoval.value) return
  try {
    const state = await api.aiState(id)
    if (activeId.value === id) aiState.value = state
  } catch { /* Status is retried by the room poll and reconnect. */ }
}
async function controlAi(generation = null) {
  const room = activeId.value
  if (!room || aiControlBusy.value) return
  aiControlBusy.value = true
  try {
    if (generation) await api.stopAi(room, generation)
    else await api.pauseAi(room, !aiState.value.paused)
    if (activeId.value === room) {
      await loadAiState()
      await loadRoom(room)
      if (generation) drafts.close(generation)
      else if (aiState.value.paused) drafts.clear(room)
      draftRevision.value++
    }
  } catch (error) { notice.value = requestErrorText(error, 'AI控制失败') }
  finally { aiControlBusy.value = false }
}
const searchOpen = ref(false)
const searchText = ref('')
const searchSender = ref('')
const searchFrom = ref('')
const searchTo = ref('')
const searchResults = ref([])
const searchBefore = ref(null)
const searchBusy = ref(false)
const contextSeq = ref(null)
const contextMessages = ref([])
const readThrough = new Map()
let readTimer
let attentionTimer
function scheduleAttentionRefresh() {
  if (attentionTimer) return
  attentionTimer = setTimeout(() => {
    attentionTimer = null
    refreshRooms()
  }, 200)
}

async function updateRead() {
  const roomId = activeId.value
  const el = messagesEl.value
  if (!roomId || contextSeq.value || document.visibilityState !== 'visible' ||
      activeRoom.value?.status !== 'joined' || activeRemoval.value || !el ||
      el.scrollHeight - el.scrollTop - el.clientHeight > 120) return
  const stream = streams.get(roomId)
  if (!stream?.messages.length) return
  const seq = stream.messages.at(-1).seq
  if (seq > stream.contiguous || seq <= (readThrough.get(roomId) ?? 0)) return
  readThrough.set(roomId, seq)
  try {
    await api.markRead(roomId, seq)
    const room = rooms.value.find(r => r.id === roomId)
    if (room && room.last_seq <= seq) { room.unread_count = 0; room.mention_seq = null }
  } catch {
    if (readThrough.get(roomId) === seq) readThrough.delete(roomId)
  }
}
function scheduleRead() {
  clearTimeout(readTimer)
  readTimer = setTimeout(updateRead, 150)
}

async function findMessages(more = false) {
  const roomId = activeId.value
  if (!roomId || searchBusy.value) return
  searchBusy.value = true
  try {
    const params = { q: searchText.value }
    if (searchSender.value) params.sender = searchSender.value
    if (searchFrom.value) params.from = Date.parse(searchFrom.value)
    if (searchTo.value) params.to = Date.parse(searchTo.value)
    if (more && searchBefore.value) params.before = searchBefore.value
    const result = await api.search(roomId, params)
    if (activeId.value !== roomId) return
    searchResults.value = more ? [...searchResults.value, ...result.messages] : result.messages
    searchBefore.value = result.messages.length === 50 ? result.next_before : null
  } catch (error) { notice.value = requestErrorText(error, '搜索失败') }
  finally { searchBusy.value = false }
}

async function locateMessage(roomId, seq, mention = false) {
  if (removedRooms.has(roomId)) return
  contextMessages.value = []
  contextSeq.value = seq
  activeId.value = roomId
  try {
    const [older, newer] = await Promise.all([
      api.history(roomId, { before: seq + 1, limit: 30 }),
      api.history(roomId, { after: seq, limit: 30 }),
    ])
    if (activeId.value !== roomId || contextSeq.value !== seq || removedRooms.has(roomId)) return
    contextMessages.value = [...older.messages, ...newer.messages]
    await nextTick()
    const target = messagesEl.value?.querySelector(`[data-seq="${seq}"]`)
    target?.scrollIntoView?.({ block: 'center' })
    if (mention && target && document.visibilityState === 'visible') {
      await api.markRead(roomId, seq)
      await refreshRooms()
    }
  } catch (error) { notice.value = requestErrorText(error, '加载消息失败') }
}

async function openRoom(room) {
  autoSelectRoom.value = true
  activeId.value = room.id
  contextSeq.value = null
  if (room.status === 'joined') await showLatest()
}
async function showLatest() {
  contextSeq.value = null
  await loadRoom(activeId.value)
  await nextTick()
  if (messagesEl.value) messagesEl.value.scrollTop = messagesEl.value.scrollHeight
  scheduleRead()
}

// AI replies still being generated, shown below the timeline.
const drafts = new AiDrafts()
const draftRevision = ref(0)

const streams = new Map()
// Messages not yet confirmed, by room: { clientMsgId, content, failed, throttled }.
const outbox = reactive(new Map())
const revision = ref(0)

const activeRoom = computed(() => rooms.value.find((r) => r.id === activeId.value) ?? null)

const activeRemoval = computed(() => removedRooms.get(activeId.value))
function removalNotice(room) {
  return removedRooms.get(room.id) === 'banned' ? `你已被禁止进入房间 ${room.name}` : `你已被踢出房间 ${room.name}`
}

const timeline = computed(() => {
  void revision.value
  if (contextSeq.value) return contextMessages.value
  const stream = streams.get(activeId.value)
  return stream ? stream.messages : []
})

const aiDrafts = computed(() => {
  void draftRevision.value
  return drafts.forRoom(activeId.value).map((d) => ({
    ...d,
    name: members.value.find((m) => m.id === d.memberId)?.name ?? 'AI',
  }))
})

const suggestions = computed(() => matchingMembers(members.value, mentionQuery(draft.value, caret.value)))

const pending = computed(() => {
  const confirmed = new Set(timeline.value.map((m) => m.client_msg_id))
  return (outbox.get(activeId.value) ?? []).filter((m) => !confirmed.has(m.clientMsgId))
})

function streamFor(roomId) {
  if (!streams.has(roomId)) {
    streams.set(
      roomId,
      new RoomStream({
        fetchAfter: async (after) => removedRooms.has(roomId) ? [] : (await api.history(roomId, { after })).messages,
        onChange: () => revision.value++,
        onMessages: (messages) => {
          for (const message of messages) {
            if (message.generation_id) drafts.close(message.generation_id)
          }
          draftRevision.value++
        },
      }),
    )
  }
  if (!removedRooms.has(roomId)) streams.get(roomId).resume()
  return streams.get(roomId)
}

const socket = new ChatSocket({
  // Logged out elsewhere or expired: back to the login screen.
  onSessionEnded: () => emit('session-ended'),
  onStatus: (up) => {
    connected.value = up
    if (!up) {
      drafts.clear()
      draftRevision.value++
    }
  },
  onMessage: (message) => {
    if (!removedRooms.has(message.room_id)) {
      streamFor(message.room_id).receive(message)
      scheduleAttentionRefresh()
    }
  },
  onEvent: (frame) => onRoomEvent(frame),
  // Subscribing first and reading history second means nothing can fall
  // between the two.
  onSubscribed: (roomId) => loadRoom(roomId),
  onRoomLeft: (roomId, reason) => {
    if (reason === 'kicked' || reason === 'banned') {
      roomRequest++ // Discard list responses started before revocation.
      removedRooms.set(roomId, reason)
      stopTracking(roomId)
      if (activeId.value === roomId) {
        showMembers.value = false
        members.value = []
      }
    } else {
      forgetRoom(roomId)
    }
  },
  onSendResult: (roomId, clientMsgId, result) => {
    const box = outbox.get(roomId) ?? []
    const entry = box.find((m) => m.clientMsgId === clientMsgId)
    if (result.ok) {
      const stream = streamFor(roomId)
      if (entry) {
        stream.receive({ type: 'message', room_id: roomId, seq: result.seq,
          created_at: result.created_at, client_msg_id: clientMsgId, content: entry.content,
          member: { id: props.user.member_id, name: props.user.username, type: 'human' }, local: true })
        outbox.set(roomId, box.filter((m) => m !== entry))
      }
      stream.sync().catch(() => {
        if (!removedRooms.has(roomId)) notice.value = '加载消息失败'
      })
    } else if (entry) {
      entry.failed = true
    }
  },
  // The socket resends by itself once the wait is over.
  onThrottled: (roomId, clientMsgId) => {
    const entry = (outbox.get(roomId) ?? []).find((m) => m.clientMsgId === clientMsgId)
    if (entry) entry.throttled = true
  },
})

function onRoomEvent(frame) {
  if (removedRooms.has(frame.room_id)) return
  const here = frame.room_id === activeId.value
  if (frame.type === 'read_updated') {
    scheduleAttentionRefresh()
  } else if (frame.type === 'ai_state_changed') {
    if (here) loadAiState()
  } else if (frame.type === 'ai_chunk') {
    if (here && !aiState.value.generations.some(g => g.id === frame.generation_id)) loadAiState()
    const member = here ? members.value.find(m => m.id === frame.member_id) : null
    if (member?.admission_id && frame.admission_id && member.admission_id !== frame.admission_id) return
    drafts.chunk(frame)
    draftRevision.value++
  } else if (frame.type === 'ai_failed') {
    drafts.close(frame.generation_id)
    draftRevision.value++
  } else if (frame.type === 'ai_notice') {
    notice.value = frame.message
  } else if (frame.type === 'role_changed' || frame.type === 'members_changed') {
    if (here) loadMembers()
  } else if (frame.type === 'ai_request') {
    if (here) requestEvent.value = frame
    notice.value = '有成员申请邀请 AI，等待你审批'
  } else if (frame.type === 'ai_request_done') {
    notice.value = frame.approved ? 'AI 邀请已获批准' : 'AI 邀请被拒绝'
  }
}

async function loadMembers() {
  const room = activeRoom.value
  if (!room || room.status !== 'joined' || removedRooms.has(room.id)) {
    members.value = []
    return
  }
  try {
    const result = await api.members(room.id)
    if (activeId.value === room.id && !removedRooms.has(room.id)) {
      members.value = result.members
      for (const partial of drafts.forRoom(room.id)) {
        if (!result.members.some(m => m.id === partial.memberId && (!partial.admissionId || !m.admission_id || partial.admissionId === m.admission_id))) drafts.close(partial.id)
      }
      draftRevision.value++
    }
  } catch {
    if (activeId.value === room.id) members.value = []
  }
}

function trackCaret(event) {
  caret.value = event.target.selectionStart ?? draft.value.length
  selectedMentions.value = selectedMentions.value.filter(m => draft.value.slice(m.start, m.end) === `@${m.name}`)
}

async function chooseMention(member) {
  const result = insertMention(draft.value, caret.value, member.name)
  draft.value = result.text
  if (member.type === 'ai') selectedMentions.value.push({ id: member.id, name: member.name, start: result.caret - member.name.length - 2, end: result.caret - 1 })
  caret.value = result.caret
  await nextTick()
  composerEl.value?.setSelectionRange(result.caret, result.caret)
  composerEl.value?.focus()
}

async function loadRoom(roomId) {
  if (removedRooms.has(roomId)) return
  const stream = streamFor(roomId)
  try {
    if (stream.bySeq.size > 0) {
      await stream.sync()
    } else {
      const { messages } = await api.history(roomId)
      if (removedRooms.has(roomId)) return
      stream.addPage(messages)
      await stream.sync()
    }
  } catch {
    if (!removedRooms.has(roomId)) notice.value = '加载消息失败'
  }
}

async function loadOlder() {
  if (activeRemoval.value) return
  const stream = streamFor(activeId.value)
  const oldest = stream.messages[0]
  if (!oldest || oldest.seq <= 1) return
  const { messages } = await api.history(activeId.value, { before: oldest.seq })
  stream.addPage(messages)
}

const canLoadOlder = computed(() => !activeRemoval.value && timeline.value.length > 0 && timeline.value[0].seq > 1)

async function refreshRooms() {
  const request = ++roomRequest
  try {
    const list = await fetchAllRooms(api.rooms)
    if (request !== roomRequest) return
    const returned = new Set(list.map(room => room.id))
    for (const id of returned) removedRooms.delete(id)
    const retained = rooms.value.filter(room => removedRooms.has(room.id) && !returned.has(room.id))
    rooms.value = [...list, ...retained]
    const joined = new Set(list.filter((r) => r.status === 'joined').map((r) => r.id))
    for (const id of joined) {
      if (!socket.rooms.has(id)) socket.subscribe(id)
    }
    for (const id of socket.rooms) {
      if (!joined.has(id)) forgetRoom(id)
    }
    if (activeId.value !== null && !rooms.value.some((r) => r.id === activeId.value)) {
      activeId.value = null
    }
    if (activeId.value === null && autoSelectRoom.value && list.length > 0) activeId.value = list[0].id
    scheduleRead()
  } catch (e) {
    if (e instanceof ApiError && e.status === 401) location.reload()
  }
}

function stopTracking(roomId) {
  drafts.clear(roomId)
  draftRevision.value++
  socket.unsubscribe(roomId)
  streams.get(roomId)?.dispose()
}

function forgetRoom(roomId) {
  drafts.clear(roomId)
  draftRevision.value++
  socket.unsubscribe(roomId)
  streams.get(roomId)?.dispose()
  streams.delete(roomId)
  outbox.delete(roomId)
  if (activeId.value === roomId) activeId.value = null
  revision.value++
}

async function createRoom() {
  const name = newRoomName.value.trim()
  if (!name) return
  try {
    const room = await api.createRoom(name)
    newRoomName.value = ''
    await refreshRooms()
    autoSelectRoom.value = true
    activeId.value = room.id
  } catch (error) {
    notice.value = requestErrorText(error, '创建房间失败，名称最多 64 个字符')
  }
}

async function joinRoom() {
  try {
    await api.join(activeId.value)
    await refreshRooms()
  } catch (error) {
    notice.value = requestErrorText(error, '加入失败，房间可能已被删除')
    await refreshRooms()
  }
}

async function invite() {
  const username = inviteName.value.trim()
  if (!username) return
  try {
    await api.invite(activeId.value, username)
    notice.value = `已邀请 ${username}`
    inviteName.value = ''
  } catch (e) {
    notice.value = requestErrorText(e, e.code === 'user_banned' ? '该用户已被此房间封禁，需要先解封' :
      e.code === 'user_not_found' ? '找不到这个用户' : '邀请失败')
  }
}

function send() {
  const content = draft.value
  if (!content.trim() || !activeRoom.value || activeRemoval.value) return
  if ([...content].length > 4000) {
    notice.value = '消息最多 4000 个字符'
    return
  }
  const ambiguous = members.value.filter(m => m.type === 'ai').some(m =>
    members.value.filter(other => other.type === 'ai' && other.name.toLowerCase() === m.name.toLowerCase()).length > 1 &&
    content.toLowerCase().includes(`@${m.name.toLowerCase()}`) &&
    !selectedMentions.value.some(selected => selected.name === m.name))
  if (ambiguous) { notice.value = '有同名AI，请从@候选中选择具体角色'; return }
  const clientMsgId = crypto.randomUUID()
  const roomId = activeId.value
  const mentionIds = [...new Set(selectedMentions.value.filter(m => content.includes(`@${m.name}`)).map(m => m.id))]
  outbox.set(roomId, [...(outbox.get(roomId) ?? []), { clientMsgId, content, mentionIds, failed: false, throttled: false }])
  socket.send(roomId, clientMsgId, content, mentionIds)
  selectedMentions.value = []
  draft.value = ''
}

function retry(entry) {
  if (activeRemoval.value) return
  entry.failed = false
  entry.throttled = false
  socket.send(activeId.value, entry.clientMsgId, entry.content, entry.mentionIds ?? [])
}

function onKeydown(event) {
  if (event.key === 'Tab' && suggestions.value.length) {
    event.preventDefault()
    chooseMention(suggestions.value[0])
    return
  }
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
    event.preventDefault()
    send()
  }
}

async function startLeave() {
  try {
    leaving.value = await prepareLeave(api, activeRoom.value)
  } catch {
    notice.value = '操作失败，请重试'
  }
}

async function exportActive() {
  try {
    const count = await exportRoom(activeRoom.value)
    notice.value = `已导出 ${count} 条消息`
  } catch {
    notice.value = '导出失败，请重试'
  }
}

async function confirmLeave() {
  leaveBusy.value = true
  try {
    const { room } = leaving.value
    leaving.value = await leave(api, leaving.value)
    if (leaving.value === null) {
      forgetRoom(room.id)
      await refreshRooms()
    }
  } catch (e) {
    if (e.code === 'owner_must_transfer') {
      leaving.value = null
      showMembers.value = true
      notice.value = '你是群主，请先在成员列表里把群主转让给别人'
    } else {
      notice.value = requestErrorText(e, '退出失败，请重试')
    }
  } finally {
    leaveBusy.value = false
  }
}

function formatTime(ms) {
  return new Date(ms).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
}

function isMine(message) {
  return message.member.id === props.user.member_id
}

watch([timeline, pending], async () => {
  const el = messagesEl.value
  const atBottom = !el || el.scrollHeight - el.scrollTop - el.clientHeight < 120
  await nextTick()
  if (!contextSeq.value && atBottom && messagesEl.value) {
    messagesEl.value.scrollTop = messagesEl.value.scrollHeight
    scheduleRead()
  }
})

watch([() => activeId.value, () => activeRoom.value?.status, () => activeRemoval.value], () => {
  searchResults.value = []
  searchBefore.value = null
  aiState.value = { paused: false, generations: [] }
  loadMembers()
  loadAiState()
})

watch(notice, (text) => {
  if (text) setTimeout(() => notice.value === text && (notice.value = ''), 4000)
})

let poll
onMounted(() => {
  document.addEventListener('visibilitychange', scheduleRead)
  refreshRooms()
  poll = setInterval(() => { refreshRooms(); loadAiState() }, ROOM_POLL_MS)
})
onBeforeUnmount(() => {
  clearInterval(poll)
  clearTimeout(readTimer)
  clearTimeout(attentionTimer)
  document.removeEventListener('visibilitychange', scheduleRead)
  socket.close()
})
</script>

<template>
  <div class="shell">
    <aside class="sidebar">
      <header class="sidebar-head">
        <strong>{{ user.username }}</strong>
        <button class="link" @click="showSettings = true">设置</button>
        <button class="link" :disabled="loggingOut" @click="$emit('logout')">{{ loggingOut ? '退出中…' : '退出登录' }}</button>
      </header>
      <form class="inline-form" @submit.prevent="createRoom">
        <input v-model="newRoomName" placeholder="新房间名称" maxlength="64" aria-label="新房间名称" />
        <button>创建</button>
      </form>
      <nav class="room-list">
        <button
          v-for="room in rooms"
          :key="room.id"
          class="room"
          :class="{ active: room.id === activeId }"
          @click="openRoom(room)"
        >
          <span class="room-name">{{ room.name }}</span>
          <span v-if="room.unread_count" class="tag" aria-label="未读消息">{{ room.unread_count > 99 ? '99+' : room.unread_count }}</span>
          <span v-if="room.mention_seq" class="tag" role="button" tabindex="0"
            @click.stop="locateMessage(room.id, room.mention_seq, true)"
            @keydown.enter.stop="locateMessage(room.id, room.mention_seq, true)">有人@我</span>
          <span v-if="removedRooms.has(room.id)" class="tag">{{ removedRooms.get(room.id) === 'banned' ? '已被封禁' : '已被踢出' }}</span>
          <span v-else-if="room.status === 'invited'" class="tag">待加入</span>
          <span v-else class="room-count">{{ room.member_count }} 人</span>
        </button>
        <p v-if="rooms.length === 0" class="empty">还没有房间。创建一个，或等待别人邀请你。</p>
      </nav>
    </aside>

    <main class="chat">
      <template v-if="activeRoom">
        <header class="chat-head">
          <h1>{{ activeRoom.name }}</h1>
          <template v-if="activeRoom.status === 'joined' && !activeRemoval">
            <form class="inline-form" @submit.prevent="invite">
              <input v-model="inviteName" placeholder="邀请用户名" aria-label="邀请用户名" />
              <button>邀请</button>
            </form>
            <button :class="{ active: showMembers }" @click="showMembers = !showMembers">
              成员 {{ members.length }}
            </button>
            <span v-if="aiState.paused" class="tag">AI已暂停</span>
            <button v-if="canManageAi" :disabled="aiControlBusy" @click="controlAi()">{{ aiState.paused ? '恢复AI' : '暂停AI' }}</button>
            <button @click="searchOpen = !searchOpen">搜索</button>
            <button @click="exportActive">导出</button>
            <button class="danger" @click="startLeave">退出房间</button>
          </template>
        </header>
        <section v-if="searchOpen && activeRoom.status === 'joined'" class="search-panel">
          <form class="inline-form" @submit.prevent="findMessages()">
            <input v-model="searchText" aria-label="搜索关键词" placeholder="搜索聊天记录" maxlength="200" />
            <select v-model="searchSender" aria-label="搜索发送者"><option value="">所有成员</option>
              <option v-for="m in members" :key="m.id" :value="m.id">{{ m.name }}</option></select>
            <input v-model="searchFrom" type="datetime-local" aria-label="开始时间" />
            <input v-model="searchTo" type="datetime-local" aria-label="结束时间" />
            <button :disabled="searchBusy">搜索</button>
          </form>
          <ul><li v-for="message in searchResults" :key="message.seq">
            <button class="link" @click="locateMessage(activeId, message.seq)">{{ message.member.name }} · {{ message.content }}<span v-if="message.generation_outcome === 'failed'"> · 生成失败</span></button>
          </li></ul>
          <button v-if="searchBefore" :disabled="searchBusy" @click="findMessages(true)">更多结果</button>
        </section>
        <button v-if="contextSeq" class="link" @click="showLatest">回到最新消息</button>


        <div v-if="activeRoom.status === 'invited'" class="invite-panel">
          <p>你被邀请加入这个房间。</p>
          <button class="primary" @click="joinRoom">加入房间</button>
        </div>

        <template v-else>
          <p v-if="activeRemoval" class="banner" role="status">{{ removalNotice(activeRoom) }}</p>
          <div ref="messagesEl" @scroll="scheduleRead" class="messages" aria-live="polite">
            <button v-if="canLoadOlder" class="link older" @click="loadOlder">加载更早的消息</button>
            <p v-if="timeline.length === 0 && pending.length === 0" class="empty">还没有消息，说点什么吧。</p>
            <article
              v-for="message in timeline"
              :key="message.seq"
              :data-seq="message.seq"
              class="message"
              :class="{ mine: isMine(message) }"
            >
              <div class="meta">
                <span>{{ message.member.name }}</span>
                <time>{{ formatTime(message.created_at) }}</time>
                <span v-if="message.generation_outcome === 'failed'">生成失败</span>
                <span v-else-if="message.stopped">已停止</span>
              </div>
              <p class="bubble">{{ message.content }}</p>
            </article>
            <article v-for="d in aiDrafts" :key="d.id" class="message pending">
              <div class="meta">
                <span>{{ d.name }}</span>
                <span>正在输入</span>
                <button v-if="canStopAi(d.id)" class="link" :disabled="aiControlBusy" @click="controlAi(d.id)">停止生成</button>
              </div>
              <p class="bubble">{{ d.text }}</p>
            </article>
            <article v-for="entry in pending" :key="entry.clientMsgId" class="message mine pending">
              <div class="meta">
                <span v-if="entry.failed" class="error">发送失败</span>
                <span v-else-if="entry.throttled">发送过快，稍后自动重试</span>
                <span v-else>发送中</span>
                <button v-if="entry.failed && !activeRemoval" class="link" @click="retry(entry)">重试</button>
              </div>
              <p class="bubble">{{ entry.content }}</p>
            </article>
          </div>

          <form class="composer" @submit.prevent="send">
            <ul v-if="suggestions.length" class="mention-list" role="listbox" aria-label="提及成员">
              <li v-for="member in suggestions" :key="member.id">
                <button type="button" class="link" @click="chooseMention(member)">@{{ member.name }}{{ member.type === 'ai' ? ` · ${member.creator_name ?? '旧版预置AI'}` : '' }}</button>
              </li>
            </ul>
            <textarea
              ref="composerEl"
              v-model="draft"
              :disabled="!!activeRemoval"
              rows="2"
              placeholder="输入消息，Enter 发送，Shift + Enter 换行，@ 提及成员"
              aria-label="消息内容"
              @keydown="onKeydown"
              @keyup="trackCaret"
              @click="trackCaret"
              @input="trackCaret"
            />
            <button class="primary" :disabled="!!activeRemoval">发送</button>
          </form>
        </template>
      </template>
      <p v-else class="empty center">选择一个房间开始聊天</p>

      <p v-if="!connected" class="banner" role="status">连接已断开，正在重连</p>
      <p v-if="logoutError || notice" class="toast" role="status">{{ logoutError || notice }}</p>
    </main>

    <MembersPanel
      v-if="showMembers && activeRoom?.status === 'joined' && !activeRemoval"
      ref="membersPanel"
      :room-id="activeRoom.id"
      :me="user"
      :members="members"
      :request-event="requestEvent"
      @changed="loadMembers"
      @notice="notice = $event"
      @open-settings="showSettings = true"
      @close="showMembers = false"
    />

    <SettingsDialog
      v-if="showSettings"
      @close="showSettings = false"
      @changed="membersPanel?.loadAgents()"
    />

    <LeaveDialog
      v-if="leaving"
      :room="leaving.room"
      :is-last-human="leaving.isLastHuman"
      :busy="leaveBusy"
      @export="exportActive"
      @confirm="confirmLeave"
      @cancel="leaving = null"
    />
  </div>
</template>
