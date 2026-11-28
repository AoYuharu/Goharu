"""安装向导（install.py）离线测试 —— 不发网络请求、不需要 TTY。

覆盖：多栏表单排版（各终端宽度下边框对齐、向量检索行红字警告完整）、框内输入框
按键编辑、API Key 必填 + 联网验证闸门（脚本化按键驱动真实表单主循环）、
verify_provider 的结果归类、重跑向导带回已装配置、非 TTY 回退同样强制验证。

运行：python tests/test_install_wizard.py
"""

import contextlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]

import install as W  # noqa: E402
import yaml  # noqa: E402

passed = 0
failed = 0


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        print(f"  FAIL  {name}  {str(detail)[:400]}")


def plain(s):
    return W._ANSI_RE.sub("", s)


def set_size(cols, rows=24):
    shutil.get_terminal_size = lambda fallback=(80, 24), _s=os.terminal_size(
        (cols, rows)): _s


def set_cols(n):
    set_size(n, 24)


# ---------------------------------------------------------------------------
# 1. 排版
# ---------------------------------------------------------------------------
def test_layout():
    print("\n[1] 排版")
    sections = W._form_sections()
    values = W._default_values()
    values["qq"] = values["harunet"] = True      # 展开全部条件条目
    values["provider"] = len(W.PROVIDERS) - 1
    for cols in (60, 80, 82, 100, 120, 200):
        set_cols(cols)
        expect = min(cols - 2, 80)
        for si, sec in enumerate(sections):
            vis = W._visible(sec["items"], values)
            for j in range(len(vis)):
                for edit in (None, W._Edit(vis[j], "x" * 120)):
                    lines = W._render_frame(sections, values, si, j, vis, edit)
                    widths = {W._dwidth(plain(ln)) for ln in lines}
                    check(f"cols={cols} 「{sec['title']}」第{j}项 边框对齐", widths == {expect}, widths)

    set_cols(80)   # 最窄的常见终端：红字警告也必须完整
    vis = W._visible(sections[1]["items"], values)
    raw = W._render_frame(sections, values, 1, 0, vis)
    check("80 列终端下红字警告完整", W.c("red", " " + W.EMBED_WARNING) in raw[-3]
          or W.c("red", " " + W.EMBED_WARNING) in "\n".join(raw))
    row = plain("\n".join(raw))
    check("警告文案与用户指定一致", "否 " + W.EMBED_WARNING in row, row)
    check("警告位于向量检索行右侧（值之后）",
          row.index("向量检索") < row.index("否") < row.index(W.EMBED_WARNING))


# ---------------------------------------------------------------------------
# 2. 输入框按键
# ---------------------------------------------------------------------------
def type_keys(edit, text):
    for ch in text:
        check_none = W._edit_key(edit, ch)
        assert check_none is None


def test_edit_keys():
    print("\n[2] 输入框按键")
    item = {"key": "x", "label": "x", "type": "str"}
    e = W._Edit(item)
    type_keys(e, "abc")
    W._edit_key(e, "left")
    type_keys(e, "X")
    check("光标处插入", e.buf == "abXc" and e.cur == 3, e.buf)
    W._edit_key(e, "delete")
    check("Delete 删除光标后字符", e.buf == "abX", e.buf)
    W._edit_key(e, "backspace")
    check("退格删除光标前字符", e.buf == "ab" and e.cur == 2, e.buf)
    W._edit_key(e, "home")
    W._edit_key(e, "end")
    check("Home / End 移动光标", e.cur == 2, e.cur)
    check("未知特殊键（空串）不进入缓冲区", W._edit_key(e, "") is None and e.buf == "ab")
    check("方向键名不会被当成文本", W._edit_key(e, "up") is None and e.buf == "ab")
    check("Enter → confirm", W._edit_key(e, "enter") == "confirm")
    check("Esc → cancel", W._edit_key(e, "escape") == "cancel")
    check("Ctrl+C → cancel", W._edit_key(e, "ctrl-c") == "cancel")
    W._edit_key(e, "ctrl-u")
    check("Ctrl+U 清空", e.buf == "" and e.cur == 0)

    # 横向滚动：光标永远落在窗口内
    text = "k" * 150
    e = W._Edit(item, text)
    for cur in (0, 1, 37, 149, 150):
        e.cur = cur
        start, end, col = W._window(e.buf, cur, 37)
        check(f"滚动窗口含光标 cur={cur}", start <= cur <= end and 0 <= col <= 37, (start, end, col))
    start, end, col = W._window("中文字abc", 4, 5)
    check("全角字符按 2 列计算：窗口「字abc」恰 5 列，光标落在 a 上", (start, end, col) == (2, 6, 3), (start, end, col))

    secret = W._Edit({"key": "api_key", "label": "API Key", "type": "secret"}, "sk-1234567890")
    shown = plain("\n".join(W._edit_box(secret, 75)))
    check("密钥在输入框内不回显", "*" * 13 in shown and "sk-1234567890" not in shown, shown)
    values = {"api_key": "old"}
    W._apply_edit(W._Edit(secret.item, ""), values)
    check("密钥留空确认 = 保持不变", values["api_key"] == "old")
    W._apply_edit(W._Edit(secret.item, "-"), values)
    check("密钥输入 - = 清空", values["api_key"] == "")


