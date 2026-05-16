"""
Prompt Cache Widget

Detects long user prompts (15+ chars) on submit, caches up to 5 most recent
unique prompts, and provides a /prompt-cache selection screen sorted by
last-selected time.
"""

import json
import time
from pathlib import Path

from textual.screen import ModalScreen
from textual.app import ComposeResult
from textual.containers import Container, Vertical
from textual.widgets import Static
from textual.binding import Binding

CACHE_FILE = Path("./runtime_memory/prompt_cache.json")
MIN_LENGTH = 15
MAX_ENTRIES = 5


def _load_cache() -> list:
    """Load cached prompts from file, returns list of {text, last_selected}."""
    if not CACHE_FILE.exists():
        return []
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return [e for e in data if isinstance(e, dict) and "text" in e]
    except (json.JSONDecodeError, OSError):
        pass
    return []


def _save_cache(cache: list):
    """Write cache list to file."""
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


def detect_and_cache(message: str):
    """Check if message qualifies for caching (15+ chars).

    If already in cache, bumps last_selected. Otherwise adds new entry,
    evicting the least-recently-selected one when over MAX_ENTRIES.
    """
    if len(message) < MIN_LENGTH:
        return

    cache = _load_cache()
    now = time.time()

    # Check if already in cache
    for entry in cache:
        if entry["text"] == message:
            entry["last_selected"] = now
            _save_cache(cache)
            return

    # Add new entry
    cache.append({"text": message, "last_selected": now})

    # Evict oldest by last_selected if over limit
    if len(cache) > MAX_ENTRIES:
        cache.sort(key=lambda e: e["last_selected"])
        cache.pop(0)

    _save_cache(cache)


def get_cached_prompts() -> list:
    """Return cached prompts sorted by last_selected descending (most recent first)."""
    cache = _load_cache()
    cache.sort(key=lambda e: e["last_selected"], reverse=True)
    return cache


def touch_prompt(text: str):
    """Update last_selected for a cached prompt when user picks it."""
    cache = _load_cache()
    now = time.time()
    for entry in cache:
        if entry["text"] == text:
            entry["last_selected"] = now
            break
    _save_cache(cache)


def remove_prompt(text: str):
    """Remove a prompt from the cache."""
    cache = _load_cache()
    cache = [e for e in cache if e["text"] != text]
    _save_cache(cache)


class PromptCacheScreen(ModalScreen):
    """Modal screen for browsing and selecting cached prompts."""

    CSS = """
    PromptCacheScreen {
        align: center middle;
        background: $surface 80%;
    }

    #cache-container {
        width: 85%;
        height: auto;
        max-height: 80%;
        border: thick $primary;
        background: $surface;
    }

    #cache-header {
        height: 3;
        background: $primary;
        color: $text;
        content-align: center middle;
        text-style: bold;
    }

    #cache-list {
        height: auto;
        max-height: 18;
        padding: 0 1;
        border: solid $accent;
        background: $surface;
        overflow-y: auto;
    }

    #cache-footer {
        height: 3;
        background: $accent;
        color: $text;
        content-align: center middle;
    }

    .cache-item {
        padding: 0 1;
        height: 1;
    }

    .cache-item.selected {
        background: $primary;
        color: $text;
    }

    .cache-item.normal {
        color: $text;
    }
    """

    BINDINGS = [
        Binding("escape", "close", "Back"),
        Binding("delete", "delete_entry", "Delete"),
    ]

    def __init__(self):
        super().__init__()
        self.entries = get_cached_prompts()
        self.selected_index = 0

    def compose(self) -> ComposeResult:
        yield Container(
            Static("📋 Prompt Cache — Recently Used Long Prompts", id="cache-header"),
            Vertical(id="cache-list"),
            Static("↑↓ Navigate | Enter Select | Delete Remove | Esc Back", id="cache-footer"),
            id="cache-container",
        )

    def on_mount(self):
        self.cache_list = self.query_one("#cache-list", Vertical)
        self._render()

    def _render(self):
        """Rebuild the item list from self.entries."""
        self.cache_list.remove_children()
        if not self.entries:
            self.cache_list.mount(
                Static("  (no cached prompts — type a message >=15 characters)", classes="cache-item normal")
            )
            return

        for i, entry in enumerate(self.entries):
            css_class = "cache-item selected" if i == self.selected_index else "cache-item normal"
            preview = entry["text"].replace("\n", " ")[:100]
            label = f"  {'▸' if i == self.selected_index else ' '} {preview}"
            if len(entry["text"]) > 100:
                label += "…"
            self.cache_list.mount(Static(label, classes=css_class))

    def key_down(self):
        if self.entries and self.selected_index < len(self.entries) - 1:
            self.selected_index += 1
            self._render()

    def key_up(self):
        if self.selected_index > 0:
            self.selected_index -= 1
            self._render()

    def key_enter(self):
        if not self.entries:
            return
        selected = self.entries[self.selected_index]
        touch_prompt(selected["text"])
        self.dismiss(selected["text"])

    def action_close(self):
        self.dismiss(None)

    def action_delete_entry(self):
        if not self.entries:
            return
        entry = self.entries[self.selected_index]
        remove_prompt(entry["text"])
        self.entries = get_cached_prompts()
        if self.selected_index >= len(self.entries) and self.entries:
            self.selected_index = len(self.entries) - 1
        self._render()
