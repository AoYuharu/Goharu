"""
Configuration Editor Screen

Interactive configuration editor with categorized parameters
"""

from textual.app import ComposeResult
from textual.screen import ModalScreen
from textual.containers import Container, Vertical, Horizontal
from textual.widgets import Static, Input, RichLog
from textual.binding import Binding
from rich.text import Text
import yaml
from pathlib import Path


# 配置参数定义（分类 + 描述）
CONFIG_CATALOG = [
    # Agent 配置
    {
        "key": "agent.maxDepth",
        "name": "最大迭代次数",
        "type": "int",
        "description": "主智能体最大迭代次数，控制单次对话中智能体可以执行的最大步骤数",
        "default": 8,
        "range": (1, 50),
        "tips": "过高会增加响应时间和成本，过低可能无法完成复杂任务",
        "group": "🤖 Agent 配置"
    },

    # 子智能体配置
    {
        "key": "agent_delegate.max_iterations",
        "name": "子智能体最大迭代次数",
        "type": "int",
        "description": "子智能体（如 explore、plan）的最大迭代次数",
        "default": 20,
        "range": (5, 100),
        "tips": "子智能体用于执行特定任务，如代码探索、规划等",
        "group": "🔧 子智能体配置"
    },
    {
        "key": "agent_delegate.explore.max_concurrent",
        "name": "Explore 最大并发数",
        "type": "int",
        "description": "explore 类型子智能体的最大并发数",
        "default": 3,
        "range": (1, 10),
        "tips": "并发执行可以加速代码探索，但会增加资源消耗",
        "group": "🔧 子智能体配置"
    },
    {
        "key": "agent_delegate.plan.max_concurrent",
        "name": "Plan 最大并发数",
        "type": "int",
        "description": "plan 类型子智能体的最大并发数",
        "default": 2,
        "range": (1, 5),
        "tips": "规划任务通常需要顺序执行，不建议设置过高",
        "group": "🔧 子智能体配置"
    },
    {
        "key": "agent_delegate.max_history_turns",
        "name": "子智能体历史轮数",
        "type": "int",
        "description": "子智能体保留的历史对话轮数",
        "default": 3,
        "range": (1, 10),
        "tips": "更多历史可以提供更好的上下文，但会增加 token 消耗",
        "group": "🔧 子智能体配置"
    },

    # 内存管理
    {
        "key": "memory.daily.retention_days",
        "name": "消息保留天数",
        "type": "int",
        "description": "每日消息保留天数，超过此天数的消息会被归档到长期记忆",
        "default": 7,
        "range": (1, 365),
        "tips": "较短的保留期可以减少内存占用，但可能丢失近期上下文",
        "group": "💾 内存管理"
    },
    # 模型配置
    {
        "key": "model.large-language-model.provider",
        "name": "LLM 提供商",
        "type": "choice",
        "description": "LLM 提供商：仅支持 anthropic_compatible(Anthropic 兼容 API)",
        "default": "anthropic_compatible",
        "choices": ["anthropic_compatible"],
        "tips": "当前仅支持 anthropic_compatible 提供商。需配置 api_key_env 和 base_url",
        "group": "🧠 模型配置"
    },
    {
        "key": "model.large-language-model.model",
        "name": "模型名称",
        "type": "str",
        "description": "使用的模型名称（如 claude-3-5-sonnet-20241022, MiniMax-M2.7）",
        "default": "MiniMax-M2.7",
        "tips": "确保模型名称与 API 提供商匹配",
        "group": "🧠 模型配置"
    },
    {
        "key": "model.large-language-model.max_tokens",
        "name": "最大 Token 数",
        "type": "int",
        "description": "单次生成的最大 token 数",
        "default": 1024,
        "range": (128, 8192),
        "tips": "更大的值允许生成更长的回答，但会增加成本",
        "group": "🧠 模型配置"
    },
    {
        "key": "model.large-language-model.temperature",
        "name": "生成温度",
        "type": "float",
        "description": "生成温度，控制输出的随机性（0.0-1.0）",
        "default": 0.7,
        "range": (0.0, 1.0),
        "tips": "较低的值使输出更确定，较高的值使输出更有创造性",
        "group": "🧠 模型配置"
    },
    {
        "key": "model.large-language-model.top_p",
        "name": "核采样参数",
        "type": "float",
        "description": "核采样参数，控制输出的多样性（0.0-1.0）",
        "default": 0.9,
        "range": (0.0, 1.0),
        "tips": "与 temperature 配合使用，控制生成质量",
        "group": "🧠 模型配置"
    },
    # 工具安全
    {
        "key": "tools.security.enabled",
        "name": "安全检查",
        "type": "bool",
        "description": "是否启用工具安全检查",
        "default": True,
        "tips": "强烈建议保持启用，防止执行危险命令",
        "group": "🔒 工具安全"
    },
    {
        "key": "tools.security.allow_confirmation",
        "name": "允许确认执行",
        "type": "bool",
        "description": "是否允许确认后执行需要确认的命令",
        "default": False,
        "tips": "启用后，某些命令会要求用户确认后才执行",
        "group": "🔒 工具安全"
    },

    # UI 配置
    {
        "key": "ui.verbose",
        "name": "详细日志",
        "type": "bool",
        "description": "是否显示详细日志",
        "default": False,
        "tips": "启用后会显示更多调试信息",
        "group": "🎨 UI 配置"
    },
    {
        "key": "ui.show_memory_events",
        "name": "显示内存事件",
        "type": "bool",
        "description": "是否显示内存管理事件",
        "default": False,
        "tips": "启用后会显示内存归档、合并等事件",
        "group": "🎨 UI 配置"
    },
    {
        "key": "ui.show_actor_output",
        "name": "显示 Actor 输出",
        "type": "bool",
        "description": "是否显示 Actor 的原始输出",
        "default": True,
        "tips": "禁用后只显示最终答案",
        "group": "🎨 UI 配置"
    },

]


