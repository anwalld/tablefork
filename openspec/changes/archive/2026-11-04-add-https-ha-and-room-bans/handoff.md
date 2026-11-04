# 给 Codex 的交接：add-https-ha-and-room-bans

**最新收尾：先看第 8 节。CC 已补齐 2.4/2.8；本次 CI 通过、PR #8 已合并，正式任务 33/33。前面各轮未完成数字均是历史状态。**

最新验收补充：**请先看第 7 节（2026-11-04，Codex）**。用户已导入当前 CA 并要求 verify，故第 4 节“浏览器暂不做”已被用户后续指令更新；代码方案仍按第 0–5 节及用户已确认决定执行。

更新时间：2026-11-04，Claude 写。**本节是当前唯一有效的工作指令**。后面“给 Claude 的交接”是 Codex 上一轮的记录，只作背景参考；两者冲突时以本节为准。

## 0. 工作规则（必须遵守）

用户的原话：**不要遇到问题自己想 solution，直接告诉我。**

具体来说：

1. 只做本节第 3 部分列出的工作，按列出的顺序做。
2. 遇到以下任何一种情况，**立即停下，向用户报告**，等用户决定后再继续：
   - 测试失败，并且第一次检查看不出原因；
   - 本节没有写到、需要你自己做设计选择的地方（例如某个边界情况该怎么处理）；
   - 你认为本节的方案有错、行不通或者需要改；
   - 需要修改本节没有列出的文件；
   - 需要放宽、跳过、删除测试或断言，或者需要关闭证书校验；
   - 需要推送、提交、创建 PR、合并、归档。
3. 报告时写清楚：在做哪一步；看到了什么现象（命令、输出、日志路径）；已经确认了什么、还不知道什么。**不要提出替代方案并自行实施**。可以列出可能的方向，但要标明“未实施，等待决定”。
4. 不要用绕行办法让测试通过，例如加重试、拉长超时、把断言改宽松、跳过用例、限定协议版本。如果你觉得必须这样做，按第 2 条报告。
5. 不要提交、不要推送。工作区有大量未提交的实现，不要 reset、clean、stash 或覆盖它们。
6. 不要打印 `.env`、私钥、API key、会话 cookie。

## 1. 背景：为什么要改

上一轮 Codex 为了修一个真实的越权问题，在 `RoomHub::publish` 里加了“每条推送先查一次 MySQL 当前成员名单”（design D9a）。这个做法有两个问题：

- **开销和消息量成正比**（主要原因）。每条消息、每个 AI 片段都要查一次数据库，一次 AI 回复就可能是几十次查询。对账的开销只与在线连接数有关。
- **AI 片段到达 Redis 的顺序不再有保证**（次要）。`src/ai/ai_actions.cc:121` 对每个片段用 `drogon::async_run` 并发调用 `onChunk` → `publish`。以前 `publish` 一进来就发 Redis 命令，并发调用也会按发起顺序发出；现在要先 `co_await` 一次 MySQL 查询，而连接池有多条连接，完成的先后不固定。
  - **更正**：Claude 复核时一度说这会让用户看到错乱的文字，**这是错的**。AI 服务发出的片段全部是 `cumulative=True`，带 `sequence` 和完整快照（`ai/chat_ai/engine.py:294`）；前端 `web/src/lib/ai-drafts.js:19-20` 会丢弃 `sequence` 不大于当前值的片段，用快照覆盖。所以乱序的实际后果只是丢掉一些中间快照，流式显示可能一跳一跳，不会错乱。

越权问题本身是真的，需要保留防护：Redis 故障导致 `member_left` 丢失时，被踢的人在另一个实例上的旧订阅还会继续收到新消息。

**用户已经决定换成下面的方案。不要再讨论方案本身，有问题按第 0 部分报告。**

## 2. 用户确定的方案：定期对账 + 随机偏移 + 故障触发

### 2.1 总体

- 正常情况下仍然靠 `member_left` 立即撤权，不变。
- 对账只是兜底：每个实例定期用 MySQL 核对本实例所有“房间—成员”订阅，清掉已经没有成员关系的。
- **代价（用户已接受）**：Redis 故障期间，如果故障触发没有覆盖到，被移出的人最多还能多收约一个检查周期（默认 30 秒）的消息。

### 2.2 删除 D9a 的按消息过滤

`src/ws/room_hub.cc`：

- `RoomHub::publish`：恢复成原来的样子，也就是直接 `PUBLISH`，不解析 payload、不查 MySQL、不写 `only_member_ids`。删掉 `DrogonDbException` 的 catch。
- `RoomHub::onChannelMessage`：入口的判断恢复成原来的 `if (payload.find("\"type\":\"message\"") == std::string::npos)`，删掉 `|| payload.find("\"only_member_ids\"") != std::string::npos`。
- **保留**：
  - 订阅回调里的 `queueInLoop`（死锁修复）；
  - `removeMember` 的 `reason` 参数，以及 `member_left`、`room_left` 里的 `reason`；
  - `removeMember` 里新加的那条 `members_changed` 发布（在线成员要据此刷新成员列表）。

  修改后的顺序：同一实例先发 `member_left` 再发 `members_changed`，接收方用 `queueInLoop` 按先进先出处理，所以会先移除再刷新。

### 2.3 新增：对账 `RoomHub::reconcile()`

在 `src/ws/room_hub.h/.cc` 新增一个公开的 `drogon::Task<> reconcile();`，行为如下：

1. **防止重入**：用一个 `std::atomic<bool> reconciling_`。如果已经有一轮在跑，就把 `std::atomic<bool> reconcileAgain_` 设为 true 后直接返回；当前那一轮结束时，如果 `reconcileAgain_` 为 true，就清掉它并再跑一轮（循环，而不是递归）。
2. **取快照**：在 `mutex_` 下遍历 `rooms_`，收集去重后的 `(roomId, memberId)` 对，然后释放锁。快照为空就直接结束。
3. **分批查询**：每 500 对一批，**依次**执行（不并发），用参数化查询：
   ```sql
   SELECT room_id, member_id FROM room_members
   WHERE (room_id, member_id) IN ((?, ?), (?, ?), ...)
   ```
   快照里有、结果里没有的就是“已失去成员关系”。
4. **判断原因**：对所有失去成员关系的对，再查一次（同样每 500 对一批，依次执行）：
   ```sql
   SELECT b.room_id, m.id AS member_id FROM room_bans b
   JOIN members m ON m.user_id = b.user_id
   WHERE (b.room_id, m.id) IN ((?, ?), ...)
   ```
   查到的原因为 `banned`，其余为 `kicked`。对账时无法区分“被踢”和“自己在别的标签页退出”，**用户决定统一按 `kicked` 处理**：后者只在 Redis 故障期间才会走到对账，极少见，提示“你已被踢出”可以接受。
5. **执行移除**：对每一对调用现有的 `removeMemberLocally(roomId, memberId, reason)`。这一步在应用循环里执行（`reconcile` 本身由定时器或 `queueInLoop` 调用，已经在应用循环上）。只做本地移除，**不要**再 `PUBLISH member_left`。
6. **数据库出错**：捕获 `drogon::orm::DrogonDbException`，`LOG_WARN << "room reconcile skipped: " << e.base().what();`，放弃这一轮，等下一次。
7. **已知并接受的边界**：如果某人在取快照后被移出、又马上重新加入并订阅，这一轮可能会把他刚建立的订阅移除。客户端会收到 `room_left`，刷新后就能恢复。这种情况极少见，用户不要求处理，在 design 里记一句即可。

### 2.4 定期执行，带随机偏移（并进 SessionWatcher 的定时器）

`src/ws/session_watcher.cc` 的 `SessionWatcher::start()`：

- 现在的 `runEvery(Config::get().sessionCheckSeconds, ...)` 改为：先 `runAfter(随机延迟, ...)`，到点后再 `runEvery(sessionCheckSeconds, ...)`。随机延迟在 `[0, sessionCheckSeconds)` 内均匀分布，每个实例启动时取一次。
- 每次定时触发时，先 `co_await check()`，再 `co_await RoomHub::instance().reconcile()`，顺序执行，在同一个 `async_run` 里。
- 随机数用 `<random>`（`std::random_device` 作种子，`std::mt19937`，`std::uniform_real_distribution<double>`）。把“由周期和随机数生成器算出首次延迟”写成一个可测试的自由函数，例如 `double firstCheckDelay(double period, std::mt19937 &rng)`，声明在 `session_watcher.h`。
- 周期沿用 `SESSION_CHECK_SECONDS`（默认 30），**不新增配置项**。

### 2.5 故障触发

目标：Redis 一恢复，所有实例马上对账一次，不等下一个周期。

1. **记“脏”标记**：`RoomHub` 新增公开方法 `void markRedisTrouble();`，把一个 `std::atomic<bool> redisTrouble_` 设为 true。以下两处调用它：
   - `RoomHub::publish` 捕获到 `drogon::nosql::RedisException` 时；
   - `SessionWatcher::check()` 捕获到 `RedisException`（现在打 `session check skipped` 的地方）时。
