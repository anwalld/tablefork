<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, shallowRef, watch } from 'vue'
import { api, ApiError } from '../lib/api.js'
import { ChatSocket } from '../lib/chat-socket.js'
import { RoomStream } from '../lib/room-stream.js'
import { fetchAllRooms } from '../lib/rooms.js'
import { exportRoom } from '../lib/transcript.js'
import LeaveDialog from './LeaveDialog.vue'

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

const streams = new Map()
// Messages not yet confirmed, by room: { clientMsgId, content, failed }.
const outbox = reactive(new Map())
const revision = ref(0)

const activeRoom = computed(() => rooms.value.find((r) => r.id === activeId.value) ?? null)

const timeline = computed(() => {
  void revision.value
  const stream = streams.get(activeId.value)
  return stream ? stream.messages : []
})

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
      }),
    )
  }
  return streams.get(roomId)
}

const socket = new ChatSocket({
  onStatus: (up) => (connected.value = up),
  onMessage: (message) => streamFor(message.room_id).receive(message),
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
})

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
  outbox.set(roomId, [...(outbox.get(roomId) ?? []), { clientMsgId, content, failed: false }])
  socket.send(roomId, clientMsgId, content)
  draft.value = ''
}

function retry(entry) {
  entry.failed = false
  socket.send(activeId.value, entry.clientMsgId, entry.content)
}

function onKeydown(event) {
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
    event.preventDefault()
    send()
  }
}

async function startLeave() {
  const room = activeRoom.value
  try {
    const { members } = await api.members(room.id)
    leaving.value = {
      room,
      isLastHuman: members.filter((m) => m.type === 'human').length === 1,
    }
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
    const { room, isLastHuman } = leaving.value
    await api.leave(room.id, isLastHuman)
    forgetRoom(room.id)
    leaving.value = null
    await refreshRooms()
  } catch (e) {
    if (e.code === 'confirmation_required') {
      // Everyone else left while the dialog was open.
      leaving.value = { ...leaving.value, isLastHuman: true }
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
            <article v-for="entry in pending" :key="entry.clientMsgId" class="message mine pending">
              <div class="meta">
                <span v-if="entry.failed" class="error">发送失败</span>
                <span v-else>发送中</span>
                <button v-if="entry.failed" class="link" @click="retry(entry)">重试</button>
              </div>
              <p class="bubble">{{ entry.content }}</p>
            </article>
          </div>

          <form class="composer" @submit.prevent="send">
            <textarea
              v-model="draft"
              rows="2"
              placeholder="输入消息，Enter 发送，Shift + Enter 换行"
              aria-label="消息内容"
              @keydown="onKeydown"
            />
            <button class="primary">发送</button>
          </form>
        </template>
      </template>
      <p v-else class="empty center">选择一个房间开始聊天</p>

      <p v-if="!connected" class="banner" role="status">连接已断开，正在重连</p>
      <p v-if="notice" class="toast" role="status">{{ notice }}</p>
    </main>

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