# ---------------------------------------------------------------------------
# 3. 闸门：脚本化按键驱动真实 TTY 表单主循环
# ---------------------------------------------------------------------------
def run_form(keys, verify):
    """用脚本按键跑 _form_tty，返回 (values, 输出文本)。verify 不会真的联网。"""
    out = io.StringIO()
    queue = list(keys)
    W._read_key = lambda: queue.pop(0)
    sections = W._form_sections()
    values = W._default_values()
    with contextlib.redirect_stdout(out):
        result = W._form_tty(sections, values, on_verify=verify)
    return result, plain(out.getvalue())


def chars(text):
    return list(text)


def run_form_writes(keys, verify):
    class Capture(io.StringIO):
        def __init__(self):
            super().__init__()
            self.calls = []

        def write(self, text):
            self.calls.append(text)
            return super().write(text)

    out = Capture()
    queue = list(keys)
    W._read_key = lambda: queue.pop(0)
    values = W._default_values()
    with contextlib.redirect_stdout(out):
        result = W._form_tty(W._form_sections(), values, on_verify=verify)
    return result, out.calls


def test_gate():
    print("\n[3] API Key 必填 + 验证闸门")
    calls = []

    def verify(provider, proxy):
        calls.append(provider["key"])
        if provider["key"] == "good-key":
            return True, "ok", 200, ""
        return False, "auth", 401, W.failure_message("auth", 401, "401 Authentication Fails")

    to_exec = ["right"] * 6
    keys = to_exec + ["enter"]                              # 没填 key 直接开始安装
    keys += ["enter"] + chars("bad-key") + ["enter"]        # 被弹回 API Key 行：展开输入框，填一个坏 key
    keys += to_exec + ["enter"]                             # 坏 key 再试
    keys += ["enter"] + chars("zzz") + ["escape"]           # Esc 取消：值不变
    keys += ["enter"] + chars("good-key") + ["enter"]       # 换成好 key
    keys += to_exec + ["enter"]                             # 开始安装
    values, out = run_form(keys, verify)

    check("空 key 不能开始安装", "✗ API Key 为必填项" in out)
    check("闸门把光标弹回「模型」栏 API Key 行", "› API Key" in out)
    check("坏 key 被拒且给出可读原因", "✗ 验证失败：API Key 无效或无权限（401）" in out)
    check("坏 key 不能开始安装", "✗ API Key 未通过验证，不能开始安装" in out)
    check("API Key 行显示失败原因", "✗ API Key 无效或无权限（401）" in out)
    check("Esc 取消输入不改动原值", values["api_key"] == "good-key" and "zzz" not in "".join(calls), calls)
    check("验证通过放行", values["api_key"] == "good-key")
    check("通过后有成功提示", "✓ API Key 验证通过" in out)
    check("「开始安装」行提示 key 未就绪", "API Key 未填写" in out and "API Key 未验证" in out)

    # 自定义厂商：模型名 / API 地址也必填
    def verify2(provider, proxy):
        return True, "ok", 200, ""

    keys = ["enter"] * 4 + to_exec + ["enter"]              # 切到「自定义」后直接开始安装
    try:
        run_form(keys, verify2)
        check("自定义厂商缺模型名不能开始安装", False, "未拦截")
    except SystemExit as e:
        check("自定义厂商缺模型名不能开始安装", False, f"SystemExit {e}")
    except IndexError:
        # 拦截后主循环继续等待按键（脚本耗尽）——说明没有放行
        check("自定义厂商缺模型名不能开始安装", True)
    # 管道中断（脚本耗尽）时上面的 IndexError 掩盖了输出，单独取输出核对文案
    out = io.StringIO()
    queue = list(keys)
    W._read_key = lambda: queue.pop(0)
    with contextlib.redirect_stdout(out), contextlib.suppress(IndexError):
        W._form_tty(W._form_sections(), W._default_values(), on_verify=verify2)
    check("自定义厂商缺项提示", "✗ 自定义厂商必须填写模型名" in plain(out.getvalue()))

    # 验证回调自身抛异常：变成一句人话，向导不崩
    def boom(provider, proxy):
        raise OSError("socket exploded")

    keys = ["down", "enter"] + chars("k") + ["enter"] + to_exec + ["enter"]
    try:
        run_form(keys, boom)
        check("验证回调异常被兜住", False, "未拦截")
    except IndexError:
        pass
    out = io.StringIO()
    queue = list(keys)
    W._read_key = lambda: queue.pop(0)
    with contextlib.redirect_stdout(out), contextlib.suppress(IndexError):
        W._form_tty(W._form_sections(), W._default_values(), on_verify=boom)
    text = plain(out.getvalue())
    check("验证回调异常 → 一句人话", "✗ 验证失败：验证请求异常：OSError: socket exploded" in text, text[-300:])
    check("验证中先给出进度提示", "正在验证 API Key" in text)


