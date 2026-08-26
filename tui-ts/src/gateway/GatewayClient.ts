/**
 * GatewayClient — manages the gateway subprocess and NDJSON JSON-RPC transport.
 *
 * Port of TUI/gateway_client.py semantics:
 * - spawn `python -u TUI/gateway_entry.py` with cwd=project root, PYTHONIOENCODING=utf-8
 * - NDJSON line protocol over stdout; stderr captured to ring buffer + log file
 * - request/response correlation by id with 30s timeout
 * - crash semantics: EOF before `gateway.ready` → synthesize `gateway.init_error`;
 *   EOF after ready → synthesize `agent.error`; intentional kill suppresses both
 * - 5s heartbeat: auto-restart if the subprocess died
 * - forceKill via `taskkill /pid <pid> /T /F` on Windows
 */

import { EventEmitter } from "node:events";
import { spawn, execFile, type ChildProcess } from "node:child_process";
import { appendFileSync, mkdirSync, fsyncSync, openSync, closeSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import type { MethodName, RpcMethods, RpcResponse } from "./protocol.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
// tui-ts/src/gateway → project root is ../../..
export const PROJECT_ROOT = path.resolve(__dirname, "..", "..", "..");

const LOG_DIR = path.join(PROJECT_ROOT, "runtime_memory", "logs", "tui");
mkdirSync(LOG_DIR, { recursive: true });
const CLIENT_LOG = path.join(LOG_DIR, "gateway_client_ts.log");
const TRACE_LOG = path.join(LOG_DIR, "trace_ts.log");

function log(level: string, msg: string): void {
  const ts = new Date().toISOString().replace("T", " ").slice(0, 19);
  try {
    appendFileSync(CLIENT_LOG, `${ts} [${level}] ${msg}\n`, "utf8");
  } catch { /* logging must never crash the client */ }
}

function trace(msg: string): void {
  try {
    const now = new Date();
    const ts = now.toISOString().replace("T", " ").slice(0, 23);
    const fd = openSync(TRACE_LOG, "a");
    appendFileSync(fd, `${ts} [GC-TS] ${msg}\n`, "utf8");
    fsyncSync(fd);
    closeSync(fd);
  } catch { /* ignore */ }
}

const REQUEST_TIMEOUT_MS = 30_000;
const HEARTBEAT_INTERVAL_MS = 5_000;
const STDERR_RING_SIZE = 50;

export interface GatewayClientOptions {
  /** Spawn the mock gateway (test/mock-gateway.mjs) instead of the real one. */
  mock?: boolean;
  /** Extra args for the mock gateway, e.g. ["--crash-after-ready"]. */
  mockArgs?: string[];
}

interface PendingRequest {
  resolve: (result: unknown) => void;
  reject: (err: Error) => void;
  timer: NodeJS.Timeout;
}

export declare interface GatewayClient {
  on(event: string, listener: (payload: any) => void): this;
}

export class GatewayClient extends EventEmitter {
  private proc: ChildProcess | null = null;
  private pending = new Map<string, PendingRequest>();
  private requestId = 0;
  private stdoutBuf = "";
  private stderrRing: string[] = [];
  private readyReceived = false;
  private killedIntentionally = false;
  private running = false;
  private heartbeatTimer: NodeJS.Timeout | null = null;
  private readonly mock: boolean;
  private readonly mockArgs: string[];

  constructor(opts: GatewayClientOptions = {}) {
    super();
    this.mock = opts.mock ?? false;
    this.mockArgs = opts.mockArgs ?? [];
  }

  get pid(): number | null {
    return this.proc?.pid ?? null;
  }

  start(): void {
    if (this.proc) return;

    this.readyReceived = false;
    this.stderrRing = [];

    const command = this.mock ? process.execPath : (process.env.PYTHON ?? "python");
    const args = this.mock
      ? [path.join(PROJECT_ROOT, "tui-ts", "test", "mock-gateway.mjs"), ...this.mockArgs]
      : ["-u", path.join(PROJECT_ROOT, "TUI", "gateway_entry.py")];
    const env = { ...process.env, PYTHONIOENCODING: "utf-8" };

    log("INFO", `Starting gateway: ${command} ${args.join(" ")}`);
    this.proc = spawn(command, args, {
      cwd: PROJECT_ROOT,
      env,
      stdio: ["pipe", "pipe", "pipe"],
    });
    this.running = true;
    this.killedIntentionally = false;
    log("INFO", `Gateway started, pid=${this.proc.pid}`);

    this.proc.stdout!.setEncoding("utf8");
    this.proc.stderr!.setEncoding("utf8");
    this.proc.stdout!.on("data", (chunk: string) => this._onStdoutData(chunk));
    this.proc.stderr!.on("data", (chunk: string) => this._onStderrData(chunk));
    this.proc.on("close", (code) => this._onClose(code));
    this.proc.on("error", (err) => {
      log("ERROR", `Gateway spawn error: ${err.message}`);
      this._synthesizeCrashError(`Gateway spawn failed: ${err.message}`);
    });

    this._startHeartbeat();
  }

  stop(): void {
    trace("stop() called");
    this.running = false;
    this._stopHeartbeat();
    if (!this.proc) return;
    this.killedIntentionally = true;
    try {
      this.proc.stdin!.end();
      const proc = this.proc;
      const forceTimer = setTimeout(() => {
        try { proc.kill(); } catch { /* already dead */ }
      }, 2000);
      proc.once("close", () => clearTimeout(forceTimer));
    } catch {
      try { this.proc.kill(); } catch { /* ignore */ }
    }
    this.proc = null;
  }

  forceKill(): void {
    if (!this.proc) return;
    this.killedIntentionally = true;
    this.running = false;
    this._stopHeartbeat();
    const pid = this.proc.pid;
    trace(`forceKill() pid=${pid}`);
    try { this.proc.stdin!.end(); } catch { /* ignore */ }
    if (pid !== undefined && process.platform === "win32") {
      execFile("taskkill", ["/pid", String(pid), "/T", "/F"], () => { /* best effort */ });
    } else {
      try { this.proc.kill(); } catch { /* ignore */ }
    }
    this.proc = null;
  }

  restart(): void {
    this.forceKill();
    this.killedIntentionally = false;
    this.start();
  }

  call<M extends MethodName>(
    method: M,
    params: RpcMethods[M]["params"],
  ): Promise<RpcMethods[M]["result"]> {
    if (!this.proc?.stdin?.writable) {
      return Promise.reject(new Error("Gateway not started"));
    }
    const id = String(++this.requestId);
    const request = { jsonrpc: "2.0", method, params, id };

    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error(`Request timeout: ${method}`));
      }, REQUEST_TIMEOUT_MS);
      this.pending.set(id, {
        resolve: resolve as (r: unknown) => void,
        reject,
        timer,
      });
      try {
        this.proc!.stdin!.write(JSON.stringify(request) + "\n", "utf8");
      } catch (err) {
        clearTimeout(timer);
        this.pending.delete(id);
        reject(new Error(`Failed to send request: ${err}`));
      }
    });
  }

  // ── internal ──────────────────────────────────────────────────────

  private _onStdoutData(chunk: string): void {
    if (process.env.GOHARU_TUI_DEBUG) {
      try {
        appendFileSync(CLIENT_LOG, `[RAW] ${JSON.stringify(chunk.slice(0, 500))}\n`, "utf8");
      } catch { /* ignore */ }
    }
    this.stdoutBuf += chunk;
    for (;;) {
      const nl = this.stdoutBuf.indexOf("\n");
      if (nl < 0) break;
      const line = this.stdoutBuf.slice(0, nl).trim();
      this.stdoutBuf = this.stdoutBuf.slice(nl + 1);
      if (!line) continue;
      let message: RpcResponse | { method: string; params: { type: string; payload: unknown } };
      try {
        message = JSON.parse(line);
      } catch {
        log("DEBUG", `Skipped malformed JSON: ${line.slice(0, 100)}`);
        continue;
      }
      this._handleMessage(message as RpcResponse);
    }
  }

  private _onStderrData(chunk: string): void {
    for (const line of chunk.split(/\r?\n/)) {
      const stripped = line.trim();
      if (!stripped) continue;
      this.stderrRing.push(stripped);
      if (this.stderrRing.length > STDERR_RING_SIZE) this.stderrRing.shift();
      log("DEBUG", `stderr: ${stripped}`);
    }
  }

  private _handleMessage(message: RpcResponse): void {
    const maybeEvent = message as unknown as { method?: string; params?: { type?: string; payload?: unknown } };
    if (maybeEvent.method === "event") {
      const type = maybeEvent.params?.type ?? "";
      const payload = maybeEvent.params?.payload ?? {};
      if (type === "gateway.ready") {
        this.readyReceived = true;
        log("INFO", "Gateway ready signal received");
      }
      if (!["agent.streaming", "agent.thinking_content", "token.stats"].includes(type)) {
        log("DEBUG", `Event: ${type}`);
      }
      this.emit(type, payload);
      this.emit("*", { type, payload });
      return;
    }
    if (message.id !== undefined) {
      const entry = this.pending.get(String(message.id));
      if (!entry) return;
      this.pending.delete(String(message.id));
      clearTimeout(entry.timer);
      if (message.error) {
        entry.reject(new Error(message.error.message));
      } else {
        entry.resolve(message.result);
      }
    }
  }

  private _onClose(code: number | null): void {
    log("INFO", `Gateway closed, code=${code}`);
    this._rejectAllPending(new Error("Gateway process closed"));

    if (this.killedIntentionally) return;
    if (!this.running) return;

    const stderrTail = this.stderrRing.slice(-5).join("\n") || "(no stderr output)";
    if (!this.readyReceived) {
      log("ERROR", `Gateway exited before ready. Stderr tail: ${stderrTail}`);
      this.emit("gateway.init_error", {
        message: `Gateway subprocess exited unexpectedly before initialization completed.\n\nStderr:\n${stderrTail}`,
      });
    } else {
      this.emit("agent.error", {
        message: `Gateway subprocess crashed unexpectedly (exit=${code}).\n\nStderr:\n${stderrTail}`,
      });
    }
  }

  private _synthesizeCrashError(message: string): void {
    if (this.killedIntentionally) return;
    this.emit(this.readyReceived ? "agent.error" : "gateway.init_error", { message });
  }

  private _rejectAllPending(err: Error): void {
    for (const entry of this.pending.values()) {
      clearTimeout(entry.timer);
      entry.reject(err);
    }
    this.pending.clear();
  }

  private _startHeartbeat(): void {
    this._stopHeartbeat();
    this.heartbeatTimer = setInterval(() => {
      if (!this.proc) return;
      if (this.proc.exitCode !== null || this.proc.killed) {
        log("WARN", `Heartbeat: gateway died (pid=${this.proc.pid}, exit=${this.proc.exitCode}), restarting...`);
        trace(`Gateway died (pid=${this.proc.pid}), restarting`);
        this.proc = null;
        try {
          this.restart();
          log("INFO", `Gateway restarted, new pid=${this.pid}`);
        } catch (err) {
          log("CRITICAL", `Gateway restart failed: ${err}`);
        }
      }
    }, HEARTBEAT_INTERVAL_MS);
    this.heartbeatTimer.unref();
  }

  private _stopHeartbeat(): void {
    if (this.heartbeatTimer) {
      clearInterval(this.heartbeatTimer);
      this.heartbeatTimer = null;
    }
  }
}
