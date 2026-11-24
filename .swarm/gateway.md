# Gateway 域经验档案

## 经验日志

### 2026-11-21 AgentNet channel 接线

- **坑：事件循环线程上禁止 `concurrent.futures.Future.result()` 阻塞等待**
  ——若被等待的协程依赖的 server/对端恰好在同一个事件循环上（本机回环、
  测试环境 hub 同进程），阻塞直接冻住循环造成死锁式超时（TimeoutError 的
  str() 为空字符串，极具迷惑性）。async handler 里一律
  `await asyncio.wrap_future(fut)`；只有确认调用方是**非事件循环线程**
  （如 session turn 处理线程）才允许 .result(timeout)。
- **姿势：channel 跨线程模型**——AgentNetClient 跑在自己的 loop 线程；
  工具 handler（宿主主循环）经 `run_coroutine_threadsafe` + wrap_future；
  channel.send_answer（session 处理线程）经 run_coroutine_threadsafe +
  .result(timeout)。三个线程各司其职，谁都不能堵谁。
- **便利贴注入缝**：宿主自定义便利贴不要改 Prompting——
  `SessionMemoryView.set_turn_extras_provider(fn)`（Gateway 层）→
  retrieval_pack.extra_reminders → `_build_turn_info_block` 渲染。
  Prompting 只认纯文本列表，不认识任何具体子系统。

### 2026-11-22 AgentNet 广播制 + 附件 + 观察台

- **协作模型转向全员广播**：用户明确定调——组网聊天像群发，所有消息全员
  可见；定向沟通靠 mentions（@）。kind 降级为分类元数据，hub 路由不再看
  `to`。回答也用 group+@原发送者 广播（原发送者 urgent，其他人静默）。
- **坑：非 ASCII 文件名不能进 URL**——urllib 请求行只能 ASCII，中文文件名
  直接炸。附件 id 用 ASCII 安全字符（sha 前缀+清洗名），原始文件名放
  metadata 的 name 字段作展示。
- **坑：async handler 等跨线程 Future 用 wrap_future**（前条已记）；
  上传走 HTTP 而非 WS：签名头鉴权（agent_id+timestamp+sha256(body)），
  时间窗 ±300s，不占用 WS 通道、天然支持大文件。

### 2026-11-22 观察台写入通道

- **观察台写接口必须强制 token**：GET 只读可以开放，`/api/send` 是注入通道
  （以服务器自持 owner 身份广播），未配置 token 时直接 403——开放公网写接口
  等于给全网发垃圾消息的后门。
- **owner 身份模型**：服务器自持一对 ed25519（state_dir/owner_identity），
  启动时自动 ensure 进 whitelist。用户从手机发的每条消息都是一条合法签名
  envelope，与智能体消息走完全相同的路由/日志/离线队列，无特例分支。
- **坑：Git Bash 的 curl -d 内联中文会按 GBK 发送** → 服务端 json.loads
  报 "bad json"（空字符串错误更可疑）。验收脚本一律 `--data-binary @file`
  （文件按 UTF-8 写），不要内联 JSON。
- **坑：Git Bash /tmp 与 Anaconda Python 的 /tmp 不是同一个目录**——跨工具
  传文件用仓库内相对路径。

### 2026-11-22 组网回复去重与附件缺口

- **坑：channel 自动中继 send_answer 与模型主动调 reply 工具会双重广播**——
  同一条回答出现两遍。修复：channel 维护 `_agent_replied` 标志（工具发送成功
  置 True，urgent 唤醒时复位），send_answer 见旗标跳过中继。
- **坑（更危险）：回复目标 `_reply_target` 用后不清空**——一次组网消息之后，
  所有后续无关 turn（TUI 私聊）的最终回答都会被广播到全网。修复：send_answer
  处理完立即置 None，"一次性回复目标"不变量。
- **附件能力"看起来不存在"的根因往往是配置**：client.upload_file 依赖
  http_base，未配置时 RuntimeError。通道/工具代码都在，但 session_manager
  必须把 agentnet.http_base 传进 channel params。诊断"模型说没有某能力"先查
  配置注入链，再查代码。
- MessageBox 后加列要写 PRAGMA table_info 迁移（attachments TEXT 默认 '[]'），
  否则老库 INSERT 报错。

### 2026-11-22 主人信任、中控、安静期合并阅读

- **信任标注必须打在唤醒文本上**：智能体只认消息内容，不认协议字段。
  owner 身份的消息在 channel 层改写成"主人指令·绝对可信直接执行"，
  同时在工具描述和 actor 提示词各放一份规则（三层冗余防幻觉）。