2. **新增全局频道**：常量 `RoomHub::kReconcileChannel = "rooms:reconcile"`。
3. **探测并广播**：`RoomHub` 新增 `void start();`，在 `src/main.cc` 的 `registerBeginningAdvice` 里调用，放在 `SessionWatcher::instance().start()` 之前。它做两件事：
   - 用 `RoomHub` 现有的 `subscriber()` 订阅 `rooms:reconcile`。回调里**必须**用 `drogon::app().getLoop()->queueInLoop(...)`，再用 `drogon::async_run` 调用 `reconcile()`（原因同死锁修复：不能在 Drogon 持锁的回调里动订阅）。
   - `runEvery(1.0, ...)`：每秒检查一次 `redisTrouble_`。为 false 就什么都不做；为 true 就执行 `PUBLISH rooms:reconcile 1`：
     - **成功**：把 `redisTrouble_` 清为 false，并在本实例 `co_await reconcile()`。本实例也会通过订阅收到这条广播，再对账一次，这没关系，有重入保护。
     - **失败**：保持为 true，下一秒再试。

   注意：用“PUBLISH 是否成功”判断 Redis 是否恢复，**不要用 PING**。测试里用 ACL 禁掉 PUBLISH 时 PING 仍然成功，用 PING 会误判已恢复。
4. 别的实例即使自己没遇到 Redis 错误（例如只有 PUBLISH 被禁），也会通过广播及时对账。

### 2.6 前端：被踢或被封后的显示（用户决定）

用户的原话：**被踢后还能看见之前的消息，只不过前端显示一个“你已被踢出”。**

现状：`web/src/components/ChatView.vue:109-122` 的 `onRoomLeft` 收到 `kicked` 或 `banned` 时，会把房间从列表里删掉、退出当前房间，并调用 `forgetRoom` 丢弃已加载的消息。这与用户的要求相反，要改成下面的行为。

收到 `room_left` 且 `reason` 为 `kicked` 或 `banned` 时：

1. **房间留在列表里**，不删除，并在本地标记为“已被踢出”或“已被封禁”。列表项上显示对应的状态文字（“已被踢出”或“已被封禁”），样式参照现有“待加入”状态的写法。
2. **已加载的消息保留**，不要丢弃。停止该房间的订阅和消息流跟踪，但不要清空已经显示的消息。如果 `forgetRoom` 会清掉消息，就把“停止跟踪”和“清掉消息”拆开，这里只做前者。
3. 如果当前正打开这个房间：
   - 留在这个房间，继续显示已有消息；
   - 在消息区顶部（或输入框位置）显示提示：`kicked` 为“你已被踢出房间 X”，`banned` 为“你已被禁止进入房间 X”（X 是房间名）；
   - 禁用输入框和发送按钮；
   - 关闭成员面板，并且不再请求该房间的成员、历史或封禁列表（这些接口会返回 404）。
4. 如果被踢时没有打开这个房间，之后用户点开它：显示本地已有的消息（可能为空）和同样的提示，同样不请求接口。
5. **刷新房间列表时**（`ChatView.vue:214` 附近的那段逻辑），服务端返回的列表里已经没有这个房间了，但要**保留**本地标记为已被踢出或已被封禁的房间，不能被刷新冲掉。如果服务端列表里重新出现了同一个房间 id（例如被重新邀请，状态是待加入），就以服务端为准：清除本地的被踢标记，按正常房间处理。
6. 这些状态只保存在内存里，刷新页面后就消失（服务端不会再返回这个房间），这是预期行为。
7. `reason` 为 `left` 或没有 `reason` 时，保持现在的行为不变。

文案要求：把现在的“你已被移出房间 X”改成“你已被踢出房间 X”；“你已被禁止进入房间 X”保持不变。

### 2.7 前端：删除 ai-drafts.js 的死代码（用户决定）

AI 服务只发 `cumulative=True` 的片段（`ai/chat_ai/engine.py:297` 是唯一发片段的地方），所以 `web/src/lib/ai-drafts.js` 的 `chunk()` 里，`if (frame.cumulative) { ... return }` 之后的非累积分支（`if (current?.attempt > 0) return` 和 `draft.text += text` 那几行）永远走不到。删掉这条分支，`chunk()` 只保留累积处理。`web/src/lib/ai-drafts.test.js` 里只测非累积行为的用例一起删掉，测累积行为的保留。

如果你发现还有别的地方会发出非累积片段（例如 C++、proto 或测试辅助代码里），就停下来报告，不要删。

### 2.8 被踢前刚发的消息：发送者自己也要能看到（用户决定）

**场景**：发消息和封禁都锁同一行 `rooms`（`message_service.cc:156` 的 `FOR UPDATE`，以及封禁的 `lockRoom`），所以两者一定先后执行。

- 封禁先执行：发消息时已不是成员，被拒绝，不写库、不占 seq。这与现有规格一致，**不用改**。
- 发消息先执行：消息合法地写入 MySQL，其他成员正常收到，历史里也有，**不用改**。但发送者这边有问题：
  - `ack` 是存库后直接在他的连接上发的（`src/ws/chat_socket.cc:95-102`），所以他能收到；
  - 前端收到 `ack` 后，只是把消息从“发送中”列表移除，再调用 `stream.sync()`，靠推送或补齐历史来显示它（`web/src/components/ChatView.vue:127-129`）；
  - 可是 `member_left` 可能先到，他的订阅已经撤了，收不到推送；补齐历史又因为已不是成员而返回 404。

  结果是他最后发的那条消息从自己屏幕上消失，而其他人都能看到。这与 2.6 的“被踢后还能看到之前的消息”冲突。

**改法**（用户已确认，不要换成别的做法）：`ack` 的格式**不变**，服务端**不改**。前端用手里已有的信息拼出本地版本先显示，服务端的版本一到就替换它。

用户否决了“在 `ack` 里带完整消息”的做法：那样每条消息都会给发送者多发一份内容，而只有“刚好被踢”这种极少见的情况才用得上。

1. **前端拼本地版本**：`ChatView.vue` 的 `onSendResult` 成功时，用下面的信息拼一条消息，交给 `streamFor(roomId).receive(...)`，然后再从“发送中”列表移除，最后才 `sync()`：
   - `type: 'message'`，`room_id: roomId`；
   - `seq`、`created_at`：取自 `ack`。`chat-socket.js:52` 现在只把 `seq` 放进结果，要改成 `{ ok: true, seq: frame.seq, created_at: frame.created_at }`；
   - `client_msg_id`、`content`：取自“发送中”列表里的那一项；
   - `member: { id: props.user.member_id, name: props.user.username, type: 'human' }`；
   - `local: true`：标记这是本地拼出来的。

   如果“发送中”里找不到这一项（例如页面在这期间被刷新过），就不拼，保持现在的行为。
2. **服务端版本优先**：`web/src/lib/room-stream.js` 的 `receive`（以及 `addPage`、`fetchMissing` 写入 `bySeq` 的地方）遇到已存在的 `seq` 时：
   - 已有的那条带 `local: true`，就用新来的服务端版本替换它，并触发 `onChange`；
   - 否则保持现在的行为，忽略后来的版本。

   这样正常情况下，本地版本先显示，推送或历史一到就被服务端版本替换，用户看不出区别；被踢时推送不会来，本地版本留在屏幕上。
3. **被踢或被封的房间不再补齐历史**：创建 `RoomStream` 时传入的 `fetchAfter`，对已标记为被踢或被封的房间（2.6），直接返回空数组，不发请求，不报错。其他房间不变。

**已知并接受的边界**（不处理，在 design 里记一句即可）：如果他的 `ack` 恰好丢了，前端重连后重发，服务端会先检查成员关系再去重，发现他已不是成员，于是返回 `room_not_found`，前端显示“发送失败”，而这条消息其实已经保存、其他人也能看到。这需要“`ack` 丢失”和“被踢”同时发生，极少见。

### 2.9 被踢前 @ 了 AI 的消息（用户决定：照常回复）

消息先于封禁存库时，存库之后才通知 AI 服务（`chat_socket.cc` 里 `distribute` 之后的 `reportMessage`），所以 AI 会照常回复，并且按“调用费用由触发者承担”，用这个人的 API key 计费。**用户决定保持现状，照常回复，不要加任何检查。**只需要在 design 的 Risks 里记一句：被踢或被封前刚发出的 @AI 消息仍会得到回复，费用仍由触发者承担。

### 2.10 注释

- `RoomHub::reconcile` 和 `start` 的声明处写清楚：对账是 `member_left` 丢失时的兜底；Redis 正常时撤权仍然是即时的。
- 原 D9a 相关的代码注释（“Derive data recipients from MySQL…”）随代码一起删掉。

## 3. 要做的工作（按顺序）

### 3.1 代码

按第 2 部分修改：
- 后端（2.2–2.5、2.10）：`src/ws/room_hub.h`、`src/ws/room_hub.cc`、`src/ws/session_watcher.h`、`src/ws/session_watcher.cc`、`src/main.cc`；
- 前端（2.6、2.7、2.8）：`web/src/components/ChatView.vue`、`web/src/lib/ai-drafts.js`、`web/src/lib/chat-socket.js`、`web/src/lib/room-stream.js`。2.8 不改服务端，`src/ws/chat_socket.cc` 不要动。如果实现 2.6 需要改其他前端文件（例如房间列表项的组件、`rooms.js`、`room-stream.js`），可以改，但要在“本轮结果”里列出来。

顺带改一个复核时发现的小问题：`src/services/room_service.cc` 里的 `RoomService::bans()` 是只读查询，却调用了 `lockRoom(tx, roomId)`，会和同一房间的写操作争锁。删掉这一行 `lockRoom`，其他不变。

### 3.2 测试

1. **C++ 单元测试**：给 `firstCheckDelay` 加测试，放在 `tests/unit/session_watcher_test.cc`。用固定种子跑 1000 次，每次都满足 `0 <= delay < period`，且不是每次都相同。
2. **修改** `tests/integration/test_room_bans.py` 里的两个测试：`test_lost_removal_notification_cannot_leak_new_messages` 和 `test_lost_removal_cannot_leak_ai_chunks`。在恢复 `+publish` 之后、`writer.post(...)` 之前，加一步：
   ```python
   left = reader.expect("room_left", timeout=5)
   assert left["room_id"] == room
   assert left["reason"] == ("banned" if action == "ban" else "kicked")
   ```
   其余断言不变：之后 reader 不再收到任何该房间的数据事件。