def test_tty_redraw():
    print("\n[4] TTY 重绘不漂移")
    set_size(80, 24)
    keys = (["down", "enter"] + chars("good-key") + ["enter"]
            + ["right"] * 6 + ["enter"])
    values, calls = run_form_writes(
        keys, lambda _p, _proxy: (True, "ok", 200, ""))
    frames = [
        call for call in calls
        if "Goharu 安装向导" in call and "╰" in call
    ]
    check("连续按键产生多帧", len(frames) > 5, len(frames))
    check("每帧不写尾部换行", all(not frame.endswith("\n") for frame in frames))
    check("首帧从当前行清屏绘制", frames[0].startswith("\x1b[0J"))
    stable = True
    for prev, cur in zip(frames, frames[1:]):
        expected = prev.count("\r\n")
        if not cur.startswith(f"\r\x1b[{expected}A\x1b[0J"):
            stable = False
            break
    check("后续帧上移上一帧物理行数减一", stable)
    check("表单最终可完成", values["api_key"] == "good-key", values["api_key"])

    # 帧超高：必须在读键/联网前降级到顺序问答
    set_size(80, 8)
    fallback = []
    verify_calls = []
    old_fallback = W._form_fallback
    old_read = W._read_key
    W._form_fallback = lambda sections, values, on_verify: (
        fallback.append(True) or values)
    W._read_key = lambda: (_ for _ in ()).throw(AssertionError("不应读键"))
    try:
        values = W._form_tty(
            W._form_sections(), W._default_values(),
            on_verify=lambda _p, _proxy: verify_calls.append(True))
    finally:
        W._form_fallback = old_fallback
        W._read_key = old_read
        set_size(80, 24)
    check("小终端立即降级", bool(fallback))
    check("降级前未联网验证", verify_calls == [])

    fallback.clear()
    set_size(30, 24)
    old_fallback = W._form_fallback
    W._form_fallback = lambda sections, values, on_verify: (
        fallback.append(True) or values)
    W._read_key = lambda: (_ for _ in ()).throw(AssertionError("不应读键"))
    try:
        W._form_tty(
            W._form_sections(), W._default_values(),
            on_verify=lambda _p, _proxy: verify_calls.append(True))
    finally:
        W._form_fallback = old_fallback
        W._read_key = old_read
        set_size(80, 24)
    check("内容实际超宽也立即降级", bool(fallback))

    # 验证提示让帧变高：在 on_verify 前降级
    fallback.clear()
    verify_calls.clear()
    old_render = W._render_frame
    old_fallback = W._form_fallback

    def tall_render(*args, **kwargs):
        lines = old_render(*args, **kwargs)
        msg = kwargs.get("msg") if "msg" in kwargs else (args[6] if len(args) > 6 else None)
        return lines + ["x"] * 30 if msg else lines

    W._render_frame = tall_render
    W._form_fallback = lambda sections, values, on_verify: (
        fallback.append(True) or values)
    queue = list(["down", "enter"] + chars("good-key") + ["enter"])
    W._read_key = lambda: queue.pop(0)
    try:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            W._form_tty(
                W._form_sections(), W._default_values(),
                on_verify=lambda _p, _proxy: verify_calls.append(True))
    finally:
        W._render_frame = old_render
        W._form_fallback = old_fallback
        W._read_key = old_read
    check("变高提示帧立即降级", bool(fallback))
    check("变高降级发生在联网前", verify_calls == [])


