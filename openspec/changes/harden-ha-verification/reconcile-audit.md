# 对账 S1 复核

日期：2026-11-04。结论：当前源码下，上一轮 S1 描述的尾部并发窗口不成立；不是本轮修复了这个竞态。没有修改生产同步代码。

## 标记访问与执行线程

`rg -n 'reconciling_|reconcileAgain_' src` 显示，除声明外，全部访问只在 `src/ws/room_hub.cc::reconcile`：
- 入口先 `co_await switchThreadCoro(app().getLoop())`，再 exchange reconciling、写 reconcileAgain。
- 每轮开始清标记时仍在该 loop 上；数据库 await 期间可能由数据库线程恢复，但下一轮开始前已显式切回 app loop。
- 尾部在 `co_await switchThreadCoro(loop)` 后执行 `reconcileAgain_.exchange(false)`，紧接着清 `reconciling_`。两者之间没有 await、事件泵或调用用户回调。

精确依赖版本来自 Dockerfile 的 v1.9.13。该版本 SwitchThreadAwaiter::await_suspend 使用 `loop_->runInLoop([handle] { handle.resume(); })`；不是只“记录一个线程指针”。
https://github.com/drogonframework/drogon/blob/v1.9.13/lib/inc/drogon/utils/coroutine.h#L653-L665

## 全部调用路径

1. RoomHub::start 的 rooms:reconcile 订阅回调先 queueInLoop，再 async_run/reconcile。
2. RoomHub::start 的每秒恢复广播 coroutine 在 PUBLISH 成功后调用 reconcile；即使 Redis await 切线程，reconcile 入口仍切回 app loop。
3. SessionWatcher 的 tick 顺序 await check、await reconcile；同样不依赖 check 的恢复线程。

其他线程直接调用 reconcile 也先经过其入口切线程；没有公开的独立标记写入方法。数据库 await 期间到来的请求可以设置“再跑一轮”，该标记会在尾部被检查；尾部两步之间，其他排队的 reconcile 不能抢占同一事件循环。

## 推论与边界

- 当前 S1 的假设需要另一个执行者在“尾部检查”和“清 reconciling”之间插入标记写入；现有线程归属与无挂起点使此前提不成立。
- 不添加第二次检查、递归或自旋，也不编造生产竞态复现。
- 如果以后取消入口/尾部的切线程约束、增加其他标记写入入口，必须重新审查同步协议。
- 此结论不消除已接受的检查周期延迟、快照后重新加入的误撤订阅、数据库失败后等下一轮等边界。
- 复核方法为调用链与精确依赖源码证明，不冒称进行了任意线程调度的穷举测试。