class ConfigEditorScreen(ModalScreen):
    """Interactive configuration editor screen"""

    CSS = """
    ConfigEditorScreen {
        align: center middle;
        background: $surface 80%;
    }

    #config-container {
        width: 90%;
        height: 90%;
        border: thick $primary;
        background: $surface;
    }

    #config-header {
        height: 3;
        background: $primary;
        color: $text;
        content-align: center middle;
        text-style: bold;
    }

    #config-body {
        layout: horizontal;
        height: 1fr;
    }

    #config-list-panel {
        width: 2fr;
        border: solid $accent;
        background: $surface;
    }

    #config-list {
        height: 1fr;
        padding: 0 1;
    }

    #config-detail-panel {
        width: 3fr;
        border: solid $primary;
        background: $surface;
    }

    #config-detail {
        height: 1fr;
        padding: 1 2;
    }

    #config-footer {
        height: 3;
        background: $accent;
        color: $text;
        content-align: center middle;
    }

    .group-header {
        text-style: bold underline;
        color: $warning;
    }

    .config-item {
        padding: 0 1;
    }

    .config-item.selected {
        background: $primary;
        color: $text;
    }

    .config-item.normal {
        color: $text;
    }

    .config-item.modified {
        color: $warning;
    }
    """

    BINDINGS = [
        Binding("escape", "close", "Back", priority=True),
        Binding("ctrl+s", "save_all", "Save All", priority=True),
        Binding("ctrl+r", "reset_all", "Reset All", priority=True),
    ]

    def __init__(self):
        super().__init__()
        self.project_root = Path(__file__).parent.parent.parent
        self.config_path = self.project_root / "config.yaml"
        self.config_data = {}
        self.selected_index = 0
        self.modified_keys = set()  # Track which keys have been modified
        self.in_edit_mode = False

    def compose(self) -> ComposeResult:
        yield Container(
            Static("⚙️  Configuration Editor — Browse & Edit Config Parameters", id="config-header"),
            Horizontal(
                Vertical(
                    Static("Configuration Parameters", id="config-list-panel-header"),
                    RichLog(id="config-list", wrap=True, highlight=False, markup=False, auto_scroll=False),
                    id="config-list-panel"
                ),
                Vertical(
                    RichLog(id="config-detail", wrap=True, highlight=False, markup=False, auto_scroll=False),
                    id="config-detail-panel"
                ),
                id="config-body"
            ),
            Static("↑↓ Navigate | Enter Edit | Ctrl+S Save | Ctrl+R Reset | Esc Back", id="config-footer"),
            id="config-container"
        )

    def on_mount(self):
        self.config_list = self.query_one("#config-list", RichLog)
        self.config_detail = self.query_one("#config-detail", RichLog)
        # Disable focus on RichLog widgets
        self.config_list.can_focus = False
        self.config_detail.can_focus = False

        # Load config
        self.load_config()
        self.render_list()
        self.show_detail()

        # Set focus to the screen itself to capture key events
        self.set_focus(None)

    def load_config(self):
        """Load configuration from YAML file"""
        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                self.config_data = yaml.safe_load(f)
        except Exception as e:
            self.app.bell()
            self.dismiss({"error": f"Failed to load config: {e}"})

    def get_config_value(self, key: str):
        """Get current value from config data"""
        keys = key.split(".")
        val = self.config_data
        for k in keys:
            val = val.get(k) if isinstance(val, dict) else None
            if val is None:
                # Return default from catalog
                entry = self.get_entry(self.selected_index)
                return entry.get("default") if entry else None
        return val

    def set_config_value(self, key: str, value):
        """Set value in config data"""
        keys = key.split(".")
        val = self.config_data
        for k in keys[:-1]:
            if k not in val:
                val[k] = {}
            val = val[k]
        val[keys[-1]] = value
        self.modified_keys.add(key)

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

    def render_list(self):
        """Render config list grouped by category"""
        self.config_list.clear()

        # Group configs by category
        groups = {}
        for entry in CONFIG_CATALOG:
            group = entry["group"]
            if group not in groups:
                groups[group] = []
            groups[group].append(entry)

        # Render each group
        idx = 0
        selected_line = 0  # Track which line the selected item is on
        current_line = 0

        for group_name, entries in groups.items():
            # Group header
            header_text = Text()
            header_text.append(f"\n{group_name}")
            header_text.stylize("bold yellow")
            self.config_list.write(header_text)
            current_line += 2  # Header takes 2 lines (newline + text)

            for entry in entries:
                is_selected = (idx == self.selected_index)
                is_modified = entry["key"] in self.modified_keys

                if is_selected:
                    selected_line = current_line

                prefix = "▸ " if is_selected else "  "
                suffix = " *" if is_modified else ""

                item_text = Text()
                item_text.append(f"{prefix}{entry['name']}{suffix}")

                if is_selected:
                    item_text.stylize("bold cyan")
                elif is_modified:
                    item_text.stylize("yellow")
                else:
                    item_text.stylize("cyan")

                self.config_list.write(item_text)
                current_line += 1
                idx += 1

        # Scroll to selected item (approximate)
        # Note: RichLog doesn't have direct line scrolling, so we rely on the visual indicator

    def show_detail(self):
        """Show detail of selected config parameter"""
        self.config_detail.clear()

        entry = self.get_entry(self.selected_index)
        if not entry:
            return

        current_value = self.get_config_value(entry["key"])
        is_modified = entry["key"] in self.modified_keys

        # Title
        title = Text()
        title.append(f"\n⚙️  {entry['name']}", style="bold cyan")
        if is_modified:
            title.append(" (已修改)", style="bold yellow")
        title.append("\n")
        self.config_detail.write(title)

        # Key path
        key_text = Text()
        key_text.append(f"配置路径: ", style="dim")
        key_text.append(f"{entry['key']}\n", style="italic")
        self.config_detail.write(key_text)

        # Separator
        self.config_detail.write(Text("─" * 60, style="dim"))

        # Current value
        value_text = Text()
        value_text.append("\n当前值: ", style="bold")
        value_text.append(f"{current_value}", style="bold green")
        value_text.append(f"  (默认: {entry.get('default', 'N/A')})\n", style="dim")
        self.config_detail.write(value_text)

        # Description
        desc_text = Text()
        desc_text.append("\n说明:\n", style="bold")
        desc_text.append(f"{entry.get('description', '')}\n", style="")
        self.config_detail.write(desc_text)

        # Type and constraints
        param_type = entry.get("type", "str")
        constraint_text = Text()
        constraint_text.append("\n参数类型: ", style="bold")

        if param_type == "choice":
            choices = entry.get("choices", [])
            constraint_text.append(f"选择项\n", style="cyan")
            constraint_text.append(f"可选值: {', '.join(map(str, choices))}\n", style="yellow")
        elif param_type in ["int", "float"]:
            constraint_text.append(f"{'整数' if param_type == 'int' else '浮点数'}\n", style="cyan")
            range_val = entry.get("range")
            if range_val:
                constraint_text.append(f"范围: {range_val[0]} - {range_val[1]}\n", style="yellow")
        elif param_type == "bool":
            constraint_text.append(f"布尔值\n", style="cyan")
            constraint_text.append(f"可选值: true, false\n", style="yellow")
        else:
            constraint_text.append(f"字符串\n", style="cyan")

        self.config_detail.write(constraint_text)

        # Tips
        tips = entry.get("tips", "")
        if tips:
            tips_text = Text()
            tips_text.append("\n💡 提示:\n", style="bold yellow")
            tips_text.append(f"{tips}\n", style="dim yellow")
            self.config_detail.write(tips_text)

        # Edit instruction
        edit_text = Text()
        edit_text.append("\n按 Enter 键编辑此参数", style="dim italic")
        self.config_detail.write(edit_text)

    def get_entry(self, index):
        """Get config catalog entry by flat index"""
        if 0 <= index < len(CONFIG_CATALOG):
            return CONFIG_CATALOG[index]
        return None

    def action_close(self):
        """Close config editor"""
        if self.modified_keys:
            # Ask for confirmation
            self.dismiss({"saved": False, "message": "已取消（有未保存的修改）"})
        else:
            self.dismiss({"saved": False, "message": "已取消"})

    def action_edit_config(self):
        """Edit selected config parameter"""
        entry = self.get_entry(self.selected_index)
        if not entry:
            return

        current_value = self.get_config_value(entry["key"])

        # Mark that we're entering edit mode
        self.in_edit_mode = True

        # Push edit screen
        self.app.push_screen(
            ConfigEditScreen(entry, current_value),
            self.on_edit_complete
        )

    def on_edit_complete(self, result):
        """Handle edit completion"""
        if result and result.get("success"):
            key = result["key"]
            new_value = result["value"]
            self.set_config_value(key, new_value)
            self.render_list()
            self.show_detail()

        # Mark that we're no longer in edit mode
        self.in_edit_mode = False

    def action_save_all(self):
        """Save all changes to config file"""
        if not self.modified_keys:
            self.dismiss({"saved": False, "message": "没有修改"})
            return

        try:
            with open(self.config_path, "w", encoding="utf-8") as f:
                yaml.dump(self.config_data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
            self.dismiss({"saved": True, "message": f"已保存 {len(self.modified_keys)} 项配置"})
        except Exception as e:
            self.dismiss({"error": f"保存失败: {e}"})

    def action_reset_all(self):
        """Reset all parameters to default values"""
        for entry in CONFIG_CATALOG:
            default_value = entry.get("default")
            self.set_config_value(entry["key"], default_value)

        self.modified_keys = set(entry["key"] for entry in CONFIG_CATALOG)
        self.render_list()
        self.show_detail()


class ConfigEditScreen(ModalScreen):
    """Edit screen for a single config parameter"""

    CSS = """
    ConfigEditScreen {
        align: center middle;
        background: $surface 90%;
    }

    #edit-container {
        width: 60%;
        height: auto;
        border: thick $accent;
        background: $surface;
        padding: 2;
    }

    #edit-title {
        text-style: bold;
        color: $accent;
        height: auto;
        margin-bottom: 1;
    }

    #edit-input {
        width: 100%;
        margin-bottom: 1;
    }

    #edit-hint {
        color: $text-muted;
        text-style: italic;
        height: auto;
        margin-bottom: 1;
    }

    #edit-footer {
        height: 1;
        color: $text-muted;
        text-style: dim;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("ctrl+s", "save", "Save"),
    ]

    def __init__(self, entry, current_value):
        super().__init__()
        self.entry = entry
        self.current_value = current_value

    def compose(self) -> ComposeResult:
        param_type = self.entry.get("type", "str")
        hint = ""

        if param_type == "choice":
            choices = self.entry.get("choices", [])
            hint = f"可选值: {', '.join(map(str, choices))}"
        elif param_type in ["int", "float"]:
            range_val = self.entry.get("range")
            if range_val:
                hint = f"范围: {range_val[0]} - {range_val[1]}"
        elif param_type == "bool":
            hint = "输入: true 或 false"

        yield Container(
            Static(f"编辑: {self.entry['name']}", id="edit-title"),
            Static(f"当前值: {self.current_value}", id="edit-current"),
            Input(value=str(self.current_value), placeholder="输入新值", id="edit-input"),
            Static(hint, id="edit-hint"),
            Static("Enter 或 Ctrl+S 保存 | Esc 取消", id="edit-footer"),
            id="edit-container"
        )

    def on_mount(self):
        self.query_one("#edit-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted):
        """Handle input submission"""
        if event.input.id == "edit-input":
            self.action_save()
            event.stop()  # Prevent event bubbling

    def action_cancel(self):
        """Cancel edit"""
        self.dismiss(None)

    def action_save(self):
        """Save edited value"""
        input_widget = self.query_one("#edit-input", Input)
        new_value_str = input_widget.value.strip()

        try:
            # Parse and validate
            param_type = self.entry.get("type", "str")

            if param_type == "int":
                new_value = int(new_value_str)
                range_val = self.entry.get("range")
                if range_val and not (range_val[0] <= new_value <= range_val[1]):
                    raise ValueError(f"值必须在 {range_val[0]} 到 {range_val[1]} 之间")
            elif param_type == "float":
                new_value = float(new_value_str)
                range_val = self.entry.get("range")
                if range_val and not (range_val[0] <= new_value <= range_val[1]):
                    raise ValueError(f"值必须在 {range_val[0]} 到 {range_val[1]} 之间")
            elif param_type == "bool":
                new_value = new_value_str.lower() in ["true", "1", "yes", "on"]
            elif param_type == "choice":
                choices = self.entry.get("choices", [])
                if new_value_str not in choices:
                    raise ValueError(f"值必须是以下之一: {', '.join(choices)}")
                new_value = new_value_str
            else:
                new_value = new_value_str

            self.dismiss({
                "success": True,
                "key": self.entry["key"],
                "value": new_value
            })
        except ValueError as e:
            # Show error
            self.app.bell()
            input_widget.value = ""
            input_widget.placeholder = f"错误: {e}"

