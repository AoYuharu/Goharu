"""
Prompt Editor Widget

Browse and edit prompt files directly from TUI.
Provides categorized list view and in-app text editing.
"""

from textual.app import ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.widgets import Static, TextArea, RichLog
from textual.screen import ModalScreen
from textual.message import Message
from textual.binding import Binding
from rich.text import Text
from rich.table import Table
from rich.panel import Panel
from pathlib import Path
from datetime import datetime


# Define prompt file catalog
PROMPT_CATALOG = [
    # Core agent prompts
    {
        "name": "Actor Agent",
        "file": "prompts/actor/base.md",
        "description": "主智能体提示词 — 工具调用、答案生成、子智能体调度",
        "group": "🧠 Core Agents"
    },
    {
        "name": "Reflection Agent",
        "file": "prompts/reflection/base.md",
        "description": "反思智能体提示词 — 回合决策、防编造、任务完成判断",
        "group": "🧠 Core Agents"
    },
    {
        "name": "Review Agent",
        "file": "prompts/reviewer/base.md",
        "description": "用户画像复盘模块 — 提取用户画像增量写入 USER.md",
        "group": "🧠 Core Agents"
    },
    {
        "name": "Review Agent (Contract)",
        "file": "prompts/reviewer/contract.md",
        "description": "复盘输出契约 — 定义用户画像更新的JSON格式",
        "group": "🧠 Core Agents"
    },
    {
        "name": "Summarizer Agent",
        "file": "prompts/summarizer/base.md",
        "description": "长期记忆摘要模块 — 将过期对话压缩为长期记忆",
        "group": "🧠 Core Agents"
    },

    # Paper analysis prompts
    {
        "name": "论文分析 Orchestrator",
        "file": "prompts/paper_analysis/orchestrator.md",
        "description": "论文分析主调度 — 协调7个子智能体按5阶段SOP",
        "group": "📄 Paper Analysis"
    },
    {
        "name": "论文分析 SOP (Agent目录)",
        "file": "Agent/prompts/paper_analysis_sop.md",
        "description": "论文分析SOP备用版 — 18条关键规则，强调直接输出JSON",
        "group": "📄 Paper Analysis"
    },
    {
        "name": "PDF Parser",
        "file": "prompts/paper_analysis/pdf_parser.md",
        "description": "PDF解析子智能体 — 提取元数据、章节、图表、公式",
        "group": "📄 Paper Analysis"
    },
    {
        "name": "Content Analysis",
        "file": "prompts/paper_analysis/content_analysis.md",
        "description": "内容分析子智能体 — 分析摘要、引言、方法等核心章节",
        "group": "📄 Paper Analysis"
    },
    {
        "name": "Tech Extraction",
        "file": "prompts/paper_analysis/tech_extraction.md",
        "description": "技术提取子智能体 — 提取可复用技术模块和创新点",
        "group": "📄 Paper Analysis"
    },
    {
        "name": "Fake Data Reproduction",
        "file": "prompts/paper_analysis/fake_data_reproduction.md",
        "description": "假数据复现子智能体 — 生成假数据及维度变化追踪",
        "group": "📄 Paper Analysis"
    },
    {
        "name": "Literature Analysis",
        "file": "prompts/paper_analysis/literature_analysis.md",
        "description": "文献分析子智能体 — 分析引用、分类、知识库覆盖",
        "group": "📄 Paper Analysis"
    },
    {
        "name": "Relation Analysis",
        "file": "prompts/paper_analysis/relation_analysis.md",
        "description": "关联分析子智能体 — 引用网络构建、SOTA判断",
        "group": "📄 Paper Analysis"
    },
    {
        "name": "Knowledge Integration",
        "file": "prompts/paper_analysis/knowledge_integration.md",
        "description": "知识库整合子智能体 — 分析结果整合和目录结构创建",
        "group": "📄 Paper Analysis"
    },

    # System snippets
    {
        "name": "系统: Soul Section",
        "file": "prompts/system/soul_section.md",
        "description": "角色设定引导 — 引入SOUL.md",
        "group": "⚙️ System Snippets"
    },
    {
        "name": "系统: User Profile",
        "file": "prompts/system/user_profile.md",
        "description": "用户画像引导 — 引入USER.md",
        "group": "⚙️ System Snippets"
    },
    {
        "name": "系统: Memory",
        "file": "prompts/system/memory.md",
        "description": "长期记忆引导 — 引入MEMORY.md",
        "group": "⚙️ System Snippets"
    },
    {
        "name": "系统: Memory Background",
        "file": "prompts/system/memory_background.md",
        "description": "补充记忆引导",
        "group": "⚙️ System Snippets"
    },
    {
        "name": "系统: Tool Directory",
        "file": "prompts/system/tool_directory.md",
        "description": "工具目录引导 — 引入工具列表及Schema",
        "group": "⚙️ System Snippets"
    },
    {
        "name": "系统: Reflection Question",
        "file": "prompts/system/reflection_question.md",
        "description": "反思-问题 — 提供用户原始问题",
        "group": "⚙️ System Snippets"
    },
    {
        "name": "系统: Reflection History",
        "file": "prompts/system/reflection_history.md",
        "description": "反思-历史 — 对话与工具调用历史",
        "group": "⚙️ System Snippets"
    },
    {
        "name": "系统: Reflection Task",
        "file": "prompts/system/reflection_task.md",
        "description": "反思-任务 — 定义Reflection判断任务",
        "group": "⚙️ System Snippets"
    },
    {
        "name": "系统: Day Summary Contract",
        "file": "prompts/system/day_summary_contract.md",
        "description": "日总结契约 — 过期对话压缩JSON格式",
        "group": "⚙️ System Snippets"
    },
    {
        "name": "系统: Topic Merge Contract",
        "file": "prompts/system/topic_merge_contract.md",
        "description": "话题合并契约 — 合并重复topic文档的JSON格式",
        "group": "⚙️ System Snippets"
    },
    {
        "name": "系统: Review Turn",
        "file": "prompts/system/review_turn.md",
        "description": "复盘触发 — 每轮对话后触发用户画像复盘",
        "group": "⚙️ System Snippets"
    },
]