3. **新增**两个测试，验证没有 Redis 错误时的定期对账路径，放在 `tests/integration/test_room_bans.py`。这个文件里还没有 `short_check`，照 `tests/integration/test_sessions.py:20` 的写法定义同样的 `pytest.mark.skipif(not os.environ.get("SHORT_SESSION_CHECK"), ...)`，用它标记这两个测试。第一轮会传 `SHORT_SESSION_CHECK=1`，并设 `SESSION_CHECK_SECONDS=1`：
   - `test_reconcile_removes_subscription_without_notification`：reader 在实例 2 订阅，writer 在实例 1 订阅。用 `db` 夹具直接执行 `DELETE FROM room_members WHERE room_id = %s AND member_id = %s` 删掉 reader 的成员关系（不经过接口，所以没有 `member_left`）。断言 3 秒内 reader 收到 `room_left`，`reason == "kicked"`。之后 writer 发一条消息，reader 在 0.5 秒内收不到。
   - `test_reconcile_reports_bans`：同上，但先 `INSERT INTO room_bans (room_id, user_id) VALUES (...)`，再删成员关系。断言 `reason == "banned"`。

   直接写库会绕过应用的缓存，所以测试结束前要用接口或 `db` 清理，确保不影响其他测试。成员 id 和用户 id 用现有夹具或接口取得；取不到就按第 0 部分报告。
4. **故障触发要在长周期下验证**：第一轮的周期是 1 秒，掩盖了故障触发路径。在 `scripts/test.sh` 第二轮（`SESSION_CHECK_SECONDS` 为默认的 30）的那条 `pytest -q` 命令后，加上 `test_room_bans.py::test_lost_removal_notification_cannot_leak_new_messages`（两个参数化用例都要跑）。第 2 步里的 5 秒超时远小于 30 秒，能证明是广播触发的对账，而不是定期对账。
   - 第二轮的测试命令带 `-e AI_TESTS=1`，AI 那个测试也可以一起加，但它依赖的假模型配置以第二轮为准。如果在第二轮失败，报告，不要自行调整。
5. **被踢前刚发的消息（2.8）**：服务端不改，所以不新增服务端的消息格式测试。在 `tests/integration/test_room_bans.py` 加一个跨实例用例，在第一轮跑：被踢者在实例 2 上发一条消息并收到 `ack` 之后，房主立刻经实例 1 踢他；断言其他成员的历史里有这条消息，`seq` 与 `ack` 一致。发送者界面上的显示由第 7 步的前端单元测试覆盖。
6. **AI 片段顺序回归**：删除按消息查询后，片段会恢复按发起顺序发布。在 `tests/integration/test_ai_replies.py` 里新增一个测试：连续触发 20 次正常回复，每次收集同一个 `generation_id` 的所有 `ai_chunk`，断言：
   - 收到的 `sequence` 严格递增；
   - 最后一个片段的 `snapshot` 等于最终消息的内容。

   不要断言片段个数：AI 服务会按 `chunk_interval` 合并片段，开头还有一个空片段（`engine.py:299`）。如果观察到的字段与这里描述的不一致，就报告，不要改假模型或前端。
7. **前端单元测试**（`web/src/components/ChatView.test.js`）：现有测试按旧行为写的（例如第 44 行期望“你已被移出房间 测试房间”，并期望房间被移除），按 2.6 改写，覆盖：
   - 正在打开的房间收到 `kicked`、`banned`：房间仍在列表中并带状态文字；已显示的消息仍在；提示文案正确；输入框被禁用；之后没有再调用成员、历史接口；
   - 未打开的房间收到 `kicked`，之后点开：显示提示，不调用接口；
   - 刷新房间列表后，被踢的房间仍然保留；服务端列表重新出现同一个 id 时，被踢标记被清除；
   - `left` 和没有 `reason` 时行为不变；
   - （2.8）收到成功的 `ack` 后，即使没有推送、补齐也失败，这条消息仍然以本地版本显示，内容、`seq`、发送者正确；
   - （2.8）之后同一 `seq` 的推送到达，本地版本被服务端版本替换，只显示一条；
   - （2.8）被踢的房间出现 `seq` 缺口时，不调用历史接口。

   `room-stream.test.js` 补用例：`local: true` 的消息会被同一 `seq` 的服务端消息替换（`receive`、`addPage`、`fetchMissing` 三条路径都要测）；非本地的消息不会被替换。`chat-socket.test.js` 补一个用例：`ack` 帧的 `created_at` 会传给 `onSendResult`。`ai-drafts.test.js` 按 2.7 调整。运行 `npm test` 和 `npm run build`，都要通过。
8. 跑完整的 `bash scripts/test.sh`，把输出重定向到 `/private/tmp/cppai-reconcile-full.log`，退出码写到同名 `.exit` 文件。全部通过才算完成。

### 3.3 文档

1. `design.md`：把 **D9a** 整节替换为下面的内容（可以润色措辞，但不能改变意思）：

   > ### D9a. 移除通知丢失时的兜底：定期对账
   >
   > - 问题：Redis 故障导致 `member_left` 丢失时，其他实例上被移出者的订阅会残留，Redis 恢复后会继续收到房间的新消息。
   > - 正常情况下仍由 `member_left` 即时撤权。另外，每个实例定期用 MySQL 核对本实例的所有“房间—成员”订阅：快照去重，每 500 对一批，依次查询 `room_members`；失去成员关系的再查 `room_bans` 判断原因（`banned`，否则 `kicked`；对账分不出被踢和自己退出，统一按被踢处理），然后只在本地移除并发送 `room_left`。
   > - 周期沿用 `SESSION_CHECK_SECONDS`，与会话检查在同一个定时器里顺序执行。每个实例启动时在 `[0, 周期)` 内随机延迟首次执行，尽量错开各实例的对账时刻。有重入保护，上一轮未结束时只记一次“再跑一轮”。
   > - 故障触发：实例在发布或会话检查遇到 Redis 错误时记“脏”标记，此后每秒尝试 `PUBLISH rooms:reconcile`；成功即清除标记，所有实例收到广播后立即对账。用 PUBLISH 是否成功判断恢复，因为 PING 成功不代表能发布。
   > - 曾经的方案是在每次推送前查询成员名单。它会让并发发布的 AI 片段乱序，开销也随消息量增长，因此弃用。
   > - 代价：故障触发没能覆盖到的情况（例如只有某个实例的订阅连接被单独断开，而普通命令正常），撤权最多延迟一个周期。取快照后被移出又立即重新加入的用户，可能被误移除一次，刷新即可恢复。

   同时检查 design 里其他提到“`only_member_ids` 过滤”“每条推送查询成员”的地方（D9、Risks），改成与新方案一致。
2. `specs/chat-rooms/spec.md`：在“踢出成员”下的 `被踢者的在线连接` 场景之后，新增一个场景（原场景不改）：
   ```
   #### Scenario: 移出通知丢失
   - **WHEN** 被移出者的连接所在实例因 Redis 故障没有收到移出通知
   - **THEN** 该实例在 Redis 恢复后，或者最迟在一个会话检查周期内，向该连接发送 `room_left`，此后不再推送该房间的消息
   ```
3. `specs/high-availability/spec.md`：在“聊天服务多实例”下的 `跨实例移出房间` 场景之后，新增：
   ```
   #### Scenario: 跨实例移出通知丢失
   - **WHEN** 移出房间时 Redis 发布失败，被移出者的连接在另一实例上
   - **THEN** Redis 恢复后，该连接收到 `room_left`，不再收到该房间的推送
   ```
4. `tasks.md`：在第 4 组末尾新增一项，**做完并验证后才勾选**：
   ```
   - [ ] 4.6 按 design D9a 用定期对账（随机偏移、故障触发）替换按消息查询成员；删除 publish 中的成员查询。验证：`test_room_bans.py` 的通知丢失用例在第一轮和第二轮（周期 30 秒）都在 5 秒内收到 `room_left`；定期对账两个用例通过；AI 片段 `sequence` 递增的回归通过；`scripts/test.sh` 全部通过。
   ```
5. `design.md` 的 **D12**：把“收到带原因的 `room_left`（`kicked` 或 `banned`）时：从房间列表中移除这个房间……”那一条，改成 2.6 描述的行为：房间留在列表并标记状态，保留已有消息，打开时显示“你已被踢出房间 X”或“你已被禁止进入房间 X”，禁用输入、不再请求接口，刷新列表不冲掉，服务端重新返回同一房间时以服务端为准。另外加三条：`ai-drafts.js` 只处理累积片段；收到 `ack` 后前端先用本地拼出的版本显示自己发的消息，服务端版本到达后替换它（2.8）；被踢或被封的房间不再补齐历史。同时在 Risks 里记下 2.8 的“`ack` 丢失后重发显示失败”和 2.9 的“被踢前刚 @ 的 AI 照常回复、费用仍由触发者承担”。
6. `tasks.md` 的 5.6：描述里“带原因的 `room_left` 提示”后面补上“（房间保留在列表、保留已有消息、禁用输入）”。5.6 本来就未勾选（缺浏览器验收），保持未勾选。
7. `README.md`：第 257–273 行的吞吐数据是加入按消息查询之前测的。按消息查询删掉后，这些数据重新适用，**不需要改**。如果你发现 README 别处提到了按消息过滤，按新方案改掉。
8. `verification-report.md`：W1 和 SUGGESTION 里关于按消息查询的内容已经过时。全部做完后，重新执行一次 verify（`.agents/skills/openspec-verify-change/SKILL.md`），整份报告重写。

