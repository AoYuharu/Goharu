# TUI 配置编辑器和历史记录加载修复

## 修复日期
2026-05-10

## 问题描述

### 问题 1：`/config` 配置编辑器一闪而过
**现象**：用户输入 `/config` 后，配置编辑页面弹出一瞬间就消失，完全没有时间进行修改。

**根本原因**：
- `ConfigEditorScreen` 使用了旧的 `key_down()`, `key_up()`, `key_enter()` 方法来处理键盘事件
- 这些方法在 Textual 框架中不能正确阻止事件传播
- 当用户按回车键打开编辑器时，事件继续传播导致屏幕立即关闭
- `in_edit_mode` 标志没有正确设置和检查

### 问题 2：历史记录加载失败
**现象**：每次启动 TUI 时显示 "Request timeout or error，已取消，有未保存的修改"。

**根本原因**：
- `_on_gateway_ready()` 在 UI 主线程中**同步调用** `gateway.call("agent.get_history")`
- `gateway.call()` 是阻塞调用，会等待最多 30 秒
- 如果 Gateway 启动慢或响应慢，会阻塞 UI 线程并超时
- 阻塞期间用户无法与界面交互

## 修复方案

### 修复 1：配置编辑器键盘事件处理

**文件**：`TUI/screens/config_editor.py`

**修改内容**：

1. **替换旧的键盘处理方法**：
   - 删除 `key_down()`, `key_up()`, `key_enter()` 方法
   - 添加统一的 `on_key(event)` 方法来处理所有键盘事件
   - 使用 `event.prevent_default()` 和 `event.stop()` 正确阻止事件传播

2. **添加编辑模式标志管理**：
   - 在 `action_edit_config()` 中设置 `self.in_edit_mode = True`
   - 在 `on_edit_complete()` 中重置 `self.in_edit_mode = False`
   - 在 `on_key()` 中检查 `in_edit_mode`，避免在编辑时响应导航键

3. **改进焦点管理**：
   - 在 `on_mount()` 中调用 `self.set_focus(None)` 确保屏幕能接收键盘事件

**代码示例**：
```python
def on_key(self, event):
    """Handle key events"""
    if self.in_edit_mode:
        return

    if event.key == "down":
        if self.selected_index < len(CONFIG_CATALOG) - 1:
            self.selected_index += 1
            self.render_list()
            self.show_detail()
        event.prevent_default()
        event.stop()
    elif event.key == "up":
        if self.selected_index > 0:
            self.selected_index -= 1
            self.render_list()
            self.show_detail()
        event.prevent_default()
        event.stop()
    elif event.key == "enter":
        self.action_edit_config()
        event.prevent_default()
        event.stop()
```

### 修复 2：异步历史记录加载

**文件**：`TUI/app.py`

**修改内容**：

1. **使用后台线程加载历史**：
   - 创建 `load_history_async()` 函数在后台线程中调用 `gateway.call()`
   - 避免阻塞 UI 主线程

2. **使用 `call_from_thread()` 更新 UI**：
   - 后台线程不能直接操作 UI 组件
   - 使用 Textual 的 `call_from_thread()` 方法安全地从后台线程更新 UI

**代码示例**：
```python
def _on_gateway_ready(self, payload):
    """Gateway is ready"""
    status_bar = self.query_one(StatusBar)
    status_bar.set_status("Gateway ready - You can start chatting!", "success")

    chat_panel = self.query_one(ChatPanel)
    chat_panel.add_system_message("Gateway is ready! You can now send messages.")

    # 加载历史消息（异步，避免阻塞 UI）
    import threading
    def load_history_async():
        try:
            result = self.gateway.call("agent.get_history", {})
            messages = result.get("messages", [])
            if messages:
                # Use call_from_thread to safely update UI from background thread
                self.call_from_thread(chat_panel.replay_messages, messages)
        except Exception as e:
            # Use call_from_thread to safely update UI from background thread
            self.call_from_thread(chat_panel.add_system_message, f"(历史记录加载失败: {e})")

    thread = threading.Thread(target=load_history_async, daemon=True)
    thread.start()
```

## 测试验证

**测试脚本**：`test_tui_config_fix.py`

**测试内容**：
1. ✅ 配置编辑器可以正常导入
2. ✅ 配置编辑器有正确的键盘事件处理（`on_key` 方法和 `in_edit_mode` 标志）
3. ✅ 应用有异步历史记录加载（使用 `threading` 和 `call_from_thread`）

**测试结果**：
```
Testing TUI fixes...

1. Testing config editor import...
✅ Config editor imports successfully

2. Testing config editor key handling...
✅ Config editor has proper key handling

3. Testing app history loading...
✅ App has async history loading

============================================================
Test Results: 3/3 passed
✅ All tests passed!
```

## 使用说明

### 配置编辑器使用
1. 在 TUI 中输入 `/config` 打开配置编辑器
2. 使用 `↑` `↓` 键浏览配置项
3. 按 `Enter` 键编辑选中的配置项
4. 在编辑框中输入新值，按 `Enter` 或 `Ctrl+S` 保存
5. 按 `Esc` 取消编辑
6. 按 `Ctrl+S` 保存所有修改到 `config.yaml`
7. 按 `Ctrl+R` 重置所有配置为默认值
8. 按 `Esc` 退出配置编辑器

### 历史记录加载
- 启动 TUI 后，历史记录会在后台自动加载
- 加载过程不会阻塞界面，用户可以立即开始输入
- 加载完成后会显示 "--- 恢复 N 条历史消息 ---"
- 如果加载失败，会显示错误信息但不影响正常使用

## 技术要点

### Textual 键盘事件处理
- 使用 `on_key(event)` 而不是 `key_<name>()` 方法
- 必须调用 `event.prevent_default()` 和 `event.stop()` 阻止事件传播
- ModalScreen 需要正确管理焦点才能接收键盘事件

### Textual 线程安全
- UI 组件只能在主线程中操作
- 后台线程必须使用 `app.call_from_thread()` 来更新 UI
- 使用 `daemon=True` 创建守护线程，避免阻止程序退出

### 异步 vs 同步调用
- 同步调用（`gateway.call()`）会阻塞当前线程
- 在 UI 线程中使用同步调用会冻结界面
- 耗时操作应该在后台线程中执行

## 相关文件

- `TUI/screens/config_editor.py` - 配置编辑器屏幕
- `TUI/app.py` - TUI 主应用
- `TUI/gateway_client.py` - Gateway 客户端（JSON-RPC 通信）
- `test_tui_config_fix.py` - 修复验证测试

## 后续改进建议

1. **配置编辑器**：
   - 添加搜索/过滤功能
   - 支持批量编辑
   - 添加配置验证和预览

2. **历史记录加载**：
   - 添加加载进度指示器
   - 支持分页加载大量历史记录
   - 添加历史记录搜索功能

3. **错误处理**：
   - 更详细的错误信息
   - 添加重试机制
   - 记录错误日志供调试