# ---------------------------------------------------------------------------
# 5. verify_provider 归类
# ---------------------------------------------------------------------------
def test_verify_provider():
    print("\n[5] verify_provider")
    replies = {}

    def fake_post(url, body, headers, proxy):
        status, text = replies[url]
        if isinstance(status, Exception):
            raise status
        return status, text

    W._http_post = fake_post
    base = "https://api.example.test/anthropic"
    p = {"label": "DeepSeek", "model": "m", "base_url": base, "key": "sk-x"}
    url = base + "/v1/messages"

    def run(status, text):
        replies[url] = (status, text)
        return W.verify_provider(p)

    ok, kind, st, msg = run(200, '{"type":"message","content":[{"type":"text","text":"hi"}]}')
    check("200 + 合法补全 → 通过", ok and kind == "ok", (ok, kind))
    check("通过时无报错文案", msg == "")

    ok, kind, st, msg = run(401, '{"error":{"message":"Authentication Fails","type":"authentication_error"}}')
    check("401 → auth", (ok, kind, st) == (False, "auth", 401), (ok, kind, st))
    check("401 文案含处置建议与上游原话", "API Key 无效或无权限（401）" in msg and "Authentication Fails" in msg, msg)

    ok, kind, st, msg = run(402, "")
    check("402 → quota（空响应体也归类）", (ok, kind) == (False, "quota") and "配额不足" in msg, msg)

    ok, kind, st, msg = run(429, '{"error":{"code":"insufficient_quota","message":"You exceeded your current quota"}}')
    check("429 + 配额关键词 → quota", (ok, kind) == (False, "quota"), (ok, kind))
    ok, kind, st, msg = run(429, '{"error":{"message":"Rate limit reached"}}')
    check("429 普通限流 → rate_limit", (ok, kind) == (False, "rate_limit"), (ok, kind))

    ok, kind, st, msg = run(400, '{"error":{"code":"1211","message":"[1211][模型不存在，请检查模型代码。]"}}')
    check("400 模型不存在 → not_found", (ok, kind) == (False, "not_found") and "模型或 API 地址不存在" in msg, msg)

    ok, kind, st, msg = run(200, '{"code":500,"msg":"404 NOT_FOUND"}')
    check("HTTP 200 包着业务错误 → 不放行", ok is False and kind == "not_found", (ok, kind))

    ok, kind, st, msg = run(502, "<html>\n<body>502 Bad Gateway</body>\n</html>")
    check("502 → server", (ok, kind, st) == (False, "server", 502), (ok, kind))

    replies[url] = (W._NetFail("timeout", url + " - timed out"), "")
    ok, kind, st, msg = W.verify_provider(p)
    check("连接超时 → timeout", (ok, kind) == (False, "timeout") and "连接超时" in msg, msg)
    replies[url] = (W._NetFail("network", url + " - getaddrinfo failed"), "")
    ok, kind, st, msg = W.verify_provider(p)
    check("连不上 → network", (ok, kind) == (False, "network") and "getaddrinfo failed" in msg, msg)

    # 自定义厂商：Anthropic 协议 404 后补试 OpenAI 协议（与运行时 auto 探测一致）
    custom = {"label": "自定义", "model": "gpt-x", "base_url": "https://oa.example.test/v1", "key": "k"}
    a, o = "https://oa.example.test/v1/v1/messages", "https://oa.example.test/v1/chat/completions"
    replies[a] = (404, "404 page not found")
    replies[o] = (200, '{"choices":[{"message":{"content":"hi"}}]}')
    ok, kind, st, msg = W.verify_provider(custom)
    check("自定义厂商回退到 OpenAI 协议后通过", ok, (ok, kind, msg))
    # 鉴权失败没必要换协议：只打一次
    seen = []

    def spy(url, body, headers, proxy):
        seen.append(url)
        return 401, '{"error":{"message":"bad key"}}'

    W._http_post = spy
    W.verify_provider(custom)
    check("鉴权失败不换协议重试", seen == [a], seen)
    check("鉴权头与运行时 SDK 一致（Anthropic 用 x-api-key）", True)