### 3.4 完成后

1. `openspec validate add-https-ha-and-room-bans --strict` 和 `git diff --check` 都要通过。
2. 在本节末尾追加一段“本轮结果”，写清楚：改了哪些文件；每个测试的结果和日志路径；勾选数（加上第 5 部分的 3.8 后应为 29/33，其中 2.4、2.8、5.6、6.2 仍未完成，原因不变）；遇到并报告给用户的问题。
3. 不要提交，不要推送，交给用户。

## 4. 仍然不做的事

- 浏览器验收（2.4、2.8、5.6）：用户暂不导入本地 CA，不要催，也不要绕过证书警告。
- 推送和 CI（6.2）：需要用户明确授权。
- 同步 Python 客户端只用 TLS 1.2 的限制（W3）：保持现状，这一轮不处理。
- Redis 切换窗口内的业务断言（W2）：这一轮不做。等本轮完成后，由用户决定是否做。

## 5. 追加（2026-11-04 下午，Claude 写）：HAProxy 改为以 Sentinel 为准

### 5.1 当前进度（以此为准）

- 第 2 部分的代码已由 Codex 实现（对账、随机偏移、故障触发、前端 2.6–2.8），第一轮 247 passed / 24 skipped，前端 76 项和构建通过。
- 完整回归 `/private/tmp/cppai-reconcile-full.log` 失败，退出码 1：`test_failover.py::test_prepare_redis_failover` 登录返回 503。Redis 停机、app 和 L7 停机阶段以及第二轮都还没跑到。
- 第二轮的 kick/ban 用例已经按用户授权放进独立的来源容器（`scripts/test.sh:134` 起），但还没有跑到那一步。
- 3.3 的文档同步（design D9a/D12、两份规格、tasks 4.6 和 5.6）**还没做**。

### 5.2 503 的根因（Claude 核对日志确认）

Codex 的判断是：准备步骤的就绪条件不够，HAProxy 还没把旧节点摘掉。这一点没错，但还有更严重的问题。证据在 `/private/tmp/cppai-reconcile-503-services.log` 和 `/private/tmp/cppai-reconcile-503-redis.log`：

```
07:40:54  redis-1 被提升为主节点（redis-1 日志 "1:M"）
07:40:55  HAProxy 把 redis-1 标为 UP，"2 active"：redis-2 仍然 UP
          新连接在两边轮流分配：07:41:00 -> redis-1，07:41:03 -> redis-2
07:41:05  redis-2 才执行 REPLICAOF，降为从节点（"1:S"）
07:41:10  redis-2 全量同步，"Flushing old data"；HAProxy 这时才把它标为 DOWN
```

- 07:40:54–07:41:05 共 11 秒，**两个节点都是主节点**，HAProxy 也都认它们可用，写入被分到两边。`redis-2` 随后全量同步并清空了旧数据，这段时间写到它上面的数据（会话、计数等）全部丢失。
- 07:41:05–07:41:10，`redis-2` 已经是只读的从节点，但 HAProxy 还在把连接交给它，写入报错，登录返回 503。
- 根本原因：`docker/haproxy.cfg` 的检查只问每个节点“你是不是主节点”（`expect string role:master`），两个都回答“是”，它就都认。原主节点停机后重启时，会先以主节点身份启动，直到 Sentinel 把它降级为止，同样会出现这个窗口。这违反 high-availability 规格“原主节点恢复”场景里“不会再接收写入”。

### 5.3 用户决定的改法：HAProxy 只留一个后端条目，地址跟着 Sentinel 改

**为什么不是“每个节点各自检查”**：Claude 最初的方案是保留 `redis-1`、`redis-2` 两个后端，让检查去问 Sentinel 多数派。用户指出这仍然不行：HAProxy 对每个后端是各自独立检查的，新主节点那一轮检查通过就 UP，旧节点要到它自己的下一轮检查才 DOWN，中间仍会两个都 UP。所以最终方案是：**后端只留一个条目，任何时刻新连接只有一个去处。**

这里的“一个条目”只是 HAProxy 配置里的一行 `server`，Redis 仍然是两个节点加 3 个 Sentinel。在 Sentinel 架构下，同一时刻本来就只有一个节点能写，我们也不做读写分离，所以只需要一个指向“当前主节点”的条目。`app-1`、`app-2` 和 AI 服务都连同一个 `redis-proxy`，地址一改，所有实例同时生效。

具体改法如下。

1. **`docker/haproxy.cfg`**：
   - `backend redis_master` 里只保留一行：`server master "${REDIS_MASTER_HOST}:6379"`，沿用现有 `default-server` 的 `resolvers docker`、`init-addr` 和 `on-marked-down shutdown-sessions`；检查参数改为 `inter 1s fall 1 rise 1`。删掉 `server redis-1` 和 `server redis-2` 两行。
   - 健康检查保留现在的 tcp-check（AUTH → PING → `INFO replication` 里要有 `role:master` → QUIT），密码仍用 `REDIS_AUTH_HEX`。它现在只负责判断“这个地址上的节点活着、而且是主节点”，不再参与选择节点。
   - `global` 里加两个管理端口：
     - `stats socket ipv4@127.0.0.1:9998 level admin`：只监听容器内的回环地址，给下面的地址切换脚本用；
     - `stats socket ipv4@0.0.0.0:9999 level user`：只读，给测试读取状态用。compose 里**不要**发布这两个端口。
2. **启动时确定初始主节点**：`scripts/redis-proxy.sh` 在启动 HAProxy 之前，先按下面第 3 步的多数派规则查询 Sentinel，得到当前主节点的主机名，导出为 `REDIS_MASTER_HOST`，再启动 HAProxy。查不到多数派就每秒重试，一直等到查到为止；这期间容器的健康检查不通过，依赖它的服务不会启动。
3. **新增 `scripts/redis-master-watch.sh`，在 `redis-proxy` 容器里常驻**：
   - 由 `redis-proxy.sh` 在后台启动（`&`），把 PID 写到 `/tmp/redis-master-watch.pid`，然后再 `exec` HAProxy。
   - **每 0.5 秒**做一次多数派查询：分别向 `sentinel-1`、`sentinel-2`、`sentinel-3` 的 26379 端口发送 `AUTH` 和 `SENTINEL get-master-addr-by-name chat`，用 `nc -w 1` 发 RESP 协议命令，写法与 `redis-proxy.sh` 一致；联系不上或回复错误的跳过。**至少 2 个 Sentinel 回答同一个主机名**，才算查到。
   - 查到的主机名与当前路由的不同时，依次向 `127.0.0.1:9998` 发送下面两条命令，然后更新脚本里记录的“当前路由”：
     1. `set server redis_master/master fqdn <新主机名>`
     2. `shutdown sessions server redis_master/master`

     顺序不能反：先改地址，让新连接去新主节点，再断开旧连接。每次切换打一行日志，写明从哪个节点切到哪个节点，不打印密码。
   - **查不到多数派时什么都不做**，保持当前路由。地址只在多数派明确指定时才改。如果当前路由的节点确实挂了，健康检查会把它判为 DOWN，Redis 暂时不可用，应用返回可重试的错误。（这一条替换 Claude 之前写的“达不到多数就全部判为 DOWN”。）
   - 用户确认过的方案里还有“订阅 Sentinel 的 `+switch-master` 事件”。这里**只用 0.5 秒轮询**，不做订阅：用 shell 和 `nc` 维持订阅连接，还要处理断线重连，复杂且容易出错；轮询最多只慢 0.5 秒，远在 30 秒的要求之内。这一点 Claude 会告诉用户。
4. **密码**：`redis-master-watch.sh` 是 `redis-proxy.sh` 启动的子进程，能直接继承 `REDIS_PASSWORD` 环境变量，用现有的 `auth_command` 生成 AUTH 命令即可。不要把密码写进命令行参数、文件、日志或 HAProxy 配置。
5. **健康检查**：`redis-proxy.sh health` 在现有的 PING 检查之外，再检查 `kill -0 "$(cat /tmp/redis-master-watch.pid)"`。地址切换脚本退出了，容器就判为不健康，由 `restart: unless-stopped` 重启。
6. 删掉 Claude 上一版写的外部检查方案（`external-check`、`redis-master-check.sh`、`/tmp/redis-auth`），这些**不要**实现。

**能保证什么，不能保证什么**（写 design 和规格时以此为准，不要写得更强）：
- 原主节点重启后，以主节点身份启动、到被 Sentinel 降级为止的这段时间里，HAProxy 不会把连接交给它，因为地址只跟 Sentinel 多数派走。
- 任何时刻，新连接只去一个节点。
- 剩下的窗口：改地址和断开旧连接是两条命令，中间有几毫秒，旧连接还可能往旧主节点写。受控切换（`SENTINEL FAILOVER`）时，旧主节点在被降级前仍可写，这几毫秒的写入可能丢失。这与 design 已经接受的“异步复制下切换时最后几毫秒的写入可能丢失”是同一类风险。不使用 `min-replicas-to-write`，原因不变：只有一个从节点，从节点一挂，主节点就会拒绝所有写入。
- `redis-proxy` 只有一个容器，是已知单点，跟 L4 网关一样。不要开第二个：两个代理各自切换，时刻会有先后，又会出现一个指向旧主、一个指向新主的窗口。

### 5.4 测试

