/**
 * Prompt cache — port of TUI/widgets/prompt_cache.py.
 * Caches long user prompts (15+ chars), keeps 5 most recent unique entries,
 * stored at runtime_memory/prompt_cache.json (same file as the Python TUI).
 */
import { readFileSync, writeFileSync, mkdirSync, existsSync } from "node:fs";
import path from "node:path";
import { PROJECT_ROOT } from "./gateway/GatewayClient.js";

const DEFAULT_CACHE_FILE = path.join(PROJECT_ROOT, "runtime_memory", "prompt_cache.json");
/** Test isolation: GOHARU_PROMPT_CACHE overrides the cache file location. */
function cacheFile(): string {
  return process.env.GOHARU_PROMPT_CACHE
    ? path.resolve(process.env.GOHARU_PROMPT_CACHE)
    : DEFAULT_CACHE_FILE;
}
const MIN_LENGTH = 15;
const MAX_ENTRIES = 5;

export interface CacheEntry {
  text: string;
  last_selected: number;
}

function loadCache(): CacheEntry[] {
  const file = cacheFile();
  if (!existsSync(file)) return [];
  try {
    const data = JSON.parse(readFileSync(file, "utf8"));
    if (Array.isArray(data)) {
      return data.filter((e) => e && typeof e.text === "string");
    }
  } catch { /* corrupted cache → start fresh */ }
  return [];
}

function saveCache(cache: CacheEntry[]): void {
  const file = cacheFile();
  mkdirSync(path.dirname(file), { recursive: true });
  writeFileSync(file, JSON.stringify(cache, null, 2), "utf8");
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