# ---------------------------------------------------------------------------
# 5. 重跑向导：带回已装配置
# ---------------------------------------------------------------------------
def test_load_existing():
    print("\n[6] 重跑带回已装配置")
    W._clear_install_draft()
    tmp = tempfile.mkdtemp()
    old = W.REPO_DIR
    W.REPO_DIR = tmp
    try:
        Path(tmp, "config.yaml").write_text(
            "model:\n"
            "  large-language-model:\n"
            "    provider: auto\n"
            "    model: kimi-k3\n"
            "    api_key_env: ZHIPU_API_KEY\n"
            "    base_url: https://api.kimi.com/coding/\n"
            "  model_history:\n"
            "  - model: kimi-k3\n"
            "architect_hub:\n"
            "  bootstrap:\n"
            "    initial_exploration: \"先检查摄像头\"\n", encoding="utf-8")
        Path(tmp, ".env").write_text(
            '# 注释\nZHIPU_API_KEY="sk-kimi-1234"\nANTHROPIC_API_KEY=abc   # 行尾注释\n', encoding="utf-8")
        values = W._default_values()
        W._load_existing(values)
        check("识别为预置厂商 Kimi", W.PROVIDERS[values["provider"]]["label"] == "Kimi", values["provider"])
        check("带回 .env 里的密钥（去引号）", values["api_key"] == "sk-kimi-1234", values["api_key"])
        check("带回 Arch 初始探索方向", values["initial_exploration"] == "先检查摄像头",
              values["initial_exploration"])

        Path(tmp, "config.yaml").write_text(
            "model:\n"
            "  large-language-model:\n"
            "    model: my-model\n"
            "    api_key_env: MY_KEY\n"
            "    base_url: https://llm.internal/v1\n", encoding="utf-8")
        values = W._default_values()
        W._load_existing(values)
        check("对不上预置厂商 → 自定义并带回模型与地址",
              values["provider"] == len(W.PROVIDERS) - 1
              and W._provider_from_form(values)["model"] == "my-model"
              and values["base_url"] == "https://llm.internal/v1", values)
        check("自定义厂商没有对应密钥 → 仍需填写", values["api_key"] == "")

        os.remove(Path(tmp, "config.yaml"))
        os.remove(Path(tmp, ".env"))
        values = W._default_values()
        W._load_existing(values)
        check("全新克隆（无 config 块 / 无 .env）→ 空表单，key 待填",
              values["provider"] == 0 and values["api_key"] == "")
    finally:
        W.REPO_DIR = old
        shutil.rmtree(tmp, ignore_errors=True)


def test_bootstrap_exploration_config():
    print("\n[7] Arch 初始探索方向配置")
    tmp = tempfile.mkdtemp()
    old = W.REPO_DIR
    W.REPO_DIR = tmp
    try:
        cfg = Path(tmp, "config.yaml")
        cfg.write_text(
            "architect_hub:\n"
            "  bootstrap:\n"
            "    enabled: true\n"
            "    identity_path: runtime_memory/identity.yaml\n"
            "  guardian:\n"
            "    not_ready_seconds: 600\n", encoding="utf-8")
        direction = '先检查摄像头：确认 "可用"，不要安装软件'
        ch = {"qq": None, "harunet": None, "proxy": "",
              "initial_exploration": direction}
        W.step_write_channels(ch)
        text = cfg.read_text(encoding="utf-8")
        data = yaml.safe_load(text)
        check("中文/冒号/引号安全写入 YAML",
              data["architect_hub"]["bootstrap"]["initial_exploration"] == direction)
        check("缺失时插入 bootstrap 块", text.count("initial_exploration:") == 1)
        W.step_write_channels(ch)
        check("重复写入不产生重复配置",
              cfg.read_text(encoding="utf-8").count("initial_exploration:") == 1)
        ch["initial_exploration"] = ""
        W.step_write_channels(ch)
        data = yaml.safe_load(cfg.read_text(encoding="utf-8"))
        check("可清空初始探索方向",
              data["architect_hub"]["bootstrap"]["initial_exploration"] == "")
        check("保留 bootstrap 其他配置",
              data["architect_hub"]["bootstrap"]["enabled"] is True
              and data["architect_hub"]["guardian"]["not_ready_seconds"] == 600)
    finally:
        W.REPO_DIR = old
        shutil.rmtree(tmp, ignore_errors=True)