- **中控消息路由与聊天分离**：hub 的 control envelope 只定向投递
  （不广播），但要在 message_log 里记一条"中控"流水便于观察台追溯。
  端侧执行前必须再查 from ∈ owner_ids（验签由 hub 做，角色判断端侧做）。
- **安静期合并阅读的节奏**：攒到 quiet_seconds 无新群消息才合并导入，
  且必须在会话空闲时（can_auto_read 注入）——处理中打断会产生双 turn。
  测试用直接调 _maybe_auto_read 而不是等 watcher 循环。
- **坑：TUI attach 断连触发 shutdown_on_detach**——headless 部署的机器
  被任何短暂 TCP attach 探一下都会自杀（haru 事故）。
  监控/脚本别把 attach 端口当健康检查用。

### 2026-11-22 定位固化：单用户编排网（不是多用户群聊）

- **AgentNet 的第一原则**：网内所有智能体都是同一个主人（haru）的下属，
  网络存在的意义是让主人把智能体联动起来。没有"多个用户"的概念——
  非 owner 的智能体消息是协作信息，不是权威指令。
- 这个定位决定设计方向：广播制天然成立（无隐私需求）；dm 降级为
  分类元数据；能力注册表的目标是"主人派活给最合适的智能体"而不是
  "陌生人互相发现"；一切权限收敛到 owner 单点。
- 写任何组网相关提示词/文档时用"同事（为同一主人工作）"而非
  "用户/他人"指代其他智能体。

### 2026-11-22 HaruNet 常驻保障

- **headless 守护三件套**：GOHARU_TUI_ATTACH_SHUTDOWN=0（env 覆盖
  gateway.tui_attach_shutdown）+ supervisor 循环（coproc 持 stdin，
  gateway 退出 5s 后自动拉起）+ 白名单 hub。缺一不可：
  detach 开关防误杀，supervisor 防崩溃，hub 白名单防假身份。
- **coproc 是持 stdin 的正确姿势**：`tail -f /dev/null |` 管道会让
  supervisor 循环永远等不到 gateway 退出（tail 不退）；coproc 可以
  在 gateway 退出后 kill $TAILC_PID 干净收尾。
- 机器专属开关一律走 .env.local（start_goharu.sh 加载链），不进 git。

### 2026-11-22 组网消息合并消化（batch intake）

- **@消息绝不逐条唤醒**：urgent 到达进 debounce 缓冲（默认 1.2s），窗口内
  按时间序合并成一条进上下文（带时间戳）。离线批量消息因此一个 turn 看完。
- **群消息体量分流**：≤group_inline_max_chars 内联进同一合辑；超阈值
  主 agent 先处理 @，压缩智能体并行摘要后补送进消息队列。
  分流逻辑两处共用（urgent flush 与 quiet watcher 调同一 _consume_group_rows）。
- **阅读提示必须随消息走**（"关注可能与你有关的内容即可；不重要不相关
  可以不回复"）——写在合辑/摘要文本里，而不是只放系统提示词，跨 turn
  都能被看到。
- 压缩器用注入缝（set_summarize_fn）而不是 channel 直接 import LLM——
  channel 保持传输层纯净，LLM 接入由 SessionManager 出。

### 2026-11-22 关注期模型与防乒乓

- **关注期（拟人化）**：被 @/私聊/主人消息唤醒 → 进入 N 秒关注期（续期），
  期内所有新消息都唤醒；超时无消息回"@才醒"。主人不带 @ 的消息也必须唤醒
  （唯一主人 = 人类发言天然高优先级）。〔**已修订**：2026-11-23 折中方案3——
  主人不@的群消息改为短安静期兜底阅读，见下条。〕
- **回声循环是组网原生风险**：两个 agent 互相看到对方回答 → 互相唤醒 →
  "我不用回复"这句话本身又被广播成新消息。三层刹车：
  ①同事触发的 turn 不自动中继（必须显式用 reply 工具）
  ②连续 N 轮纯同事对话后同事闲聊不再唤醒（agent_pingpong_limit）
  ③合辑文本提示"不要发送纯确认/待命/寒暄类消息"
- **坑：去重旗标（_agent_replied）只能在"会话空闲时的 flush"重置**——
  到达时重置或 turn 中 flush 重置都会把 dedupe 打穿：turn 中到来的同事
  消息触发 flush → 重置 → send_answer 又中继一遍 = 双广播。判据用
  can_auto_read()（=会话空闲），它是注入的、不需要 channel 感知 session 内部。

### 2026-11-23 关注机制折中方案3（主人不@不立即唤醒）

