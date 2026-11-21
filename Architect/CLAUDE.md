# Architect/ 维护者文档

幕后助手子系统：主 Goharu 智能体只做对话，Architect 在背后辅佐——
遥测聚合、唤醒路由、代码更新审计。守护只是职责之一。

## 结构

```
Architect/
├── api 入口见 hub.py     # create_architect_hub / get_default_hub（进程级单例）
├── telemetry.py          # TelemetryDigest：事件流哑聚合（纯计数器，零 LLM/IO）
├── wake.py               # WakeRequest + WakeSource 协议（唤醒条件接口，未实现调度）
├── audit.py              # AuditPipeline：staging 审计 → PASS 合并 / FAIL 隔离
├── channel.py            # TelemetryChannel：channel 插件，唯一 import Gateway 的文件
└── CLAUDE.md
```

## 不变量（改动前必读）

1. **hub/telemetry/wake/audit 禁止 import Gateway./Tools./Agent. 顶层依赖**——
   唯一例外是 `hub.default_dispatch` 的**函数内惰性 import**（SubAgent 不存在时
   返回 None 降级）。channel.py 是唯一的 Gateway 适配点（channel 插件身份）。
2. **send_event 在 session turn 线程上同步调用**：digest.ingest 必须快速返回，
   不得抛异常穿透（内部已全 try/except，新增字段保持该纪律）。
3. **四个唤醒触发器**（2026-11-21 定）：
   - ① 不就绪：`Gateway/session.py` 看门狗循环内累计 step 卡死时长，超
     `architect_hub.guardian.not_ready_seconds` → daemon 线程 push wake(fatal)；
     同循环写 `runtime_memory/heartbeat.json`（进程外哨兵的数据源）
   - ② 逻辑错误：`hub.ingest_event` 对 `agent.error` 做 transient/logic 分类，
     逻辑错误（非网络/限流）立即唤醒 guardian；网络错误自愈不唤醒
   - ③ 升级指令：`hub.request_upgrade()` → Upgrader git fetch + worktree 拉
     staging（不动生产区）→ 审计管线 → PASS 且 merge_fn 接线才合并
   - ④ 主智能体主动请求：`Tools/builtin/architect_tools.py` ConsultArchitect
     工具（wait=true 同步等报告，false 后台派发）
   派发串行由 `hub._dispatch_lock` 非阻塞兜底（忙时返回 busy 不排队）；
   后台派发走 hub 内守护线程（turn 线程/看门狗绝不能阻塞）。
   - ⑤ 定期轮询（首个拉式源）：`upgrader.GitUpdateSource` 定期 fetch
     `architect_hub.upgrade.{remote,branch}`（默认 origin/master =
     github.com/AoYuharu/Goharu）比对本地 HEAD，有新提交 → code_update 唤醒
     → hub 路由到 `request_upgrade()`（staging 审计流），报告经 Hero 桥推送。
     调度循环：`hub.start_scheduler()`（守护线程，tick 由
     `wake.scheduler_interval_seconds` 控制，源自身按 check_interval 节流）。
     去重：同一远端 sha 只报一次（审计 FAIL 不反复骚扰）。
     ⚠️ 教训：`int(x or 1800)` 会把合法的 0 吞掉——0=不节流（测试用），
     默认值必须 `x if x is not None else default`。

## 更新四态状态机（update_flow.py，2026-11-21）

`IDLE → GATHERING → AWAITING_CONFIRM → UPDATING → DONE(outcome)`。

- **触发门控**（auto 路径，手动指令 force=True 直通）：落后提交数 ≥
  `upgrade.min_commits`（"足够多的不同"）+ 主会话空闲且用户安静期
  （`_is_suitable_moment`：idle_probe + digest 里 user.message 距今 >
  `upgrade.quiet_seconds`）。门控不过不 ack 源，源下周期重报。
- **不可干扰契约**：三中段期间 `session.enqueue` 挂起一切新消息（不打断、
  不启动新轮）；确认/作答走 channel 拦截层天然豁免；DONE 时
  `hub._on_update_done` 回调 `session._start_processing` 解锁。
- **确认走现有确认链**：`confirm_fn` = confirmation_manager.request
  （TUI 弹窗 / QQ 按钮），超时自动取消。
- **merge_fn 未接线时 UPDATING 只出报告不动生产**（安全默认）。
- 手动入口：TUI `/upgrade`（agent.upgrade RPC）、任意 channel 文本
  "/upgrade" 或 "更新智能体"。
4. **合并动作永远显式注入**：AuditPipeline 的 merge_fn 为 None 时 PASS 也只出
   报告不动目录。staging 目录与生产目录的物理布局由注入方决定。
5. **digest 是旁路观测**：计数错误不得影响主流程；遥测 channel 不实现任何
   用户可见出站（send_answer 是 no-op）。
6. **Hero 桥是唯一对外通道**：`hub.set_hero_bridge(notify_user, message_hero)`
   由 Gateway 装配期注入（session_manager.py，Phase 1 只接 default session）。
   hub 不得自己 import Gateway 拿 channel——未接线时 notify/message 降级为
   日志并返回 False。后台派发完成自动 `_push_report`（`architect_hub.report_push`
   可关）。Architect 侧的沟通工具是 `MessageHero`（architect 白名单专属，
   SubAgent.py 工具清单同步维护）；Hero 侧入口是 `ConsultArchitect`。
   防聊天循环：提示词约定一次交换内收敛。

## 配置（config.yaml）

```yaml
channels:
  plugins: ["Architect.channel"]      # 注册 architect_telemetry channel type
sessions:
  - channels:
      - {type: architect_telemetry, channel_id: arch_telemetry}
architect_hub:
  digest_window: 200                  # 事件滚动窗口
  wake:
    cooldown_seconds: {fatal: 1800, soft_failure: 1800, code_update: 300}
  audit:
    contract_tests: [tests/...]       # staging 里要跑的契约测试清单
    test_timeout: 600
```

## 接口契约测试

`tests/test_architect_hub_standalone.py`：digest 计数 / channel 插件喂数 /
唤醒冷却与 pending 降级 / 审计管线（假 runner + 假 dispatch + 假 merge）。
改动公共接口后必跑。
