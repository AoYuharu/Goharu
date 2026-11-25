/**
 * ChannelsEditorScreen 渲染契约测试（ink-testing-library，无真实 gateway）。
 *
 * 覆盖 2026-11-25 需求：
 * - 列表/详情显示 qqbot 机器人名（health.bot_name）
 * - AppID/AppSecret 字段始终可见（凭据在 .env，不在 params）
 * - 不存在"保存到 config.yaml"二次确认项（回车即保存）
 * - harunet 只有一个接入密码入口（字段本身，无重复 action）
 *
 * 行序来自真实 config.yaml（组件直读），测试动态定位 qqbot/harunet 行。
 *
 * 运行：npx tsx test/channels-editor.test.tsx
 */
import React from "react";
import { render } from "ink-testing-library";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import YAML from "yaml";
import { ChannelsEditorScreen } from "../src/screens/ChannelsEditorScreen.js";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");

let passed = 0, failed = 0;
function check(name: string, cond: boolean) {
  if (cond) { passed++; console.log(`  PASS  ${name}`); }
  else { failed++; console.log(`  FAIL  ${name}`); }
}

const HEALTH = [
  {
    session: "default", type: "qqbot", channel_id: "qq_main", name: "qq_main",
    ok: true, state: "online", detail: "已连接 QQ 官方 WebSocket",
    params: { require_mention: true, priority: "high" },
    bot_name: "Kei", app_id: "1903496248", has_secret: true,
  },
  {
    session: "default", type: "harunet", channel_id: "harunet_main", name: "goharu",
    ok: true, state: "online", detail: "已连接",
    params: { url: "ws://x", name: "goharu", password: "pw123", http_base: "", focus: "" },
  },
];

const props = {
  active: true,
  onClose: () => {},
  onSave: async () => "已保存",
  onResult: () => {},
  onHealth: async () => HEALTH,
  onReconnect: async () => ({ ok: true }),
  onSetPassword: async () => ({ ok: true }),
  onSetCredentials: async () => ({ ok: true }),
};

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

/** 复刻 buildRows 的行序：config sessions 顺序 + health-only 追加 */
function rowTypes(): string[] {
  const cfg = YAML.parse(readFileSync(path.join(ROOT, "config.yaml"), "utf8"),
                         { uniqueKeys: false }) ?? {};
  const types: string[] = [];
  for (const s of (cfg.sessions ?? [])) {
    for (const ch of (s.channels ?? [])) types.push(ch.type);
  }
  types.push("harunet");   // health-only，追加在末尾
  return types;
}

async function down(stdin: { write: (s: string) => void }, n: number) {
  for (let i = 0; i < n; i++) { stdin.write("\x1b[B"); await sleep(20); }
}

async function main() {
  const types = rowTypes();
  const qqIdx = types.indexOf("qqbot");
  const haruIdx = types.lastIndexOf("harunet");
  check("config 里有 qqbot 通道", qqIdx >= 0);

  const { lastFrame, stdin, unmount } = render(<ChannelsEditorScreen {...props} />);
  await sleep(80);   // 等 onHealth resolve
  check("列表无「保存」提示", !(lastFrame() ?? "").includes("保存到"));

  // ── qqbot 详情 ──
  await down(stdin, qqIdx);
  check("选中行显示机器人名 🤖 Kei", (lastFrame() ?? "").includes("Kei"));
  stdin.write("\r");
  await sleep(50);
  const detail = lastFrame() ?? "";
  check("详情显示 AppID 值", detail.includes("1903496248"));
  check("详情显示 AppSecret 已设置", detail.includes("已设置"));
  check("详情显示机器人名", detail.includes("Kei"));
  check("无「保存到 config.yaml」动作项", !detail.includes("保存到 config.yaml"));
  check("提示语为回车即保存", detail.includes("回车确认即保存"));

  // 回车编辑 AppID → 输入行预填当前值
  stdin.write("\r");
  await sleep(50);
  const editing = lastFrame() ?? "";
  check("AppID 编辑行预填当前值", editing.includes("AppID") && editing.includes("1903496248"));
  unmount();

  // ── harunet 详情：只有一个密码入口 ──
  const r2 = render(<ChannelsEditorScreen {...props} />);
  await sleep(80);
  await down(r2.stdin, haruIdx);
  r2.stdin.write("\r");
  await sleep(50);
  const hd = r2.lastFrame() ?? "";
  check("harunet 显示接入密码字段", hd.includes("接入密码"));
  check("harunet 无重复「修改接入密码」动作项", !hd.includes("修改接入密码"));
  const pwCount = (hd.match(/接入密码/g) ?? []).length;
  check("接入密码只出现一次", pwCount === 1);
  check("保留「立即重连」动作", hd.includes("立即重连"));
  r2.unmount();

  console.log(`\n结果: ${passed} 通过, ${failed} 失败`);
  process.exit(failed ? 1 : 0);
}

void main();
