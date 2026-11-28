"""语音 LED 指示器：把语音事件映射为安全 LED 的闪烁图案。

安全边界（与 Architect/probe.py 的 blink_support 裁决一致）：
- 只用 Linux sysfs leds-class 中可证明安全的 LED——brightness 可写、trigger
  可保存恢复、非 regulator、非键盘背光 / 锁定键；
- Windows/macOS / 无 LED / 权限不足 → Noop 后端，发一次 voice.indicator
  unsupported 事件并降级为文字提示；
- 绝不写 /dev、风扇 PWM、键盘背光、CapsLock/NumLock/ScrollLock。

指示器异常全部吞掉并记日志，绝不影响语音链路。
"""

import logging
import platform
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
IDENTITY_PATH = _PROJECT_ROOT / "runtime_memory" / "identity.yaml"

_UNSAFE_NAME_TOKENS = (
    "kbd", "keyboard", "capslock", "numlock", "scrolllock", "input",
)
_UNSAFE_DRIVER_TOKENS = ("leds-regulator",)


def _read_identity() -> dict:
    try:
        import yaml

        if not IDENTITY_PATH.exists():
            return {}
        return yaml.safe_load(IDENTITY_PATH.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        logger.debug("voice indicator: read identity failed: %s", exc)
        return {}


def _is_safe_led(led: dict) -> bool:
    """按 probe 的同一标准复核——identity 被手工改写时也不盲信。"""
    name = str(led.get("name") or "").lower()
    driver = str(led.get("driver") or "").lower()
    if any(token in name for token in _UNSAFE_NAME_TOKENS):
        return False
    if any(token in driver for token in _UNSAFE_DRIVER_TOKENS):
        return False
    return bool(led.get("writable"))


def led_support() -> dict:
    """LED 能力裁决：identity 里的 blink_support；无记录时降级 unknown。"""
    identity = _read_identity()
    actuation = identity.get("actuation") or {}
    support = actuation.get("blink_support")
    if isinstance(support, dict) and support.get("status"):
        return dict(support)
    if platform.system() != "Linux":
        return {"status": "unknown", "interface": None,
                "reason": "no generic LED interface on this OS"}
    return {"status": "unsupported", "interface": None,
            "reason": "identity 未记录 LED 探测结果（先重跑 bootstrap 或手工补录）"}


def _select_led(support: dict, target: str = "auto") -> dict | None:
    """从 identity 的 leds 列表选择安全目标；target 可由配置指定 name/path。"""
    identity = _read_identity()
    leds = (identity.get("actuation") or {}).get("leds") or []
    if not isinstance(leds, list):
        return None
    candidates = [led for led in leds if isinstance(led, dict) and _is_safe_led(led)]
    if not candidates:
        return None
    if target and target != "auto":
        for led in candidates:
            if target in (led.get("name"), led.get("path")):
                return led
        return None
    interface = support.get("interface")
    if interface:
        for led in candidates:
            if led.get("path") == interface or led.get("name") == interface:
                return led
    return candidates[0]


class SysfsLedBackend:
    """Linux sysfs LED：保存原 brightness/trigger，退出时恢复。"""

    def __init__(self, path: str):
        self.path = Path(path)
        self._saved_brightness = None
        self._saved_trigger = None

    def start(self):
        self._saved_brightness = self._read("brightness")
        self._saved_trigger = self._read("trigger")
        # trigger 置 none 才能手动控制 brightness；原值退出时写回
        self._write("trigger", "none")
        self._write("brightness", "0")

    def on(self):
        self._write("brightness", "1")

    def off(self):
        self._write("brightness", "0")

    def close(self):
        try:
            if self._saved_trigger is not None:
                self._write("trigger", self._saved_trigger)
            if self._saved_brightness is not None:
                self._write("brightness", self._saved_brightness)
        except Exception as exc:
            logger.warning("voice indicator: restore LED failed: %s", exc)

    def _read(self, name: str):
        try:
            return (self.path / name).read_text(encoding="utf-8").strip()
        except OSError:
            return None

    def _write(self, name: str, value: str):
        with open(self.path / name, "w", encoding="utf-8") as f:
            f.write(value)


class NoopBackend:
    def start(self):
        pass

    def on(self):
        pass

    def off(self):
        pass

    def close(self):
        pass


# 事件 → 图案步骤：(亮/灭, 秒)。loop/steady/off 单独处理。
_PATTERNS = {
    "voice.activated": [(True, 0.4), (False, 0.4)] * 3,
    "voice.transcript": [(True, 0.08), (False, 0.08)] * 2,
    "voice.error": [(True, 0.15), (False, 0.15)] * 3,
}
_STATE_PATTERNS = {
    "wake": [(True, 0.12), (False, 0.12)] * 2,
    "listening": "steady",
    "recognizing": "loop",
    "idle": "off",
}


class VoiceIndicator:
    """事件驱动的 LED 指示器。handle_event 永不阻塞、永不抛异常。"""

    def __init__(self, emit_event=None, enabled=None, target=None,
                 support_fn=None, backend_factory=None):
        self._emit_event = emit_event
        self._support_fn = support_fn or led_support
        self._backend_factory = backend_factory or SysfsLedBackend
        self._enabled = enabled
        self._target = target
        self._cond = threading.Condition()
        self._cmd = None
        self._shutdown = False
        self._thread = None
        self._backend = None
        self._initialized = False
        self._notified = False

    # ── 事件入口 ──────────────────────────────────────────────
    def handle_event(self, ev_type: str, payload: dict | None = None):
        try:
            if ev_type == "voice.indicator":
                return
            cmd = self._command_for(ev_type, payload or {})
            if cmd is None:
                return
            if not self._ensure_backend():
                return
            with self._cond:
                self._cmd = cmd
                self._cond.notify_all()
        except Exception as exc:
            logger.warning("voice indicator: handle_event failed: %s", exc)

    def _command_for(self, ev_type: str, payload: dict):
        if ev_type == "voice.state":
            kind = _STATE_PATTERNS.get(str(payload.get("state") or "").lower())
            if kind:
                return {"kind": kind}
            return None
        pattern = _PATTERNS.get(ev_type)
        if pattern:
            return {"kind": "pattern", "steps": pattern}
        return None

    # ── 后端生命周期 ──────────────────────────────────────────
    def _enabled_now(self) -> bool:
        if self._enabled is not None:
            return bool(self._enabled)
        try:
            from configurationLoader import config
            return bool(config.get("voice.led_indicator.enabled", True))
        except Exception:
            return True

    def _target_now(self) -> str:
        if self._target is not None:
            return self._target
        try:
            from configurationLoader import config
            return str(config.get("voice.led_indicator.target", "auto") or "auto")
        except Exception:
            return "auto"

    def _ensure_backend(self) -> bool:
        if self._initialized:
            return self._backend is not None and not isinstance(
                self._backend, NoopBackend)
        self._initialized = True
        if not self._enabled_now():
            logger.info("voice indicator: 配置关闭（voice.led_indicator.enabled=false）")
            self._backend = NoopBackend()
            return False

        support = {}
        try:
            support = self._support_fn() or {}
        except Exception as exc:
            support = {"status": "unknown", "reason": str(exc)}
        status = str(support.get("status") or "unknown")
        led = None
        if status == "supported":
            led = _select_led(support, self._target_now())
        if led is None:
            self._backend = NoopBackend()
            self._notify_unsupported(support)
            return False
        try:
            backend = self._backend_factory(led["path"])
            backend.start()
        except Exception as exc:
            logger.warning("voice indicator: backend init failed: %s", exc)
            self._backend = NoopBackend()
            self._notify_unsupported(
                {"status": "unsupported", "interface": None,
                 "reason": f"LED 初始化失败: {exc}"})
            return False
        self._backend = backend
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="voice-led-indicator")
        self._thread.start()
        logger.info("voice indicator: target LED = %s（%s）", led.get("name"), led["path"])
        return True

    def _notify_unsupported(self, support: dict):
        if self._notified:
            return
        self._notified = True
        if self._emit_event is None:
            return
        try:
            self._emit_event("voice.indicator", {
                "status": support.get("status") or "unsupported",
                "reason": support.get("reason") or "",
                "message": "本机没有可安全控制的 LED，语音状态改用文字提示",
            })
        except Exception:
            pass

    # ── 图案执行 ──────────────────────────────────────────────
    def _run(self):
        while True:
            with self._cond:
                while self._cmd is None and not self._shutdown:
                    self._cond.wait()
                if self._shutdown:
                    break
                cmd = self._cmd
                self._cmd = None
            if cmd is None:
                continue
            try:
                self._execute(cmd)
            except Exception as exc:
                logger.warning("voice indicator: pattern failed: %s", exc)
                try:
                    if self._backend is not None:
                        self._backend.off()
                except Exception:
                    pass

    def _execute(self, cmd: dict):
        if self._backend is None:
            return
        kind = cmd.get("kind")
        if kind == "off":
            self._backend.off()
            return
        if kind == "steady":
            self._backend.on()
            return
        if kind == "pattern":
            for on, dur in cmd.get("steps", []):
                if self._interrupted():
                    return
                self._backend.on() if on else self._backend.off()
                self._wait(dur)
            self._backend.off()
            return
        if kind == "loop":
            while not self._interrupted():
                self._backend.on()
                if self._wait(0.17):
                    break
                self._backend.off()
                if self._wait(0.17):
                    break
            self._backend.off()

    def _wait(self, seconds: float) -> bool:
        """等待；有新命令/退出时提前返回 True。"""
        with self._cond:
            if self._cmd is not None or self._shutdown:
                return True
            self._cond.wait(timeout=seconds)
            return self._cmd is not None or self._shutdown

    def _interrupted(self) -> bool:
        with self._cond:
            return self._cmd is not None or self._shutdown

    def close(self):
        try:
            with self._cond:
                self._shutdown = True
                self._cond.notify_all()
            if self._backend is not None:
                self._backend.close()
        except Exception as exc:
            logger.warning("voice indicator: close failed: %s", exc)
