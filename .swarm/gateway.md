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