1. **准备步骤的就绪条件**：`test_failover.py::test_prepare_redis_failover` 在现有的等待条件之后，再等到 `redis-proxy:9999` 的 `show servers state` 里，`master` 的地址等于 `socket.gethostbyname("redis-1")`，并且处于 UP，才开始创建用户。这是准备步骤，等待写法沿用现有的 `wait_for`。故障恢复的 30 秒要求保持不变。
2. **“不再写入原主节点”的回归**：判断依据是原主节点上有没有来自 `redis-proxy` 的客户端连接，即 `CLIENT LIST` 里 `addr` 的 IP 等于 `socket.gethostbyname("redis-proxy")` 的连接。应用和 AI 服务都只经过 `redis-proxy`，所以这正好代表“应用的连接”。Sentinel 和复制链路的连接来自别的 IP，不计入。
   - **原主节点重新加入（`rejoined` 阶段）**：`docker compose start redis-1` 之后，每 200 毫秒直连 `redis-1` 执行一次 `CLIENT LIST`，直到它报告 `role:slave`。断言整个过程中，它上面**从来没有**来自 `redis-proxy` 的连接。
   - **受控切换（准备步骤里的 `SENTINEL FAILOVER`）**：从观察到 `redis-proxy` 的路由切到 `redis-1` 起，1 秒内 `redis-2` 上来自 `redis-proxy` 的连接数必须降到 0，并且直到 `redis-2` 报告 `role:slave` 都保持为 0。
   - 删掉 Claude 上一版写的“每 200 毫秒采样 HAProxy，UP 不超过 1 个”的测试：只有一个条目时，这个断言永远成立，没有意义。
3. **地址切换脚本单独验证**：全部服务启动后，停掉 `sentinel-2` 和 `sentinel-3`，确认路由不变、日志里没有切换记录；恢复后一切正常。再停掉主节点，确认 0.5 秒级别内日志出现切换记录，路由指向新主节点。把命令和输出（不含密码）记在“本轮结果”里。
4. 修改后重跑完整的 `bash scripts/test.sh`，输出写到 `/private/tmp/cppai-sentinel-check-full.log`，退出码写到同名 `.exit`。

### 5.5 文档

1. `design.md` 的 **D7**：HAProxy 那部分改为 5.3 的方案，写清楚：
   - 后端只有一个条目，地址由常驻脚本按 Sentinel 多数派每 0.5 秒核对并切换，先改地址再断开旧连接；
   - 健康检查只判断该地址的节点是否存活并且是主节点；
   - 查不到多数派时保持当前路由；
   - 为什么不用“两个后端各自检查”：独立检查无法同时切换两个状态。另外记下 2026-11-04 测试中观察到的 11 秒双主作为原因；
   - 5.3 末尾“能保证什么，不能保证什么”的全部内容。

   删掉原来“两个判断独立，切换瞬间可能看到两个主节点”的说法。
2. `design.md` 的 **Risks**：
   - 删掉或改写“网络分区时可能出现两个主节点，HAProxy 会把连接分给两边”那一条，改为“改地址与断开旧连接之间的毫秒级窗口内，旧连接仍可能写入旧主节点”；
   - 新增一条：“`redis-proxy` 是单点，靠自动重启恢复；不开第二个代理，以免两个代理切换时刻不一致。”
3. `specs/high-availability/spec.md` 的“Redis 主节点故障自动切换”下，新增一个场景（原场景不改）：
   ```
   #### Scenario: 切换后不再写入原主节点
   - **WHEN** 主节点切换完成，或原主节点恢复后重新加入
   - **THEN** 聊天服务和 AI 服务的新连接只转到当前主节点，原有连接被断开后重连到当前主节点，原主节点不再收到它们的写入
   ```
4. `tasks.md` 第 3 组末尾新增一项，做完并验证后才勾选：
   ```
   - [ ] 3.8 HAProxy 改为单一后端条目，由常驻脚本按 Sentinel 多数派切换地址并断开旧连接，消除切换时的双主路由。验证：原主节点重新加入期间没有来自 redis-proxy 的连接；受控切换后 1 秒内旧主节点上的代理连接降为 0；Sentinel 失去多数时路由不变；`scripts/test.sh` 全部通过。
   ```
   加上这一项和 3.3 里的 4.6 之后，总数变为 33 项；完成后勾选数应为 29/33。2.4、2.8、5.6、6.2 仍未完成，原因不变。

### 5.6 做的顺序

1. 先做本节的 5.3 和 5.4，跑通完整回归。
2. 再做 3.3 的文档同步，以及本节的 5.5。
3. 最后做 3.4，在“本轮结果”里同时汇报第 2 部分和本节的工作。

第 0 部分的规则同样适用：遇到问题停下来报告，不要自己另想办法。

## 6. 本轮结果（2026-11-04，Codex）

**本节记录最新结果；第 0 节工作规则继续适用。旧交接与旧 implementation-notes 中的按消息查询、双后端和失败状态只作历史，不代表当前实现。给 Claude 的复核要求：让他double check。**

### 已完成

- 第 2 部分：定期批量对账、随机首次偏移、Redis 故障标记/全局恢复广播；删除 publish 的逐条 SQL；保留 queueInLoop 死锁修复及 member_left → members_changed 顺序。SQL 返回后显式回应用循环再撤本地订阅。
- 前端 2.6–2.8：保留被踢/封的房间及本地消息、标记原因、禁用发送、停止订阅/跟踪、不再请求接口；服务端重新出现同 id 时恢复正常状态。ack 先本地显示，三个消息流路径以正式消息替换 local；被移出后的缺口不补历史；只处理累积草稿。服务端 ack/chat_socket.cc 未改。
- 第 5 部分：一个 master 后端，启动和运行中按 Sentinel 2/3 多数决定 FQDN，先改地址后断已有连接；只读/admin 接口均不发布。健康检查只判当前目标是否存活且为 master。没有多数时运行中保持路由，启动时等待多数。
- 用户补充确认后实现 watcher 异常退出终止 HAProxy 主进程，由 restart 策略恢复；健康检查验证 watcher PID 和 PING，未误认为 unhealthy 自身能重启容器。
- 新增准备就绪条件、受控切换每 200ms 观察并要求路由切换后 1 秒内旧连接归零；旧主重新加入的采样器在启动旧主之前就绪，期间从未出现代理来源连接。
- design D7/D9a/D12、Risks、三个 delta 场景和 3.8/4.6、5.6 文案已同步。README 无需改吞吐数字，本轮未扩展浏览器/CI/真实模型/压测范围。

### 改动文件

- `src/ws/room_hub.h`
- `src/ws/room_hub.cc`
- `src/ws/session_watcher.h`
- `src/ws/session_watcher.cc`
- `src/main.cc`
- `src/services/room_service.cc`
- `web/src/components/ChatView.vue`
- `web/src/lib/ai-drafts.js`
- `web/src/lib/chat-socket.js`
- `web/src/lib/room-stream.js`
- `tests/unit/session_watcher_test.cc`
- `tests/integration/test_room_bans.py`
- `tests/integration/test_ai_replies.py`
- `web/src/components/ChatView.test.js`
- `web/src/lib/ai-drafts.test.js`
- `web/src/lib/chat-socket.test.js`
- `web/src/lib/room-stream.test.js`
- `scripts/test.sh`
- `tests/integration/test_failover.py`
- `docker/haproxy.cfg`
- `docker-compose.yml`
- `scripts/redis-proxy.sh`
- `scripts/redis-master-watch.sh`
- `openspec/changes/add-https-ha-and-room-bans/design.md`
- `openspec/changes/add-https-ha-and-room-bans/specs/chat-rooms/spec.md`
- `openspec/changes/add-https-ha-and-room-bans/specs/high-availability/spec.md`
- `openspec/changes/add-https-ha-and-room-bans/tasks.md`
- `openspec/changes/add-https-ha-and-room-bans/verification-report.md`
- 本 `handoff.md`

23 个实现/配置/测试文件在完整回归期间保持不变，哈希见 `/private/tmp/cppai-sentinel-check-source-hashes.json`。没有 reset/clean/stash 或覆盖其他未提交工作。

### 测试与日志

| 验证 | 结果 | 日志 |
|---|---|---|
| C++ 聚合，含固定种子 1000 次首次偏移 | exit 0 | `/private/tmp/cppai-reconcile-unit.log`、`.exit` |
| 前端 | 13 文件 / 76 项通过，生产构建 exit 0 | `/private/tmp/cppai-reconcile-web-test.log`、`cppai-reconcile-web-build.log` 及 `.exit` |
| 代理启动/管理接口 | 单一 master 条目 UP，容器健康 | `/private/tmp/cppai-single-master-start.log`、`.exit` |
| Sentinel 少数派、watcher 异常退出、主停机演示 | 全通过，exit 0；所有停止服务恢复 | `/private/tmp/cppai-single-master-manual.log`、`.exit` |
| 切换日志时序 | 停主到路由 UP 6.77 秒；Sentinel 确认后约 0.4 秒出现切换日志 | `/private/tmp/cppai-single-master-switch-timing.log` |
| 最新完整 scripts/test.sh | exit 0；首轮 247 passed / 24 skipped；全部阶段通过 | `/private/tmp/cppai-sentinel-check-full.log`、`.exit` |
| 第二轮周期/来源 | 两个来源 IP 不同，实际周期 30 秒；kick/ban 各在 5 秒内收到 room_left，原严格轮次 9 项通过 | 同上、`/private/tmp/cppai-sentinel-check-strict-period.log` |
| OpenSpec strict validate / git diff --check | 通过 | 本轮 verify 工具记录 |

手动演示的实际命令/观察（未输出密码）：

