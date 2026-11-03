<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, shallowRef, watch } from 'vue'
import { api, ApiError } from '../lib/api.js'
import { AiDrafts } from '../lib/ai-drafts.js'
import { ChatSocket } from '../lib/chat-socket.js'
import { leave, prepareLeave } from '../lib/leave.js'
import { insertMention, matchingAis, mentionQuery } from '../lib/mentions.js'
import { RoomStream } from '../lib/room-stream.js'
import { fetchAllRooms } from '../lib/rooms.js'
import { exportRoom } from '../lib/transcript.js'
import LeaveDialog from './LeaveDialog.vue'
import MembersPanel from './MembersPanel.vue'
import SettingsDialog from './SettingsDialog.vue'

const ROOM_POLL_MS = 10000

const props = defineProps({ user: { type: Object, required: true } })
defineEmits(['logout'])

const rooms = ref([])
const activeId = ref(null)
const connected = ref(false)
const notice = ref('')
const newRoomName = ref('')
const inviteName = ref('')
const draft = ref('')
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

// AI replies still being generated, shown below the timeline.
const drafts = new AiDrafts()
const draftRevision = ref(0)

const streams = new Map()
// Messages not yet confirmed, by room: { clientMsgId, content, failed, throttled }.
const outbox = reactive(new Map())
const revision = ref(0)

const activeRoom = computed(() => rooms.value.find((r) => r.id === activeId.value) ?? null)

