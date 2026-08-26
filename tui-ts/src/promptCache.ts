/**
 * Prompt cache — port of TUI/widgets/prompt_cache.py.
 * Caches long user prompts (15+ chars), keeps 5 most recent unique entries,
 * stored at runtime_memory/prompt_cache.json (same file as the Python TUI).
 */
import { readFileSync, writeFileSync, mkdirSync, existsSync } from "node:fs";
import path from "node:path";
import { PROJECT_ROOT } from "./gateway/GatewayClient.js";

const CACHE_FILE = path.join(PROJECT_ROOT, "runtime_memory", "prompt_cache.json");
const MIN_LENGTH = 15;
const MAX_ENTRIES = 5;

export interface CacheEntry {
  text: string;
  last_selected: number;
}

function loadCache(): CacheEntry[] {
  if (!existsSync(CACHE_FILE)) return [];
  try {
    const data = JSON.parse(readFileSync(CACHE_FILE, "utf8"));
    if (Array.isArray(data)) {
      return data.filter((e) => e && typeof e.text === "string");
    }
  } catch { /* corrupted cache → start fresh */ }
  return [];
}

function saveCache(cache: CacheEntry[]): void {
  mkdirSync(path.dirname(CACHE_FILE), { recursive: true });
  writeFileSync(CACHE_FILE, JSON.stringify(cache, null, 2), "utf8");
}

export function detectAndCache(message: string): void {
  if (message.length < MIN_LENGTH) return;
  const cache = loadCache();
  const now = Date.now() / 1000;

  const existing = cache.find((e) => e.text === message);
  if (existing) {
    existing.last_selected = now;
    saveCache(cache);
    return;
  }

  cache.push({ text: message, last_selected: now });
  if (cache.length > MAX_ENTRIES) {
    cache.sort((a, b) => a.last_selected - b.last_selected);
    cache.shift();
  }
  saveCache(cache);
}

export function getCachedPrompts(): CacheEntry[] {
  return loadCache().sort((a, b) => b.last_selected - a.last_selected);
}

export function touchPrompt(text: string): void {
  const cache = loadCache();
  const entry = cache.find((e) => e.text === text);
  if (entry) {
    entry.last_selected = Date.now() / 1000;
    saveCache(cache);
  }
}

export function removePrompt(text: string): void {
  saveCache(loadCache().filter((e) => e.text !== text));
}
