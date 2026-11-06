# 分支整理记录

2026-11-06，用户要求立即整理分支。以origin/main的已合并祖先关系核验，远端删除使用准确SHA租约和atomic推送。未改动功能源码和主工作区的未提交文档。

清理本地14个旧分支、远端11个旧分支，移除已完成的/private/tmp/cppai-offline-message-retries工作区；该工作区无未提交文件，忽略文件仅web/node_modules和web/dist，且无运行容器挂载。

保留4个本地分支：

| 分支 | 用途 |
|---|---|
| main | 主线 |
| codex/chat-controls-and-observability | 当前PR13及CI |
| codex/shared-request-buffering | 运行中的cppai-buffering-static仍挂载该工作区 |
| claude/project-framework-overview-22187f | Claude工作区占用 |

远端保留main、当前PR13分支及shared-request-buffering。PR13尚未合并，CI仍由原后台任务跟进。后续合并/归档时不应再次尝试处理已移除工作区。

## 已删除本地分支及原提交

| 分支 | 原SHA |
|---|---|
| claude/next-steps-4c4c78 | f3c338744438f3d71ace267eeccfe90ea56cd046 |
| codex/archive-harden-ha-verification | 03d5fb1f6dffd04d0505d324741885ae400b5555 |
| codex/archive-https-ha-and-room-bans | 7ca936f40098c823aeb9ffe14781657f54374086 |
| codex/fix-offline-message-retries | adcd29457ac8e3eb24e41736988b9e5cd0f7cfdf |
| codex/harden-ha-verification | 4305bc4a0e88f39dc6db69de0ee69bffc1601f20 |
| docs/archive-add-ai-members | 3e6abb806d34952b1da988f0679e1c824e69cd42 |
| docs/archive-harden-auth-and-migrations | e87127db2515701475cb2318cd8532e0638967fb |
| docs/archive-session-lifecycle | 2e834a109b6209e57a99a2d357b36b4b494cb79d |
| feat/add-ai-members | f99e2e1ead82f3d187ea280c99ea615c121f0035 |
| feat/chatroom-foundation | f431c93c1192a227df6175d35032c03e8eb54529 |
| feat/harden-auth-and-migrations | 9b7a82d638a86b664b07cef53e080999b178673a |
| feat/https-ha-and-room-bans | dbecc810dd47758ad957e3f5f2c7436f7706900f |
| feat/session-lifecycle-and-tidy-up | 065b804aff108d203cd55adae34811f062b73394 |
| fix/verify-warnings | 3847daa1857c24227aff5a853227536d87268de2 |

## 已删除远端分支及原提交

| 分支 | 原SHA |
|---|---|
| codex/archive-harden-ha-verification | 03d5fb1f6dffd04d0505d324741885ae400b5555 |
| codex/archive-https-ha-and-room-bans | 7ca936f40098c823aeb9ffe14781657f54374086 |
| codex/harden-ha-verification | 4305bc4a0e88f39dc6db69de0ee69bffc1601f20 |
| docs/archive-add-ai-members | 3e6abb806d34952b1da988f0679e1c824e69cd42 |
| docs/archive-harden-auth-and-migrations | e87127db2515701475cb2318cd8532e0638967fb |
| docs/archive-session-lifecycle | 2e834a109b6209e57a99a2d357b36b4b494cb79d |
| feat/add-ai-members | f99e2e1ead82f3d187ea280c99ea615c121f0035 |
| feat/harden-auth-and-migrations | 9b7a82d638a86b664b07cef53e080999b178673a |
| feat/https-ha-and-room-bans | dbecc810dd47758ad957e3f5f2c7436f7706900f |
| feat/session-lifecycle-and-tidy-up | 065b804aff108d203cd55adae34811f062b73394 |
| fix/verify-warnings | 3847daa1857c24227aff5a853227536d87268de2 |

核验main：a1bfbc020dedfe316e1428b099204e4f503d0a61。这些提交仍由main历史保留，需要恢复分支时可按上述SHA重建。让他double check。