class PromptListScreen(ModalScreen):
    """Full-screen prompt list browser"""

    CSS = """
    PromptListScreen {
        align: center middle;
        background: $surface 80%;
    }

    #prompt-container {
        width: 90%;
        height: 90%;
        border: thick $primary;
        background: $surface;
    }

    #prompt-header {
        height: 3;
        background: $primary;
        color: $text;
        content-align: center middle;
        text-style: bold;
    }

    #prompt-body {
        layout: horizontal;
        height: 1fr;
    }

    #prompt-list-panel {
        width: 2fr;
        border: solid $accent;
        background: $surface;
    }

    #prompt-list {
        height: 1fr;
        padding: 0 1;
    }

    #prompt-preview-panel {
        width: 3fr;
        border: solid $primary;
        background: $surface;
    }

    #prompt-preview {
        height: 1fr;
        padding: 0 1;
    }

    #prompt-footer {
        height: 3;
        background: $accent;
        color: $text;
        content-align: center middle;
    }

    .group-header {
        text-style: bold underline;
        color: $warning;
    }

    .prompt-item {
        padding: 0 1;
    }

    .prompt-item.selected {
        background: $primary;
        color: $text;
    }

    .prompt-item.normal {
        color: $text;
    }
    """

    BINDINGS = [
        Binding("escape", "close", "Back", priority=True),
        Binding("ctrl+s", "save_prompt", "Save", priority=True),
        Binding("ctrl+q", "close", "Quit", priority=True),
    ]

    def __init__(self):
        super().__init__()
        self.project_root = Path(__file__).parent.parent.parent
        self.selected_index = 0
        self.selected_entry = None
        self.in_editor = False
        self.editor_content = None

    def compose(self) -> ComposeResult:
        yield Container(
            Static("📝 Prompt Editor — Browse & Edit Prompt Files", id="prompt-header"),
            Horizontal(
                Vertical(
                    Static("Prompt Files", id="prompt-list-panel-header"),
                    RichLog(id="prompt-list", wrap=True, highlight=False, markup=False),
                    id="prompt-list-panel"
                ),
                Vertical(
                    RichLog(id="prompt-preview", wrap=True, highlight=False, markup=False),
                    id="prompt-preview-panel"
                ),
                id="prompt-body"
            ),
            Static("↑↓ Navigate | Enter Edit | Ctrl+S Save | Esc Back", id="prompt-footer"),
            id="prompt-container"
        )

    def on_mount(self):
        self.prompt_list = self.query_one("#prompt-list", RichLog)
        self.prompt_preview = self.query_one("#prompt-preview", RichLog)
        # Disable focus on RichLog widgets so they don't steal keyboard events
        self.prompt_list.can_focus = False
        self.prompt_preview.can_focus = False
        self.render_list()
        self.show_preview()

    def key_down(self):
        """Direct down key handler"""
        if self.selected_index < len(PROMPT_CATALOG) - 1:
            self.selected_index += 1
            self.render_list()
            self.show_preview()

    def key_up(self):
        """Direct up key handler"""
        if self.selected_index > 0:
            self.selected_index -= 1
            self.render_list()
            self.show_preview()

    def key_enter(self):
        """Direct enter key handler"""
        self.action_select_prompt()

    def render_list(self):
        """Render prompt list grouped by category"""
        self.prompt_list.clear()

        # Group prompts by category
        groups = {}
        for entry in PROMPT_CATALOG:
            group = entry["group"]
            if group not in groups:
                groups[group] = []
            groups[group].append(entry)

        # Render each group
        idx = 0
        for group_name, entries in groups.items():
            # Group header
            header_text = Text()
            header_text.append(f"\n{group_name}")
            header_text.stylize("bold yellow")
            self.prompt_list.write(header_text)

            for entry in entries:
                is_selected = (idx == self.selected_index)
                prefix = "▸ " if is_selected else "  "
                item_text = Text()
                item_text.append(f"{prefix}{entry['name']}")
                item_text.stylize("bold cyan" if is_selected else "cyan")
                self.prompt_list.write(item_text)
                idx += 1

    def show_preview(self):
        """Show preview of selected prompt"""
        self.prompt_preview.clear()

        entry = self.get_entry(self.selected_index)
        if not entry:
            return

        file_path = self.project_root / entry["file"]
        if not file_path.exists():
            preview = Text(f"⚠️ File not found: {entry['file']}", style="red")
            self.prompt_preview.write(preview)
            return

        # Title
        title = Text()
        title.append(f"\n📄 {entry['name']}\n", style="bold cyan")
        title.append(f"   {entry['description']}\n", style="dim")
        title.append(f"   Path: {entry['file']}\n", style="italic dim")
        self.prompt_preview.write(title)

        # Separator
        self.prompt_preview.write(Text("─" * 40, style="dim"))

        # File content (first 30 lines)
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                lines = f.readlines()

            total_lines = len(lines)
            preview_lines = lines[:min(40, total_lines)]

            for line in preview_lines:
                # Use Text() to avoid markup parsing errors
                self.prompt_preview.write(Text(line.rstrip()))

            if total_lines > 40:
                self.prompt_preview.write(Text(f"\n... ({total_lines - 40} more lines)", style="dim"))
        except Exception as e:
            self.prompt_preview.write(Text(f"Error reading file: {e}", style="red"))

    def get_entry(self, index):
        """Get prompt catalog entry by flat index"""
        if 0 <= index < len(PROMPT_CATALOG):
            return PROMPT_CATALOG[index]
        return None

    def action_close(self):
        """Close prompt editor"""
        self.dismiss(None)

    def action_select_prompt(self):
        """Open selected prompt for editing"""
        entry = self.get_entry(self.selected_index)
        if not entry:
            return

        file_path = self.project_root / entry["file"]
        if not file_path.exists():
            return

        # Read full file content
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception as e:
            return

        # Switch to editor screen
        self.selected_entry = entry
        self.app.push_screen(PromptEditScreen(entry, content))