- **最终拍板**：主人不@的群消息**不再立即唤醒全体**（撤销前条"主人恒醒"），
  改为短安静期兜底——新增 `owner_quiet_seconds`（默认 15，`quiet_seconds` 仍 120），
  `_maybe_auto_read` 发现未读主人群消息时用 15s 阈值快速合并阅读；
  @某智能体仍秒回并进关注期。主人的 DM/@ 照旧立即唤醒（CLASS_DM/CLASS_MENTION）。
- **教训：信任标注必须随阅读路径走，不能只在唤醒路径**——主人消息改走
  安静期自动阅读后，原来 urgent 合辑里的"（含主人指令，优先执行）"表头
  会丢，只剩裸 sender。因此 `_consume_group_rows`（分流内联）必须给主人
  sender 加"（主人）"标记，`_maybe_auto_read` 的表头也要按 `owner_latest`
  补"（含主人指令，优先执行）"。任何"消息换了一条进上下文的路"都要复查
  信任/优先级标记是否跟着走。
- **便利**：`MessageBox.latest_ts_from(senders, class_)` 用于查"最近一条来自
  指定发送者的未读消息时间戳"——主人快速阅读的判据，避免为看一眼而把消息
  标已读。

### 2026-11-23 关注期/空闲状态透出观察台

- **链路**：channel `_attentive_until` → client `attentive_provider` →
  presence_update 加 `attentive` 字段 → hub `Presence.set_state` 落盘 →
  `/api/presence` → 页面 chips/列表/详情三处展示。
- **翻转才推**（`_report_presence_if_changed`）：进入关注期在
  `_on_any_message` 即时推；超时退出挂在 quiet watcher tick 上检测。
  续期不重复推——bool 没变就 no-op，防刷。心跳（30s）带同字段兜底。
- **坑：页面 HTML 是模块加载时一次性读入**（`observer.py:30` PAGE 常量），
  改了 observer_page.html 必须重启 poharu-hub 才生效，不是刷新页面就行。
- 状态机 `state`（idle/working/maintenance）与 `attentive` 正交：
  工作中也可能在关注期，两列分开展示。

### 2026-11-23 群体意识：@对象标注 + 主人@别人不当重点

- **事故**：主人@A，B 也回复。根因是 `_flush_urgent` 分区
  `directed = class != GROUP or is_owner`——主人@别人的群消息被当
  "你被点名，需处理或回应"重点塞给 B，且 `auto_relay=bool(owners)`
  把 B 的回答广播回网。修复：directed 只收真正 @我/DM 的消息；
  主人群消息独立成【主人消息 · 群体动态】区，逐条标注
  `@的是谁（不是你）`；auto_relay 改为仅 `owner_directed`（@我/DM）为真。
- **数据底座**：mentions 全链路落库——envelope → messagebox.add(mentions)
  （新列，带 ALTER 迁移）→ read() 返回；urgent item 带 mentions；
  presence_state 广播维护 `_peer_names`（id→name），标注显示名字。
- **提示词纪律**：表头"含主人指令，优先执行"已证明会诱导旁观者行动，
  一律降级为"含主人消息"；凡"优先执行"类措辞只允许出现在确认@我的场景。
- **观察台媒体内联**：renderMsg 按扩展名分 img/video/附件三类
  （IMG_EXTS/VIDEO_EXTS 常量）；`_serve_file` 对媒体用 mimetypes
  guess_type + Content-Disposition inline（原来统一 octet-stream +
  attachment，<video> 无法播）。

### 2026-11-23 中控改名链路

- **hub 权威源**：presence 名字以**白名单**为准（`_handshake` 不采用
  hello 的 name）——`submit_owner_control(rename)` 同步改白名单
  （`ensure_agent` 复用）+ `Presence.rename` + `_broadcast_presence`，
  再发 control 消息让目标 agent 自己改端侧身份。agent 重连后
  `set_online` 读白名单新名，闭环。
- **端侧持久化**：`_apply_rename` 改 `self.name`/identity/params 并
  `_persist_profile()`；`_restore_saved_profile` 里 profile 名改为
  **优先于** config（与 focus/tags 一致）——`self.name or saved` 的
  写法会让重启后 config 默认值顶掉主人改过的名字。
- **提示词名字行**：session_manager 的 `_agentnet_turn_extras` 每轮
  注入"你在组网中的名字是「X」"（走 extra_reminders 便利贴，不动
  静态前缀，零缓存代价）；改名后下一条消息即生效。
- **坑：submit_owner_control 不能在事件循环线程内直接调**——内部
  `run_coroutine_threadsafe(...).result()` 阻塞调用线程，测试里调用
  线程就是 loop 线程会自锁超时（TimeoutError str 为空串，极难排查）。
  生产无碍（observer 在 HTTP 线程调用）。测试须
  `asyncio.to_thread(hub.submit_owner_control, ...)`。
