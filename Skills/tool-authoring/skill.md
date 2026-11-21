---
name: tool-authoring
description: >-
  给 Goharu 自己新增/修改内置工具（Tools/builtin 下的 Python 工具），并热加载验证。
  当用户说"给你自己写个工具""加一个内置工具""把这个能力做成工具""写个 tool
  然后热重载试试""修改现有工具的 schema/参数"时使用。含注册范式、最小模板、
  硬约束、持久化与动态验证清单。
---

# 工具添加（Tool Authoring）

目标：新增一个内置工具，让它当轮就能被模型调用，且重启后仍在。

## 一、先判断

- 现有 30+ 内置工具或 MCP 工具能覆盖的，**不要造新工具**；先查工具清单有无同名（同名注册是**覆盖**语义，会静默干掉旧工具）。
- 需要外部资源（DB、Manager 实例）→ 走依赖注入；需要执行命令/删文件 → 必须自己接安全层。

## 二、流程（5 步）

1. **建文件**：`Tools/builtin/<snake_case>_tool.py`，模块末尾集中 `registry.register(...)`。
2. **写 handler**：普通函数或 `async def` 均可，**必须返回 `str`**，**不得抛异常**（内部 `try/except` 后返回 `{"status":"error","error":...}`）。
3. **注册**：`name` / `description` / `arguments_schema` / `handler` 四个必填；`group` 可选（core/file/memory/utility/knowledge）。
4. **持久化**：`config.yaml` 的 `tools.builtin_modules` 追加 `- Tools.builtin.<module>`（不写则重启即丢）。
5. **热加载并验证**：`ReloadTools(modules=["Tools.builtin.<module>"])` → 确认 `status: ok` 与 `tool_count` 变化 → 真实调用一次。

## 三、最小模板

```python
"""Xxx tool."""
import json
from Tools.registry import registry

def xxx(query: str, limit: int = 5) -> str:
    try:
        return json.dumps({"status": "success", "items": []}, ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"status": "error", "error": f"{type(exc).__name__}: {exc}"},
                          ensure_ascii=False)

registry.register(
    name="xxx",                    # 模型看到的工具名，非空
    description="做什么 + 有无副作用 + 何时使用。",   # 模型据此决策
    arguments_schema={
        "type": "object",          # 必须
        "properties": {
            "query": {"type": "string", "description": "..."},
            "limit": {"type": "integer", "description": "...", "default": 5},
        },
        "required": ["query"],
    },
    handler=xxx,
    group="utility",
)
```

## 四、硬约束（违反即静默失效）

- **参数名必须与 schema 的 properties key 完全一致**：dispatch 用 kwargs 调 handler，不一致直接 TypeError。
- **schema 的 `default` 不自动填充**：只是给模型看的提示，真正兜底靠函数签名默认值，两处要一致。
- **`required` 缺失 = 全部可选**；类型白名单为 string/integer/number/boolean/object/array/null，未知 type 一律放行。
- **`is_async` 不要手写**，`asyncio.iscoroutinefunction` 自动判定。
- **sync handler 跑在线程池**，内部不要依赖事件循环。
- **依赖注入型工具**必须同时登记到 `Tools/builtin/reload_tools.py` 的 `_INJECTABLES`：
  `"Tools.builtin.xxx_tool": ("_dep", "set_dep")`，否则热重载后注入实例变 `None`。
- **安全层不自动生效**：`Tools/security.py` 的 `get_security_checker()` 只在工具内部主动调用；新工具若执行命令/写系统路径/删除文件，必须自己调，否则等于开后门。

## 五、验证（必须真实执行，不能只看 status: ok）

```bash
cd /e/Goharu && python <<'EOF'
import asyncio, sys
sys.path.insert(0, "E:/Goharu")
from Tools.loader import load_builtin_tools
load_builtin_tools(["Tools.builtin.xxx_tool"])
from Tools.registry import registry
print(asyncio.run(registry.dispatch("xxx", {"query": "test"})))
EOF
```

逐项确认：
1. 正向调用返回预期值（**校验语义值**，不是只看非空）。
2. 边界/非法输入返回 `error` JSON 而非抛异常。
3. 类型错误被 schema 拦截（`must be of type ...`）。
4. `registry.get_entry(name).to_definition()` 的 name / inputSchema 正确。
5. 走一次 `InProcessToolRuntime` 完整链路（initialize → call_tool → 格式化输出）。
6. 热加载后**自己直接调用**一次，证明已进入工具清单。
7. `load_builtin_tools()` 无参加载时也能找到该工具（证明 config.yaml 生效）。

## 六、常见坑

| 现象 | 原因 | 处理 |
|---|---|---|
| 热加载 ok 但模型看不见 | 未刷新工具定义缓存 | 依赖 `ReloadTools` 成功后的 `list_tools()`；不要手工改注册表 |
| 重启后工具消失 | 没写进 `config.yaml: tools.builtin_modules` | 补一行 |
| 热加载后注入实例变 None | 漏登 `_INJECTABLES` | 补登记 |
| 工具返回被格式化器改乱 | `tools.result_format.enabled` 会做中文模板渲染 | 返回 JSON 字符串即可，格式化层会兜底 |
| 长耗时工具卡住 | 前台默认 25s 后转后台 | 需要同步结果就用 `run_background` 语义或拆小 |