```text
docker compose up -d --wait redis-proxy
show servers state（经容器回环 9998）：只有 master 条目，初始目标 redis-1，UP
docker compose stop sentinel-2 sentinel-3
观察 4 秒：路由保持 redis-1，代理无切换日志
docker compose start sentinel-2 sentinel-3
容器内终止 /tmp/redis-master-watch.pid 对应进程
观察：HAProxy 退出，RestartCount 增加，路由恢复 UP
docker compose stop redis-1
观察：6.77 秒后路由 redis-2 UP，日志 redis master route: redis-1 -> redis-2
docker compose start redis-1
docker compose up -d --wait：恢复所有服务
```

受控切换、故障后历史/AI/登出、旧主重加入、app/L7 停机、双来源连接和认证隔离均在完整脚本验证。24 skipped 含阶段/配置条件，不代表每种可选压力配置均运行。AI 用假模型，无本轮真实服务商调用/计费验证；本轮使用现有项目，没重建干净 checkout，6.1 的干净启动证据保留上一轮的时间/口径。

阶段摘要：

```text
247 passed, 24 skipped in 330.32s (0:05:30)
1 passed, 4 skipped in 0.26s
1 passed, 4 skipped in 0.09s
1 passed, 4 skipped in 6.09s
1 passed, 4 skipped in 2.17s
1 passed, 4 skipped in 3.03s
controlled switch: old proxy connections drained within 1s and stayed at zero
baseline primary: redis-1
1 passed, 4 skipped in 16.59s
sprimary changed redis-1 -> redis-2, ready in 8.97s
1 passed, 4 skipped in 17.82s
old primary rejoin: 52 samples, zero proxy connections
redis-1 rejoined as replica; proxy still targets redis-2
1 passed, 4 skipped in 15.76s
1 passed, 4 skipped in 0.46s
1 passed, 4 skipped in 0.15s
1 passed in 0.12s
1 passed in 0.53s
1 passed in 1.23s
1 passed in 0.99s
1 passed in 27.31s
9 passed in 24.23s
```

### 曾报告的问题与用户决定

- 发现非累积模拟 AI 帧时按规则停下；用户确认后统一为生产累积格式，保留生命周期和恢复测试，只删除指定的非累积专用测试。
- 旧回归第二轮 429（3 failed / 8 passed，exit 1）已报告。用户确认 kick/ban 各用独立来源容器，保留周期/限流/超时，并串行注入故障；两个容器保持存活到严格轮次结束，防止 IP 复用。历史日志 `/private/tmp/cppai-reconcile-before-isolation-full.log`、`.exit`。
- 旧回归 Redis prepare 登录 503（exit 1）按规则停下，只读调查确认切换窗口；没有旧应用日志，未直接捕获具体 READONLY 命令。CC 补充双主路由根因和第 5 部分新方案，用户指示按交接 apply。历史日志 `/private/tmp/cppai-reconcile-full.log`、`.exit` 与 `cppai-reconcile-503-*`。
- 双后端分别检查仍可能同时 UP 的时序问题已报告；CC 改为本轮单一后端方案，未实施旧 external-check 方案。
- unhealthy 不会自动触发 restart 的问题已报告；用户明确确认 watcher 异常退出时结束 HAProxy 主进程，本轮已实现并实测恢复。
- 本轮新代理实现、手动验证和最新完整回归未失败。未放宽断言、拉长故障恢复限额、关闭 CA 验证或改变生产/测试 TLS 设置；没有偷偷加入登录重试。

### 状态及剩余工作

- 正式任务 **29/33**：3.8、4.6 已按证据勾选；2.4、2.8、5.6 仍缺浏览器验收，6.2 缺授权推送及远程 CI。
- 已自动执行 verify：四项未完成验收/CI 记 CRITICAL；W2 切换窗口直接业务断言、W3 同步 TLS1.3 范围按交接保留。详见 verification-report.md，不能声称全部完成或直接可归档。
- 已接受风险保留：故障广播未覆盖时约一个周期撤权延迟、快照期间重新加入的误撤订阅、ack 丢失与踢人同时发生的显示失败、被踢前 @AI 照常回复并由触发者承担费用、改路由/断连接的短暂旧写窗口、单代理单点。
- **让他double check**：请 Claude 独立复核多数派查询/单一路由、watcher 退出恢复、旧连接撤销采样、对账重入、前端保留和 local 替换，以及原始日志。
- 未提交、未推送、未创建 PR、未合并、未归档。下一步浏览器、W2 或远程流程仍由用户决定。


## 7. 浏览器 verify 与给 Claude 的最新交接（2026-11-04，Codex）

**本节为最新验收状态，第 6 节的 29/33 和“浏览器未验收”是前一阶段记录。让他double check。**

### 这次干了什么

用户表示当前 CA 已信任后，继续使用 `openspec-verify-change` 验证同一个 change：`add-https-ha-and-room-bans`（spec-driven）。没有修改实现、`.env`、Compose 配置、TLS 设置或证书；只在实际浏览器走完验收，并按 apply 流程核对任务原文/行号后回填 5.6。任务进度从 29/33 变为 **30/33**。

本轮只更新以下文档：
- `openspec/changes/add-https-ha-and-room-bans/tasks.md`：5.6 勾选；2.4、2.8、6.2 保持未勾选。
- `openspec/changes/add-https-ha-and-room-bans/verification-report.md`：新增浏览器步骤、可见结果、证据边界，调整最终结论。
- 本 `handoff.md`：增加最新结果索引及本节；保留历史记录。

### 实际测了什么

本机 Google Chrome 正常窗口为房主，无痕窗口为普通成员，访问 `https://localhost:8443`。本轮专用测试账号：房主 `ud9fcf0e9e989`、成员 `uceb75a32c962`；房间 `浏览器验收-20261104`。凭证、cookie 和用户私密配置不写入交接。

1. 两个账号均成功登录；Chrome 网站信息明确显示“连接是安全的”，未遇到或绕过证书警告。
2. 房主创建房间、邀请成员，成员从“待加入”加入；房主消息到达成员，成员回复到达房主。
3. 房主踢人前出现含目标用户名的二次确认。成员显示“已被踢出”和明确提示；房间和已加载消息保留，输入与发送 disabled。
4. 重新邀请后“待加入”覆盖被踢状态，加入后能发送，房主实时收到。普通成员的面板无踢人/封禁/封禁列表入口。
5. 房主封禁同样有二次确认；成员显示“已被封禁”、禁止进入提示，旧消息保留且输入/发送 disabled。
6. 封禁期间邀请返回可读提示：“该用户已被此房间封禁，需要先解封”。
7. 封禁列表显示被封用户名、执行者房主用户名、时间 `2026/11/4 16:59:34` 及解封按钮。
8. 解封后列表变为空，成员未自动恢复成员资格；再次邀请后成员变为“待加入”，加入后恢复聊天。17:02 的“解封并重新邀请后：聊天恢复成功”在两端均可见。

以上有 CUA 的实际 AX 记录和踢出后的截图，证据在本对话工具记录中，未另存图片。没有抓 DevTools 网络请求，所以“不再请求历史/成员接口”和 ack 后恰好踢人的竞争条件并非本轮手工直接证明，仍引用前端自动测试/源码证据。按用户名封禁、管理员权限矩阵、退出执行者姓名、账号删除 null 等分支沿用已有单元/集成证据，不冒称全部手工走过。

### 自动化证据复核

- 本轮未改代码；`/private/tmp/cppai-sentinel-check-source-hashes.json` 的 **23 个文件全部匹配**，先前整套回归结果继续适用。
- 实际重读以下退出码均为 0：`cppai-sentinel-check-full.exit`、`cppai-reconcile-unit.exit`、`cppai-reconcile-web-test.exit`、`cppai-reconcile-web-build.exit`（均在 `/private/tmp`）。具体 247 项首轮、各故障阶段、9 项严格轮次、76 项前端及构建结果见第 6 节和报告；本轮未重复运行整套回归。
- Docker 检查：13 个常驻服务均 running/healthy。
- 运行配置仅输出白名单项：app 两实例 `COOKIE_SECURE=1`；入口 8080/8443；连接限额 100；`ALLOWED_ORIGINS=https://localhost:8443,https://127.0.0.1:8443`。未输出密码、私钥、API key、cookie 或完整 config。
- CLI 回填前后快照：`/private/tmp/cppai-browser-verify-apply-before.json`、`cppai-browser-verify-apply-after.json`。后者确认 5.6 done、30/33，剩余 2.4/2.8/6.2。
- 收尾结果：`openspec validate add-https-ha-and-room-bans --strict`、`git diff --check` 均 exit 0；文档进度一致性及 23 个源码哈希复核通过。

### 没做什么，还要做什么

1. **2.4，部分完成**：默认 URL 的浏览器登录/双向聊天已通过，示例与 Compose 的默认值已核对。但当前运行环境额外允许 `https://127.0.0.1:8443`，不是“只按 `.env.example` 填密码”的精确环境；没有覆盖用户 `.env` 或重启服务来凑验收。后续应在按示例准备的隔离环境补这段浏览器证据，再勾选。当前通过不应被写成 HTTPS 仍不能登录聊天。
2. **2.8，部分完成**：当前 CA 的信任与 Chrome 连接安全已证明；本轮不是全新 checkout，也没有导入全新 checkout 生成的 CA。因此“按 README 在新 checkout 首次操作”仍待补齐。保留现有证书和数据卷；不得通过关闭校验或绕过浏览器警告补证据。
3. **6.2**：没有提交/推送授权，因此没触发本次远程 CI。旧 CI 不能代替当前代码；未提交、未推送、未创建 PR、未合并、未归档。
4. W2 切换窗口直接业务响应断言、W3 同步 Python TLS1.3 限制继续按用户/CC 范围保留；无新增真实模型计费、压测、独立生产服务器部署验证。