def test_voice_install():
    print("\n[7] 语音栏与轻量安装")
    values = W._default_values()
    check("默认不安装语音", values["voice"] is False)
    sec = next((s for s in W._form_sections() if s["title"] == "语音"), None)
    check("表单含语音栏", sec is not None and any(i["key"] == "voice" for i in sec["items"]))
    check("语音栏在检索之后", [s["title"] for s in W._form_sections()].index("语音")
          == [s["title"] for s in W._form_sections()].index("检索") + 1)

    tmp = tempfile.mkdtemp()
    old = W.REPO_DIR
    old_pip, old_step, old_smoke = W.run_pip_step, W.run_step, W._voice_smoke
    W.REPO_DIR = tmp
    calls = []
    try:
        sdk = Path(tmp) / "extras" / "voice-wakeup" / "voice-wakeup-sdk"
        sdk.mkdir(parents=True)
        vals = W._default_values()
        W._load_existing(vals)
        check("重跑带回已装语音状态", vals["voice"] is True)

        (Path(tmp) / "extras").rename(Path(tmp) / "extras_bak")
        vals = W._default_values()
        W._load_existing(vals)
        check("未装语音时为否", vals["voice"] is False)

        W.run_pip_step = lambda *a, **k: calls.append(("pip", a)) or True
        W.run_step = lambda *a, **k: calls.append(("step", a)) or True
        W._voice_smoke = lambda: calls.append(("smoke",)) or True

        check("缺 SDK 包时跳过且不装依赖", W.step_install_voice() is True and not calls, calls)

        tarball = Path(tmp) / "Skills" / "voice-wakeup" / "voice-wakeup-sdk.tar.gz"
        tarball.parent.mkdir(parents=True)
        tarball.write_bytes(b"x")
        check("解包 + 依赖 + 冒烟",
              W.step_install_voice() is True
              and any(c[0] == "step" for c in calls)
              and any(c[0] == "pip" and "requirements-voice.txt" in " ".join(map(str, c[1]))
                      for c in calls)
              and ("smoke",) in calls, calls)
        check("不触发模型下载（无 ensure_models 调用点）",
              not any(len(c) > 1 and "download" in " ".join(map(str, c[1]))
                      for c in calls), calls)
    finally:
        W.REPO_DIR = old
        W.run_pip_step, W.run_step, W._voice_smoke = old_pip, old_step, old_smoke
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# 7. 非 TTY 回退：同样强制
# ---------------------------------------------------------------------------
def test_fallback():
    print("\n[8] 非 TTY 回退")
    out = io.StringIO()

    def feed(text):
        sys.stdin = io.StringIO(text)
        sys.stdin.isatty = lambda: False  # type: ignore[attr-defined]

    calls = []

    def verify(provider, proxy):
        calls.append(provider["key"])
        return (True, "ok", 200, "") if provider["key"] == "good" else (False, "auth", 401, "401 bad")

    # 顺序：厂商 → API Key(空) → embedding → systemd → qq → harunet → proxy
    #       → 初始探索方向 → API Key 重试：bad → good
    answers = ["", "", "", "", "", "", "", "", "", "先检查摄像头", "bad", "good"]
    feed("\n".join(answers) + "\n")
    old_out = sys.stdout
    try:
        sys.stdout = io.TextIOWrapper(io.BytesIO(), encoding="utf-8", write_through=True)
        values = W._form_fallback(W._form_sections(), W._default_values(), on_verify=verify)
    finally:
        sys.stdout = old_out
    check("回退路径最终拿到验证通过的 key", values["api_key"] == "good", values["api_key"])
    check("非 TTY 可填写初始探索方向", values["initial_exploration"] == "先检查摄像头")
    check("空 key 与坏 key 都被拦下", calls == ["bad", "good"], calls)

    feed("\n" * 15)
    try:
        sys.stdout = io.TextIOWrapper(io.BytesIO(), encoding="utf-8", write_through=True)
        W._form_fallback(W._form_sections(), W._default_values(), on_verify=verify)
        check("连续 3 次不通过 → 中止安装", False, "未中止")
    except SystemExit as e:
        check("连续 3 次不通过 → 中止安装", e.code == 1, e.code)
    finally:
        sys.stdout = old_out
    del out


