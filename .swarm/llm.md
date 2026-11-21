# LLM 代理经验档案

> 管辖：`Agent/llm.py`、`Agent/LLMCore.py`、`Agent/providers/`、`Agent/LargeLanguageModel.py`。
> 章程：`Agent/README.md` "LLM 访问层"节。契约测试：`tests/test_llm_standalone.py`。

## 领域知识（长期沉淀）

- LLMCore 无参必须是单例（`__new__` 里赋值 `_instance`）——Gateway/ActorAgent 靠它共享 `_last_usage`；曾因此破坏缓存命中率显示（2026-10-11 事故，已被契约测试锁死）
- 独立实例只能 `LLMCore(llm_config=dict)`；provider 协议四件套：create_client/probe/build_request/normalize_response
- generate() 双形态：带 tools 返回 NormalizedResponse，不带返回纯文本 str
- 重试：指数退避 1/2/4/8s 封顶，5xx/429/超时关键词可重试

## 经验日志（按时间追加，最新在最后）

- 2026-10-11｜蜂群系统建立，档案初始化。
- 2026-10-13｜代理 TLS 挂死事故：httpcore 隧道握手吃 `timeouts["connect"]`；float timeout 会被摊到全阶段（含 connect）——api_timeout=120 时死代理每次握手卡 120s × 10 次重试 ≈ 20min，打爆 150s watchdog。修法：httpx.Timeout(connect=15, read=api_timeout) + generate() 总时限 max_total_seconds（检查点在 sleep 之前，封顶耗时=预算+最后一次退避，属预期）。httpx.Timeout 首参是"默认值"不是 read；anthropic/openai SDK 都把 timeout 原样存 client.timeout 可直接断言。
- 2026-10-13｜观察：test_llm_standalone.py 第[1]节会真实发网络 probe（auto 探测），契约测试并非完全离线。