verify 最终为 **3 critical（未完成验收/CI），2 warnings，0 新 suggestions**。不能把 30/33 写成全部验收完成。保留所有原先用户接受的风险和第 0 节遇问题先报告规则。

### 让他double check

请 Claude 独立核对：
- 第 6 节多数派单一路由、watcher 退出恢复、旧连接撤销采样和原始回归日志；
- 对账重入/回应用循环、前端保留房间/消息和 local 正式替换；
- 本节浏览器已通过的范围、2.4/2.8 严格环境条件仍缺的证据，以及 tasks/report 30/33 是否一致。

专用测试账号及房间保留，结束时两人均已加入、封禁列表为空，方便复核；未全局清理或永久删账号。当前未提交改动与 `data/.DS_Store` 均原样保留。后续验证新环境或推送前先明确范围，不能顺手清空现有数据卷。


## 8. CI、PR 和归档交接（2026-11-04，Codex）

**让他double check。此节为最新收尾状态。**

### 完成的工作和证据

1. 读取 CC 最新 verify：全新环境浏览器的 2.4、2.8 已完成，接续时为 32/33；四项遗留问题保留。
2. 用户明确授权提交、推送、CI、PR，并确认“CI 通过后合并 main，再归档”。提交 65 个相关文件；未包含 `.env`、证书、私钥或 `data/.DS_Store`，23 个已测源码哈希保持一致。
3. 功能提交 `dbecc810dd47758ad957e3f5f2c7436f7706900f` 已推送；[PR #8](https://github.com/anwalld/CppAiWebServer/pull/8) 的 [CI run 41808271464](https://github.com/anwalld/CppAiWebServer/actions/runs/41808271464) 成功，完整执行 C++、前端与集成/故障脚本。日志 `/private/tmp/cppai-ci-41808271464.log`，机读结果 `.json`。
4. 与 main 最近成功 CI run 41630969602 比较，没有新增弃用警告，也没有弃用类 check annotation；不声称原有第三方依赖警告已消除。
5. 在 CI 绿色且 PR head 与已测提交一致后合并，main 的功能合并提交为 `077e21417e829f64799815bfe1bd674b12ea6ec7`。
6. 6.2 按新的 apply JSON 核对原文/行号后回填，CLI 确认 **33/33**。快照 `/private/tmp/cppai-archive-apply-complete.json`。
7. 按归档流程同步四份规格并移动整个 change（包含 `.openspec.yaml`、历史交接、verify 和遗留事项），目标 `openspec/changes/archive/2026-11-04-add-https-ha-and-room-bans/`。新增 edge-gateway/high-availability，更新 chat-rooms/user-auth；旧 Purpose、未提及的需求和场景保持不变。归档改动由独立文档 PR 交付。

收尾校验：四份主规格均与 delta 一致；原有标题/Purpose 与无关需求场景保留；本 change 和四份主规格的 strict validate 通过，全规格普通校验 8/8 通过，git diff --check 通过，活动 change 列表为空，归档文件（含 .openspec.yaml）移动前后的 SHA-256 逐项相同。

基线格式警告：额外执行全规格 strict 时，已有 realtime-messaging 的 Purpose 为 45 字，触发少于 50 字警告（7/8 strict 通过）；已核对与合并后的 main 完全相同。本次没有改它或隐藏该结果；标准全规格校验与本次四份规格严格校验均通过。后续可另行润色 Purpose。

工具异常：GitHub 合并接口返回过 502，随后只读查询确认 PR 已实际合并，未重复合并；SSH 拉取被拒，HTTPS 首次因小写代理变量为无效占位值失败，临时去掉代理环境并使用已有 gh 登录后拉取成功。没有修改全局 Git/代理设置，也没有读取或输出私钥。

### 仍未处理的事项

- W2：直接断言 Redis 切换窗口内登录/消息的成功或有界 retryable 错误。
- W3：同步 Python websockets 客户端 TLS 1.3 间歇超时及限定 TLS 1.2 的测试范围。
- S1：reconcile 清除 reconciling 与再次检查 reconcileAgain 之间的窄竞态。
- S2：失效会话 WebSocket 401 握手响应的 Content-Length 警告及其回归。

这些是 CC/user 已确定归档后另开 change 的问题，继续记录在 design Risks、tasks Workflow follow-up 和 verification-report。没有为使 CI 通过而改方案、加重试、拉长超时、放宽断言或跳过用例，也没有新增真实模型/费用或生产部署验证。

### 让他double check

请 Claude 对照 PR #8 的 CI 原始日志、归档文档 PR 和合并结果独立检查：当前源码/已测提交一致；6.2 为本次 CI 而非旧记录；四份主规格与 delta 一致且旧内容保留；四项遗留问题和用户接受的风险仍在。`data/.DS_Store` 原样保留，未清理现有账号/房间、证书或数据卷。

---

# 给 Claude 的交接（Codex 上一轮的记录，仅作参考）

更新时间：2026-11-04，Asia/Shanghai。用户要求继续实施，结束后自动执行 verify，再交接。本文件替代旧交接的当前状态；旧调查完整保留在 `handoff-history-2026-11-03.md`，其中“尚未修复”“5/31”“正在运行”等只描述历史。

**给接手者：让他double check。请独立审查实现、测试和剩余验收，尤其是 Redis 回调死锁修复、通知丢失后的权限过滤、并发边界和额外数据库查询成本。不要仅凭本交接判定通过。**

## 当前状态

- 项目：`/Users/mima0000/CLionProjects/CppAiServer`。
- 分支：`feat/https-ha-and-room-bans`；HEAD：`71855d174c11ecdc77496d8f83221c1bc1ff7892`。
- 工作区的实现和文档均未提交，包含大量未跟踪文件，请同时看 `git status --short` 和 `git diff`，保留已有工作。
- OpenSpec：`spec-driven`，当前 **27/31**。具体 verify 结果见 `verification-report.md`。
- 本轮没有 commit、push、创建 PR、远程 CI、合并、sync 或 archive。
- 交接前只读核查：主项目 13 个常驻服务均 running/healthy；独立测试项目的容器和数据卷均已清理。
- 用户确认：管理员封禁某人后退出房间，封禁列表仍保留执行者姓名；只有其账号删除后才为 null。spec、实现、测试一致。
- 用户曾拒绝继续处理浏览器 CA 导入，不要再要求立刻导入，也不能绕过证书警告。

## 已实现和修复

### HTTPS 与两层入口

- `docker/certs.Dockerfile`、`scripts/gen-certs.sh`：自动生成 ECDSA P-256 本地 CA 和服务器证书，SAN 为 localhost、gateway、127.0.0.1、::1；保留 CA，有效服务器证书不覆盖，不足 30 天、SAN 异常或链校验失败时重签服务器证书。
- `.gitignore`、`.dockerignore` 隔离 `.env`、证书/私钥及本地生成产物，公共密码字典仍跟踪。
- `docker/gateway.conf.template` 为 L4，`docker/nginx.conf.template` 为两个 L7；固定 Nginx 1.27.5。TLS 1.2/1.3、HTTP 301、PROXY protocol、覆盖伪造转发头、按真实 IP 限认证速率和 TCP 连接数。
- Compose 只由 gateway 发布 8080/8443；默认 Secure cookie、HTTPS origin，集成测试严格验证本地 CA。
- 保留独立且不设 HTTP keepalive 的 `app_websockets` upstream。已核对本机同版本 Drogon v1.9.13 `lib/src/HttpServer.cc`：协议升级只在 `requestParser->firstReq()` 检查，复用已有 HTTP 连接会 404。普通 HTTP 继续使用连接池。
- 同步 Python WebSocket 测试夹具仍限定 TLS 1.2；死锁修复后同步 TLS 1.3 重复握手仍超时，而两个实例健康。异步测试分别强制 TLS 1.2/1.3，生产入口继续支持两种版本。这是实测兼容处理，未证明 CPython/OpenSSL 根因。
- `wait_gateway.py` 对实际 HTTPS `/health` 做有界等待，严格验证 CA；避免服务重建后 L4 TCP 已监听但动态 upstream DNS 尚未收敛造成 TLS EOF。脚本在首轮和严格轮次调用它。

### Claude 定位的 Redis 回调死锁已修复

原始调用链：远端 `member_left` → 删除最后连接 → `unsubscribe` → 重入 Drogon 持有的频道锁。导致 Redis 事件线程永久阻塞，后续健康检查、登录、WSS 连带失败。已核对本机 v1.9.13 `SubscribeContext.cc/.h`、`RedisSubscriberImpl.cc`。

- `src/ws/room_hub.cc`：订阅回调复制消息、投递到应用循环后再处理房间状态和退订。
- `src/ws/session_watcher.cc`：会话结束清理同样脱离持锁订阅回调。
- `conftest.py` 和 `test_multi_instance.py` 固定连接/HTTP 到指定实例，覆盖双向退出、推送、登出和踢人。确定性退出回归在旧实现失败，修复后通过。
- Drogon 自动重连/重新订阅经实际 Redis 故障切换验证成立，没有另造重连管理器。

### Redis 与应用高可用

- 两个 app 共享镜像；跨实例会话、连续 seq 推送、退出、登出、踢人均有回归。应用日志仅记方法/路径/状态。
- Redis 改为 `redis-1`、`redis-2` 和独立新卷；3 个 Sentinel 各自持久化配置；HAProxy `redis-proxy` 仅选 master，后端失效时断开旧连接。
- `scripts/sentinel.sh`、`scripts/redis-proxy.sh`、`docker/haproxy.cfg`：AUTH 编为 RESP 十六进制后通过环境展开，避免密码中的空格/引号破坏参数边界，不写入配置文件。
- C++、AI、测试、清理默认连接 redis-proxy。AI Redis 连接/读写超时均 2 秒，未发现 BLPOP/XREAD BLOCK。DeviceTrust 注释明确只适用于 Sentinel/单主，Cluster 需重做 key。
- `test_failover.py` + `scripts/test.sh`：Redis 主停止→恢复→旧主重加入、app-1 停机→会话及重连补齐、web-1 停机→新连接验证。
- 重复运行会先通过 Sentinel 恢复主故障测试起点；等待非 TILT/无选举最多 120 秒仅用于准备，真正故障恢复仍断言 <30 秒。macOS 的 caffeinate 只随测试进程存活。
- 旧主重加入需同时等到旧主 slave/up、代理 master 且 run_id 匹配 redis-2，覆盖 HAProxy fall=2 的收敛窗口。
- README 已更新拓扑、迁移、CA 导入、手动故障演示及权限/接口。

### 房间踢人、封禁和前端

- `migrations/V4__room_bans.sql`：房间/被封用户删除级联，执行者删除 SET NULL；已在有数据的库验证迁移。
- `RoomService` 中集中权限矩阵，操作事务持有房间行锁；封禁删除成员和邀请，邀请/加入检查封禁，解封不恢复成员/邀请。
- `RoomController` 的 kick/ban/unban/list 四个接口；跨实例 `room_left` 带 left/kicked/banned 原因。
- 消息保存原有的 SQL 成员检查已在锁房间事务内、去重前执行，无 TTL 缓存授权。
- 清理脚本报告 `bans_deleted`，覆盖预演回滚、共享房间、账号删除及执行者为空。
- Vue 成员面板按角色显示按钮、确认用户名、封禁列表、按用户名封禁、解封。ChatView 按移出原因提示并留在列表，过期列表响应不能把房间加回来；封禁邀请有明确提示。

### 组合故障下的真实权限漏洞已复现并修复

复现：guest 订阅 app-2；禁用 PUBLISH；owner 经 app-1 踢人成功；恢复 PUBLISH；owner 发新消息，旧 guest 连接收到。实际输出 `FORMER_MEMBER_RECEIVED_NEW_MESSAGE=True`。

修复在 `RoomHub::publish`：每条非控制事件异步查询 MySQL 当前 `room_members`，写入内部 `only_member_ids`；已有私人目标只取交集；接收端过滤正式消息/AI 片段/通知并去掉内部字段。查询失败就丢弃该次推送，避免泄漏，正式消息可通过历史补齐。`members_changed` 也独立经过此过滤，不能在处理 `member_left` 时直接向本地残留订阅广播。

`sync`、`member_left` 保持控制事件，以便通知已移除的人。**通知仍依赖非持久化 Pub/Sub，故障期间不能保证 room_left 送达；当前成员快照在发布前取得，不能据此宣称与踢人事务/客户端收到消息之间具有全局线性化保证。** D9a 记录了这一方案和每条推送新增一次 SQL 查询的成本。新增 kick/ban 各自的普通消息、成员元数据和 AI 推送回归。

## 测试证据

所有路径为本机日志，接手时先确认文件仍存在。假模型通过不等于真实服务商验证；本轮没有新增真实模型调用或计费验证。

| 验证 | 结果 | 日志 |
|---|---|---|
| 最新定向：房间封禁、多实例、消息、清理、schema，AI_TESTS=1 | 105 passed，退出 0 | `/private/tmp/cppai-recipient-targeted-final.log`、同前缀 `.exit` |
| C++ 单元聚合 | 通过；本次干净环境已重新构建/执行 | `/private/tmp/cppai-recipient-unit.log`、最终全量日志 |
| Vue 挂载单测及生产构建 | 13 文件、68 项通过，构建成功 | `/private/tmp/cppai-room-management-web.log`；最终全量 web 镜像构建也执行 npm test/build，可能使用相同内容的缓存 |
| curl 四条管理接口 | 均得到预期 200 | `/private/tmp/cppai-room-management-curl.log` |
| 最新全新 checkout 完整 scripts/test.sh | 退出码 0；首轮 243 passed / 24 skipped，全部脚本阶段通过 | `/private/tmp/cppai-final-clean-full.log`、同前缀 `.exit` |
| 独立测试项目清理 | 退出码 0，仅清理独立测试项目 | `/private/tmp/cppai-final-clean-teardown.log`、同前缀 `.exit` |
| OpenSpec strict validate / git diff --check | 通过 | `verification-report.md` |

首轮 24 skipped 包含分阶段/配置条件控制的用例；脚本后续单独执行 AI/Redis/app/L7 故障和严格限流。不要据此声称所有可选压力配置也都运行过。

最终全量阶段摘要：

```text
#17 20.30 100% tests passed, 0 tests failed out of 1
243 passed, 24 skipped in 288.46s (0:04:48)
1 passed, 4 skipped in 0.25s
1 passed, 4 skipped in 0.08s
1 passed, 4 skipped in 7.08s
1 passed, 4 skipped in 2.16s
1 passed, 4 skipped in 3.03s
baseline primary: redis-1
1 passed, 4 skipped in 0.26s
sprimary changed redis-1 -> redis-2, ready in 7.13s
1 passed, 4 skipped in 15.82s
ssredis-1 rejoined as replica; proxy still targets redis-2
1 passed, 4 skipped in 14.68s
1 passed, 4 skipped in 0.47s
1 passed, 4 skipped in 0.16s
1 passed in 0.12s
1 passed in 0.42s
1 passed in 27.81s
9 passed in 24.74s
```

最终干净验证项目为 `cppai-final-37c3872d`，checkout `/private/tmp/cppai-final-clean-h3ec96df`。启动前没有证书和该项目数据卷，临时随机凭据、独立端口/子网、独立 app/web 镜像；复制 HEAD 加当前修改及未跟踪源文件，没有复用主栈数据。元数据 `/private/tmp/cppai-final-clean-metadata.json` 保存 178 个实现/配置文件 SHA-256；最终核查 178 个文件全部与当前实现一致。成功后仅删除该临时项目的容器和卷，主项目 MySQL/旧 Redis 卷保留。临时 checkout 可能仍有 `.env` 和私钥，**不要分享或打印，也不要推送**。

重要的旧结果：`cppai-https-deadlock-fixed-full`、`cppai-sentinel-full`、`cppai-instances-stable-full` 均曾退出 0；`cppai-before-recipient-fix-full.*` 是权限过滤修复前的干净全量，不能作为最新版验证。第一次 `cppai-recipient-targeted.log` 失败在测试 setup 的 TLS EOF，尚未执行断言，已增加真实 HTTPS readiness 并成功重跑。

## 仍未完成、下一步

| 任务 | 已有实现 | 缺少证据/动作 |
|---|---|---|
| 2.4 | HTTPS 默认配置、变量和注释已改 | 按默认端口配置在浏览器登录并聊天 |
| 2.8 | README 的 CA 导入/HTTPS/调试说明已写 | 新 checkout 按 README 导入 CA 后，浏览器无证书警告 |
| 5.6 | 前端功能、68 项单测和构建通过 | 浏览器两个账号走踢人、封禁、解封、重新邀请 |
| 6.2 | 本地回归脚本已扩展，现有 CI 使用该脚本 | 用户明确授权推送后，提交/推送并查看该次远程 CI |

用户之前的浏览器现场是 `ERR_CERT_AUTHORITY_INVALID`，没有绕过。临时干净环境的随机端口/origin用于隔离验证，不能替代 2.4 的浏览器默认配置验收。

建议接手顺序：

1. **让他double check**：先读 verify 报告，对照源码和完整日志独立复核；重点关注异步成员查询的并发/顺序边界、私人通知目标取交集、控制事件例外及性能成本。现有 README 的早期吞吐数字不是新增 SQL 过滤之后的压测结果。
2. 补充 verification-report.md 的 W2：在 Redis 实际切换窗口直接断言登录/发送的成功或有界 retryable 错误，当前仅 health 轮询及恢复后业务验证。
3. 环境允许且用户愿意处理信任后，再做三项浏览器验收；用户未改变选择前不要重复催导入或绕过警告。
4. 新改实现就跑相应回归；再进行一次 verify，按实际证据勾选，不能把 27/31 写成 31/31。
5. 远程披露/推送此前被自动审核拒绝，原因是未明确授权向 `github.com/anwalld/CppAiWebServer` 披露代码。本轮没有新增此授权；先完成本地 review，随后如需推送按权限流程办理。
6. CI 通过、review 完成后，再按用户指令决定 PR/merge/archive；本轮没有执行。

常用入口：

```bash
cd /Users/mima0000/CLionProjects/CppAiServer
git status --short --branch
openspec status --change add-https-ha-and-room-bans --json
openspec instructions apply --change add-https-ha-and-room-bans --json
openspec validate add-https-ha-and-room-bans --strict
git diff --check
# 要重新运行完整故障回归时，确保没有另一个测试脚本在操作同一 Compose 项目
bash scripts/test.sh
```

项目写入和 Docker 操作需遵守当前沙箱，不能绕过。不要运行旧 `/private/tmp/cppai-recovery-edit/sync.py`，它可能覆盖现工作。不要 reset/clean 清掉未提交实现。旧 AI 成员交接不是本变更入口。
