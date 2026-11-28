"""语音 LED 指示器契约测试：图案映射 / 安全 LED 选择 / 恢复 / 降级。

运行：python tests/test_voice_indicator.py
"""

import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import Gateway.voice_indicator as vi  # noqa: E402

PASS, FAIL = 0, 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {detail}")


def _leds():
    return [
        {"name": "power", "path": "/sys/class/leds/power", "writable": True,
         "driver": None},
        {"name": "input3::capslock", "path": "/sys/class/leds/input3::capslock",
         "writable": True, "driver": None},
        {"name": "reg:red", "path": "/sys/class/leds/reg:red", "writable": True,
         "driver": "leds-regulator"},
        {"name": "lock", "path": "/sys/class/leds/lock", "writable": False,
         "driver": None},
    ]


class FakeBackend:
    instances = []

    def __init__(self, path):
        self.path = path
        self.events = []
        FakeBackend.instances.append(self)

    def start(self):
        self.events.append("start")

    def on(self):
        self.events.append("on")

    def off(self):
        self.events.append("off")

    def close(self):
        self.events.append("close")


def wait_for(predicate, timeout=2.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_event_mapping():
    print("== 事件 → 图案映射 ==")
    ind = vi.VoiceIndicator(enabled=False)
    check("activated 慢闪 3 次",
          ind._command_for("voice.activated", {})["steps"] == [(True, 0.4), (False, 0.4)] * 3)
    check("transcript 快闪 2 次",
          ind._command_for("voice.transcript", {})["steps"] == [(True, 0.08), (False, 0.08)] * 2)
    check("error 短闪 3 次",
          ind._command_for("voice.error", {})["steps"] == [(True, 0.15), (False, 0.15)] * 3)
    check("listening 常亮", ind._command_for("voice.state", {"state": "listening"})["kind"] == "steady")
    check("recognizing 3Hz 循环",
          ind._command_for("voice.state", {"state": "recognizing"})["kind"] == "loop")
    check("idle 熄灭", ind._command_for("voice.state", {"state": "idle"})["kind"] == "off")
    check("未知事件无动作", ind._command_for("voice.unknown", {}) is None)
    check("unknown state 无动作", ind._command_for("voice.state", {"state": "??"}) is None)


def test_select_led():
    print("== 安全 LED 选择 ==")
    old = vi._read_identity
    vi._read_identity = lambda: {"actuation": {"leds": _leds()}}
    try:
        support = {"status": "supported", "interface": "/sys/class/leds/power"}
        check("auto 选 blink_support 指定 LED",
              vi._select_led(support, "auto")["name"] == "power")
        check("指定 target 可按 name 选",
              vi._select_led(support, "power")["name"] == "power")
        check("不安全候选被拒绝（锁定键）", vi._select_led(support, "input3::capslock") is None)
        check("regulator 被拒绝", vi._select_led(support, "reg:red") is None)
        check("不可写 LED 被拒绝", vi._select_led(support, "lock") is None)

        vi._read_identity = lambda: {"actuation": {"leds": [
            {"name": "input3::capslock", "path": "/x", "writable": True, "driver": None},
        ]}}
        check("只有不安全候选时返回 None", vi._select_led(support, "auto") is None)
    finally:
        vi._read_identity = old


def test_sysfs_restore():
    print("== sysfs 原值保存与恢复 ==")
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp) / "power"
        base.mkdir()
        (base / "brightness").write_text("1", encoding="utf-8")
        (base / "trigger").write_text("timer", encoding="utf-8")
        backend = vi.SysfsLedBackend(str(base))
        backend.start()
        check("启动后 trigger=none、brightness=0",
              (base / "trigger").read_text(encoding="utf-8").strip() == "none"
              and (base / "brightness").read_text(encoding="utf-8").strip() == "0")
        backend.on()
        check("on 写 brightness=1", (base / "brightness").read_text(encoding="utf-8").strip() == "1")
        backend.off()
        check("off 写 brightness=0", (base / "brightness").read_text(encoding="utf-8").strip() == "0")
        backend.close()
        check("退出恢复原 brightness/trigger",
              (base / "brightness").read_text(encoding="utf-8").strip() == "1"
              and (base / "trigger").read_text(encoding="utf-8").strip() == "timer")


def test_indicator_drives_backend():
    print("== 指示器驱动后端（常亮/熄灭）==")
    old_read = vi._read_identity
    vi._read_identity = lambda: {"actuation": {"leds": _leds()}}
    FakeBackend.instances.clear()
    ind = vi.VoiceIndicator(
        enabled=True, support_fn=lambda: {"status": "supported",
                                          "interface": "/sys/class/leds/power"},
        backend_factory=FakeBackend)
    try:
        ind.handle_event("voice.state", {"state": "listening"})
        check("listening → 后端 on",
              wait_for(lambda: FakeBackend.instances
                       and "on" in FakeBackend.instances[0].events))
        ind.handle_event("voice.state", {"state": "idle"})
        check("idle → 后端 off",
              wait_for(lambda: FakeBackend.instances
                       and FakeBackend.instances[0].events[-1] == "off"))
        ind.close()
        check("close 调用后端 close",
              bool(FakeBackend.instances)
              and "close" in FakeBackend.instances[0].events,
              FakeBackend.instances[0].events if FakeBackend.instances else "no backend")
    finally:
        vi._read_identity = old_read


def test_unsupported_fallback():
    print("== 无安全 LED → unsupported + 文字事件 ==")
    events = []
    old_platform = vi.platform
    old_read = vi._read_identity
    vi.platform = type("P", (), {"system": staticmethod(lambda: "Windows")})
    vi._read_identity = lambda: {}
    try:
        ind = vi.VoiceIndicator(emit_event=lambda t, p: events.append((t, p)),
                                enabled=True)
        ind.handle_event("voice.activated", {})
        check("Windows 无 LED → 发 voice.indicator unsupported",
              any(t == "voice.indicator" and p.get("status") == "unknown"
                  for t, p in events), events)
        check("后端降级为 Noop", isinstance(ind._backend, vi.NoopBackend))
        count = len(events)
        ind.handle_event("voice.transcript", {"text": "hi"})
        check("unsupported 只提示一次", len(events) == count)
    finally:
        vi.platform = old_platform
        vi._read_identity = old_read


def test_error_isolation():
    print("== 后端异常不穿透 ==")
    old_read = vi._read_identity
    vi._read_identity = lambda: {"actuation": {"leds": _leds()}}

    def boom(_path):
        raise OSError("permission denied")

    try:
        events = []
        ind = vi.VoiceIndicator(emit_event=lambda t, p: events.append((t, p)),
                                enabled=True,
                                support_fn=lambda: {"status": "supported",
                                                    "interface": "/sys/class/leds/power"},
                                backend_factory=boom)
        ind.handle_event("voice.activated", {})   # 不应抛异常
        check("初始化异常被吞掉", True)
        check("异常降级发 unsupported 事件",
              any(p.get("status") == "unsupported" for t, p in events), events)
        ind.handle_event("voice.state", {"state": "listening"})  # Noop 后端，不应抛
        check("降级后事件不抛异常", True)
        ind.close()
    finally:
        vi._read_identity = old_read


def main():
    test_event_mapping()
    test_select_led()
    test_sysfs_restore()
    test_indicator_drives_backend()
    test_unsupported_fallback()
    test_error_isolation()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
