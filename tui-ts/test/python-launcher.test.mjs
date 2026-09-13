/**
 * GatewayClient python 解释器选择的契约测试（bug #3 修复验证）。
 *
 * 验证 dist/gateway/GatewayClient.js（编译产物）中的选择逻辑：
 *   process.env.PYTHON ?? (process.platform === "win32" ? "python" : "python3")
 *
 * 两级验证：
 *   1. 契约：编译产物包含平台分支默认值与 PYTHON 环境变量优先逻辑
 *   2. 行为：从源码中抽取真实表达式，在模拟平台下 eval，断言三种场景取值正确
 *
 * 运行: node tui-ts/test/python-launcher.test.mjs
 */

import assert from "node:assert";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const TEST_DIR = path.dirname(fileURLToPath(import.meta.url));
const DIST_FILE = path.join(TEST_DIR, "..", "dist", "gateway", "GatewayClient.js");
const src = readFileSync(DIST_FILE, "utf8");

// ── 1. 契约：编译产物包含修复后的逻辑 ──
assert.ok(src.includes("process.env.PYTHON"), "缺少 PYTHON 环境变量优先逻辑");
assert.ok(src.includes('"python3"'), "缺少 POSIX 默认 python3");
assert.ok(src.includes('process.platform === "win32"'), "缺少平台分支");
console.log("[1] dist 契约：PYTHON 环境变量优先 + 平台分支默认值 存在 OK");

// ── 2. 行为：抽取真实表达式，模拟三平台断言 ──
const defaultMatch = src.match(/const defaultPython = (.+?);/);
const commandMatch = src.match(/const command = this\.mock \? process\.execPath : \((.+?)\);/);
assert.ok(defaultMatch && commandMatch, "无法从 dist 抽取选择表达式");

function resolveCommand(platform, pythonEnv) {
  const proc = { platform, execPath: "/usr/bin/node", env: { PYTHON: pythonEnv } };
  const defaultPython = eval(`(${defaultMatch[1].replaceAll("process.", "proc.")})`);
  return eval(`(${commandMatch[1].replaceAll("process.", "proc.")})`);
}

// Windows 默认 → python
assert.strictEqual(resolveCommand("win32", undefined), "python");
// Linux/macOS 默认 → python3
assert.strictEqual(resolveCommand("linux", undefined), "python3");
assert.strictEqual(resolveCommand("darwin", undefined), "python3");
// PYTHON 环境变量在所有平台优先
assert.strictEqual(resolveCommand("win32", "/opt/py/bin/python"), "/opt/py/bin/python");
assert.strictEqual(resolveCommand("linux", "/opt/py/bin/python"), "/opt/py/bin/python");
console.log("[2] 行为：win32→python / linux→python3 / darwin→python3 / PYTHON 全覆盖优先 OK");

console.log("\n=== GatewayClient python 解释器选择：全部通过 ===");