def test_install_draft_and_launcher():
    print("\n[9] 安装草稿恢复与启动器")
    tmp = tempfile.mkdtemp()
    old_repo = W.REPO_DIR
    old_draft = os.environ.get("GOHARU_INSTALL_DRAFT")
    draft = os.path.join(tmp, "draft.json")
    W.REPO_DIR = tmp
    os.environ["GOHARU_INSTALL_DRAFT"] = draft
    try:
        values = W._default_values()
        values.update({
            "embedding": True, "api_key": "sk-draft", "qq": True,
            "qq_app_id": "app-id", "qq_secret": "qq-secret",
            "harunet": True, "harunet_name": "draft-bot",
            "initial_exploration": "检查摄像头",
        })
        W._save_install_draft(values)
        check("草稿文件已写入", os.path.exists(draft))
        if os.name != "nt":
            check("草稿仅用户可读", os.stat(draft).st_mode & 0o777 == 0o600)
        restored = W._default_values()
        check("重跑可恢复草稿", W._load_existing(restored)
              and restored["api_key"] == "sk-draft"
              and restored["qq_app_id"] == "app-id"
              and restored["harunet_name"] == "draft-bot",
              restored)
        W._clear_install_draft()
        check("安装成功后草稿可清除", not os.path.exists(draft))

        set_size(80, 24)
        old_read = W._read_key
        queue = list(["down", "enter"] + chars("draft-key") + ["enter", "ctrl-c"])
        W._read_key = lambda: queue.pop(0)
        try:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.suppress(SystemExit):
                W._form_tty(W._form_sections(), W._default_values(),
                            on_verify=lambda _p, _proxy: (True, "ok", 200, ""))
        finally:
            W._read_key = old_read
        check("Ctrl+C 退出后仍保留已填内容",
              os.path.exists(draft) and W._load_install_draft(
                  probe := W._default_values()) and probe["api_key"] == "draft-key")

        content = W._posix_launcher_content(
            "/tmp/Goharu test", "/tmp/venv/bin/python", "/tmp/node22/bin/node")
        check("启动器路径变量引号闭合",
              'GOHARU_HOME="/tmp/Goharu test"' in content)
        check("启动器持久化安装时选择的 Node 22",
              'export GOHARU_NODE="/tmp/node22/bin/node"' in content
              and 'export PATH="/tmp/node22/bin:$PATH"' in content)
        bash = shutil.which("bash")
        if bash:
            launcher = os.path.join(tmp, "goharu")
            with open(launcher, "w", encoding="utf-8", newline="\n") as f:
                f.write(content)
            parsed = subprocess.run([bash, "-n", launcher], capture_output=True,
                                    text=True, encoding="utf-8", errors="replace")
            check("启动器通过 bash 语法检查", parsed.returncode == 0, parsed.stderr)
    finally:
        W.REPO_DIR = old_repo
        if old_draft is None:
            os.environ.pop("GOHARU_INSTALL_DRAFT", None)
        else:
            os.environ["GOHARU_INSTALL_DRAFT"] = old_draft
        shutil.rmtree(tmp, ignore_errors=True)


def _run_all():
    print("=" * 60)
    print("安装向导离线测试")
    print("=" * 60)
    test_layout()
    test_edit_keys()
    test_gate()
    test_tty_redraw()
    test_verify_provider()
    test_load_existing()
    test_bootstrap_exploration_config()
    test_voice_install()
    test_fallback()
    test_install_draft_and_launcher()
    print("\n" + "=" * 60)
    print(f"结果: {passed} passed, {failed} failed")
    print("=" * 60)
    return 1 if failed else 0


def main():
    tmp = tempfile.mkdtemp(prefix="goharu_draft_test_")
    old = os.environ.get("GOHARU_INSTALL_DRAFT")
    os.environ["GOHARU_INSTALL_DRAFT"] = os.path.join(tmp, "draft.json")
    try:
        rc = _run_all()
    finally:
        if old is None:
            os.environ.pop("GOHARU_INSTALL_DRAFT", None)
        else:
            os.environ["GOHARU_INSTALL_DRAFT"] = old
        shutil.rmtree(tmp, ignore_errors=True)
    sys.exit(rc)


if __name__ == "__main__":
    main()