class PromptEditScreen(ModalScreen):
    """Full-screen text editor for prompt files"""

    CSS = """
    PromptEditScreen {
        align: center middle;
        background: $surface 90%;
    }

    #edit-container {
        width: 90%;
        height: 90%;
        border: thick $primary;
        background: $surface;
    }

    #edit-header {
        height: 3;
        background: $primary;
        color: $text;
        content-align: center middle;
        text-style: bold;
    }

    #edit-area {
        height: 1fr;
        border: solid $accent;
    }

    TextArea {
        width: 100%;
        height: 100%;
    }

    #edit-footer {
        height: 3;
        background: $accent;
        color: $text;
        content-align: center middle;
    }
    """

    BINDINGS = [
        Binding("escape", "close", "Discard"),
        Binding("ctrl+s", "save", "Save"),
        Binding("ctrl+q", "close", "Quit"),
    ]

    def __init__(self, entry, content):
        super().__init__()
        self.entry = entry
        self.original_content = content
        self.file_path = Path(__file__).parent.parent.parent / entry["file"]

    def compose(self) -> ComposeResult:
        yield Container(
            Static(f"✏️ Editing: {self.entry['name']} [{self.entry['file']}]", id="edit-header"),
            TextArea(self.original_content, id="edit-area", language="markdown"),
            Static("Ctrl+S Save | Esc Discard changes | Ctrl+Q Quit", id="edit-footer"),
            id="edit-container"
        )

    def on_mount(self):
        self.query_one(TextArea).focus()

    def action_close(self):
        """Discard changes and go back"""
        self.dismiss(None)

    def action_save(self):
        """Save changes to file"""
        editor = self.query_one(TextArea)
        new_content = editor.text

        try:
            self.file_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.file_path, "w", encoding="utf-8") as f:
                f.write(new_content)

            self.dismiss({
                "success": True,
                "file": str(self.file_path),
                "name": self.entry["name"]
            })
        except Exception as e:
            self.dismiss({
                "success": False,
                "error": str(e),
                "file": str(self.file_path),
                "name": self.entry["name"]
            })