const timeline = computed(() => {
  void revision.value
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

const suggestions = computed(() => matchingAis(members.value, mentionQuery(draft.value, caret.value)))

const pending = computed(() => {
  const confirmed = new Set(timeline.value.map((m) => m.client_msg_id))
  return (outbox.get(activeId.value) ?? []).filter((m) => !confirmed.has(m.clientMsgId))
})

function streamFor(roomId) {
  if (!streams.has(roomId)) {
    streams.set(
      roomId,
      new RoomStream({
        fetchAfter: async (after) => (await api.history(roomId, { after })).messages,
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
  return streams.get(roomId)
}

const socket = new ChatSocket({
  onStatus: (up) => {
    connected.value = up
    if (!up) {
      drafts.clear()
      draftRevision.value++
    }
  },
  onMessage: (message) => {
    streamFor(message.room_id).receive(message)
  },
  onEvent: (frame) => onRoomEvent(frame),
  // Subscribing first and reading history second means nothing can fall
  // between the two.
  onSubscribed: (roomId) => loadRoom(roomId),
  onRoomLeft: (roomId) => forgetRoom(roomId),
  onSendResult: (roomId, clientMsgId, result) => {
    const box = outbox.get(roomId) ?? []
    const entry = box.find((m) => m.clientMsgId === clientMsgId)
    if (result.ok) {
      streamFor(roomId).sync()
      if (entry) outbox.set(roomId, box.filter((m) => m !== entry))
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
  const here = frame.room_id === activeId.value
  if (frame.type === 'ai_chunk') {
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
  if (!room || room.status !== 'joined') {
    members.value = []
    return
  }
  try {
    members.value = (await api.members(room.id)).members
  } catch {
    members.value = []
  }
}

function trackCaret(event) {
  caret.value = event.target.selectionStart ?? draft.value.length
}

async function chooseMention(member) {
  const result = insertMention(draft.value, caret.value, member.name)
  draft.value = result.text
  caret.value = result.caret
  await nextTick()
  composerEl.value?.setSelectionRange(result.caret, result.caret)
  composerEl.value?.focus()
}

async function loadRoom(roomId) {
  const stream = streamFor(roomId)
  try {
    if (stream.bySeq.size > 0) {
      await stream.sync()
    } else {
      const { messages } = await api.history(roomId)
      stream.addPage(messages)
      await stream.sync()
    }
  } catch {
    notice.value = '加载消息失败'
  }
}

async function loadOlder() {
  const stream = streamFor(activeId.value)
  const oldest = stream.messages[0]
  if (!oldest || oldest.seq <= 1) return
  const { messages } = await api.history(activeId.value, { before: oldest.seq })
  stream.addPage(messages)
}

const canLoadOlder = computed(() => timeline.value.length > 0 && timeline.value[0].seq > 1)

async function refreshRooms() {
  try {
    const list = await fetchAllRooms(api.rooms)
    rooms.value = list
    const joined = new Set(list.filter((r) => r.status === 'joined').map((r) => r.id))
    for (const id of joined) {
      if (!socket.rooms.has(id)) socket.subscribe(id)
    }
    for (const id of socket.rooms) {
      if (!joined.has(id)) forgetRoom(id)
    }
    if (activeId.value !== null && !list.some((r) => r.id === activeId.value)) {
      activeId.value = null
    }
    if (activeId.value === null && list.length > 0) activeId.value = list[0].id
  } catch (e) {
    if (e instanceof ApiError && e.status === 401) location.reload()
  }
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
    activeId.value = room.id
  } catch {
    notice.value = '创建房间失败，名称最多 64 个字符'
  }
}

async function joinRoom() {
  try {
    await api.join(activeId.value)
    await refreshRooms()
  } catch {
    notice.value = '加入失败，房间可能已被删除'
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
    notice.value = e.code === 'user_not_found' ? '找不到这个用户' : '邀请失败'
  }
}

function send() {
  const content = draft.value
  if (!content.trim() || !activeRoom.value) return
  if ([...content].length > 4000) {
    notice.value = '消息最多 4000 个字符'
    return
  }
  const clientMsgId = crypto.randomUUID()
  const roomId = activeId.value
  outbox.set(roomId, [...(outbox.get(roomId) ?? []), { clientMsgId, content, failed: false, throttled: false }])
  socket.send(roomId, clientMsgId, content)
  draft.value = ''
}

function retry(entry) {
  entry.failed = false
  entry.throttled = false
  socket.send(activeId.value, entry.clientMsgId, entry.content)
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
      notice.value = '退出失败，请重试'
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
  if (atBottom && messagesEl.value) {
    messagesEl.value.scrollTop = messagesEl.value.scrollHeight
  }
})

watch(() => [activeId.value, activeRoom.value?.status], loadMembers)

watch(notice, (text) => {
  if (text) setTimeout(() => notice.value === text && (notice.value = ''), 4000)
})

let poll
onMounted(() => {
  refreshRooms()
  poll = setInterval(refreshRooms, ROOM_POLL_MS)
})
onBeforeUnmount(() => {
  clearInterval(poll)
  socket.close()
})
</script>

<template>
  <div class="shell">
    <aside class="sidebar">
      <header class="sidebar-head">
        <strong>{{ user.username }}</strong>
        <button class="link" @click="showSettings = true">设置</button>
        <button class="link" @click="$emit('logout')">退出登录</button>
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
          @click="activeId = room.id"
        >
          <span class="room-name">{{ room.name }}</span>
          <span v-if="room.status === 'invited'" class="tag">待加入</span>
          <span v-else class="room-count">{{ room.member_count }} 人</span>
        </button>
        <p v-if="rooms.length === 0" class="empty">还没有房间。创建一个，或等待别人邀请你。</p>
      </nav>
    </aside>

    <main class="chat">
      <template v-if="activeRoom">
        <header class="chat-head">
          <h1>{{ activeRoom.name }}</h1>
          <template v-if="activeRoom.status === 'joined'">
            <form class="inline-form" @submit.prevent="invite">
              <input v-model="inviteName" placeholder="邀请用户名" aria-label="邀请用户名" />
              <button>邀请</button>
            </form>
            <button :class="{ active: showMembers }" @click="showMembers = !showMembers">
              成员 {{ members.length }}
            </button>
            <button @click="exportActive">导出</button>
            <button class="danger" @click="startLeave">退出房间</button>
          </template>
        </header>

        <div v-if="activeRoom.status === 'invited'" class="invite-panel">
          <p>你被邀请加入这个房间。</p>
          <button class="primary" @click="joinRoom">加入房间</button>
        </div>

        <template v-else>
          <div ref="messagesEl" class="messages" aria-live="polite">
            <button v-if="canLoadOlder" class="link older" @click="loadOlder">加载更早的消息</button>
            <p v-if="timeline.length === 0 && pending.length === 0" class="empty">还没有消息，说点什么吧。</p>
            <article
              v-for="message in timeline"
              :key="message.seq"
              class="message"
              :class="{ mine: isMine(message) }"
            >
              <div class="meta">
                <span>{{ message.member.name }}</span>
                <time>{{ formatTime(message.created_at) }}</time>
              </div>
              <p class="bubble">{{ message.content }}</p>
            </article>
            <article v-for="d in aiDrafts" :key="d.id" class="message pending">
              <div class="meta">
                <span>{{ d.name }}</span>
                <span>正在输入</span>
              </div>
              <p class="bubble">{{ d.text }}</p>
            </article>
            <article v-for="entry in pending" :key="entry.clientMsgId" class="message mine pending">
              <div class="meta">
                <span v-if="entry.failed" class="error">发送失败</span>
                <span v-else-if="entry.throttled">发送过快，稍后自动重试</span>
                <span v-else>发送中</span>
                <button v-if="entry.failed" class="link" @click="retry(entry)">重试</button>
              </div>
              <p class="bubble">{{ entry.content }}</p>
            </article>
          </div>

          <form class="composer" @submit.prevent="send">
            <ul v-if="suggestions.length" class="mention-list" role="listbox" aria-label="提及 AI">
              <li v-for="member in suggestions" :key="member.id">
                <button type="button" class="link" @click="chooseMention(member)">@{{ member.name }}</button>
              </li>
            </ul>
            <textarea
              ref="composerEl"
              v-model="draft"
              rows="2"
              placeholder="输入消息，Enter 发送，Shift + Enter 换行，@ 提及 AI"
              aria-label="消息内容"
              @keydown="onKeydown"
              @keyup="trackCaret"
              @click="trackCaret"
              @input="trackCaret"
            />
            <button class="primary">发送</button>
          </form>
        </template>
      </template>
      <p v-else class="empty center">选择一个房间开始聊天</p>

      <p v-if="!connected" class="banner" role="status">连接已断开，正在重连</p>
      <p v-if="notice" class="toast" role="status">{{ notice }}</p>
    </main>

    <MembersPanel
      v-if="showMembers && activeRoom?.status === 'joined'"
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