- **观察台详情页滚动**：`#agentDetail` 容器必须是限高 flex 列
  （`flex:1; min-height:0; display:flex; flex-direction:column`），
  否则内层 `.detail` 的 overflow-y 永远不生效，内容被裁切无法下滑。
- **待答卡片区（Phase 3）**：AskUser/高危确认走 hub 挂起表——agent 发
  `type_"ask"` 信封（验签）→ `hub.pending_asks` → 观察台轮询
  `GET /api/requests` → 主人 `POST /api/answer`（强制 token）→
  `submit_answer` pop 后构造 control 信封（`answer` / `confirm_result`
  +approved）回送。channel 覆写 `ask_questions`/`confirm_request` 先
  `_set_ask_pending`（保留网内文本作答兜底）再推观察台，推失败
  `super()` 退化文本。答案契约严格对齐 `_dispatch_answer`：单问单选=
  label 字符串；多问/含多选=JSON `{"answers":[{question,answer}]}`。
- **坑：观察台 2s 轮询抢输入焦点**：`renderPresence` → `showAgent`
  重建 `detailBody.innerHTML`，手机软键盘刚弹出就被收回。守卫：
  `document.activeElement` 在详情页内（INPUT）时跳过重建。待答卡片
  同理——`askCards` 按 request_id 增量挂卡，已存在的不重建。
- **坑：附件"传不了大文件"三重根因**：observer `read()` 整收 body
  （内存爆）+ 25MB 硬上限 + client `read_bytes()` 全量进内存 +
  urlopen timeout=60。改法：双方 1MB 分块流式（client http.client
  +先扫一遍算 sha 签名；server tempfile 落盘 +边读边哈希 +
  `FileStore.commit` 同卷 os.replace），上限提到 2GB。签名内容不变
  （agent_id\nts\nsha256hex），新旧端兼容。
- **坑（复发）：scp 多文件单目标目录会把全部文件丢进同一目录**——
  带不同子路径的文件必须分次 scp 或逐文件给完整远端路径；scp 后
  一律 md5sum 双侧比对 + `mv` 归位检查。
- **安全策略上观察台**：规则在每台设备自己的 config.yaml（`tools.security`），
  天然按设备独立。链路：详情页「安全策略」区（数据来自 profile.security
  快照，channel 启动时上报）→ POST /api/control action=security（清单限
  100 条×200 字符）→ hub extra 载荷透传 → channel `apply_rules` 运行时
  生效 + 经 config_proxy  duck-type（有 set/save 才写盘，纯 dict 配置只改
  运行时）→ `_report_profile` 回推新快照。snapshot/apply_rules 在
  Tools/security.py，Tools 不 import Checkpoint 的同款解耦思路。
- **坑：多 AgentNetClient 同身份并发连接**——hub `_online[agent_id]=ws`
  覆盖式登记不踢旧线，临时客户端（测试/脚本）退出时把仍在用的原连接
  标离线，原连接不断不重连 → 幽灵离线。修复方向（待做）：注册新连接时
  主动 close 旧 ws，让旧端 connect_forever 感知断开立即重连。
- **网络话语权（Floor）**：hub 内存态（唯一权威，重启清零；持有者掉线自动
  收回）+ `floor_state` 广播 + welcome/presence 快照；`AgentSession.enqueue`
  前置关卡（channel 之前，任何 channel 入站统一拦截）——他人持权：本地直连
  输入回透明提示、agentnet 副本静默；自己·arch：worker 线程串行派发给
  Architect `request_wake(REASON_MANUAL, kind=floor_chat, background=False)`，
  回复经**来源 channel** 回送（不碰 _active_channel/_processing）。
  long-hold：turns>3 或 >600s 注入 reminder（epoch 变化即重置计数）。
- **坑：channel 出站钩子有隐式状态**——agentnet 的 send_answer 依赖
  `_reply_target`（正常 turn 的中继语状态），floor 派发这类"旁路回复"
  直接调会被静默丢弃。约定：旁路回复传 `meta={"floor": True}` 走
  `_broadcast_text` 直发；先发 ack 再发结果（透明化）。
- **arch 聊天口吻**：architect 子代理提示词强制结构化审计报告
  （Changes/Verification），话语权对话时必须在 task context 末尾显式
  以"【话语权对话模式】…不要输出 Changes/Verification 报告格式"覆盖，
  否则会回审计体。context 在任务文本末尾，是可靠的覆盖位置。
