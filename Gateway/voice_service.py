"""
Voice Service — 语音能力的安装状态管理与模型下载。

语音是 harness 的输入通道而非工具：这里只负责"环境是否就绪"的管理
（麦克风、模型文件、用户授权、启停开关），实际的唤醒/转写服务由
VoiceListener 在后续版本中接入主回路。

状态持久化在 config.yaml 的 voice.* 键下。
"""
import importlib
import importlib.util
import logging
import sys
import threading
from pathlib import Path

from configurationLoader import config

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).parent.parent.resolve()
# 解包后的 voice-wakeup-sdk 目录即 models_root（kws_model/sense-voice-asr/silero-vad 都在其下）
# 2026-10 仓库整理后 SDK 迁入 extras/；保留旧路径兜底以兼容其他部署（如 haru）
_MODELS_ROOT_CANDIDATES = [
    _PROJECT_ROOT / "extras" / "voice-wakeup" / "voice-wakeup-sdk",
    _PROJECT_ROOT / "voice-wakeup" / "voice-wakeup-sdk",
]
MODELS_ROOT = next(
    (p for p in _MODELS_ROOT_CANDIDATES if p.is_dir()),
    _MODELS_ROOT_CANDIDATES[0],
)
SENSEVOICE_ONNX = MODELS_ROOT / "sense-voice-asr" / "model.int8.onnx"
SENSEVOICE_MIN_BYTES = 200 * 1024 * 1024

_download_lock = threading.Lock()
_download_state = {"running": False, "error": None}

_listener_lock = threading.Lock()
_listener = None  # 运行中的 VoiceListener（仅当 wakeup_enabled）

# haru_kws_sdk 可用性（三元：None=未探测, True/False=已确定）；
# 缺失时全链路静默降级——只告警一次，不再连刷 traceback
_sdk_ok = None

# 推理依赖探测（sherpa_onnx/pypinyin 必需；sounddevice 在 Linux 可选，可用 arecord）
_deps_state = None       # (ok: bool, error: str|None)
_listener_message = ""   # 最近一次 start/stop 的结果说明（TUI 面板展示）


def _sdk_path():
    """让 haru_kws_sdk 可导入。"""
    sdk_dir = str(MODELS_ROOT)
    if sdk_dir not in sys.path:
        sys.path.insert(0, sdk_dir)


def _missing_deps():
    """推理依赖探测。返回缺失说明（None=齐全）。

    sounddevice 在 Linux 只告警：SDK 可用 arecord 兜底；Windows/macOS 必需。
    """
    required = ["sherpa_onnx", "pypinyin"]
    if sys.platform != "linux":
        required.append("sounddevice")
    missing = []
    for name in required:
        try:
            if importlib.util.find_spec(name) is None:
                missing.append(name)
        except (ImportError, ValueError):
            missing.append(name)
    optional_missing = []
    if sys.platform == "linux":
        try:
            if importlib.util.find_spec("sounddevice") is None:
                optional_missing.append("sounddevice")
        except (ImportError, ValueError):
            optional_missing.append("sounddevice")
    if missing:
        return "缺少语音依赖: " + ", ".join(missing) + "（pip install -r requirements-voice.txt）"
    if optional_missing:
        logger.info("voice: sounddevice 缺失，Linux 下将回退 arecord 采集")
    return None


def deps_available():
    """推理依赖是否就绪。返回 (ok, error)；结果缓存，refresh() 后重探。"""
    global _deps_state
    if _deps_state is None:
        error = _missing_deps()
        _deps_state = (error is None, error)
    return _deps_state


def refresh():
    """清空 SDK/依赖探测缓存，供安装依赖后重新探测。"""
    global _sdk_ok, _deps_state
    _sdk_ok = None
    _deps_state = None
    try:
        importlib.invalidate_caches()
    except Exception:
        pass
    logger.info("voice: probe cache refreshed")


def sdk_available():
    """探测语音链路是否可用：SDK 可导入且推理依赖齐全（结果缓存，缺失只告警一次）。"""
    global _sdk_ok
    if _sdk_ok is not None:
        return _sdk_ok
    deps_ok, deps_error = deps_available()
    if not deps_ok:
        _sdk_ok = False
        logger.warning("voice: 语音依赖不齐，语音功能已禁用（%s）", deps_error)
        return _sdk_ok
    try:
        _sdk_path()
        import haru_kws_sdk  # noqa: F401
        _sdk_ok = True
    except Exception as exc:
        _sdk_ok = False
        logger.warning(
            "voice: haru_kws_sdk 不可用（%s 下未安装，%s），语音功能已禁用；"
            "后续语音相关操作将直接返回不可用，不再重复报错", MODELS_ROOT, exc,
        )
    return _sdk_ok


def _check_mic():
    if not sdk_available():
        return False
    try:
        from haru_kws_sdk import mic_available
        return bool(mic_available())
    except Exception as exc:
        logger.warning("voice: mic detection failed: %s", exc)
        return False


def _model_states():
    kws_dir = MODELS_ROOT / "kws_model"
    return {
        "kws": kws_dir.is_dir() and any(kws_dir.glob("*.onnx")),
        "vad": (MODELS_ROOT / "silero-vad" / "silero_vad.onnx").is_file(),
        "tokens": (MODELS_ROOT / "sense-voice-asr" / "tokens.txt").is_file(),
        "sensevoice": SENSEVOICE_ONNX.is_file()
        and SENSEVOICE_ONNX.stat().st_size >= SENSEVOICE_MIN_BYTES,
    }


def get_status():
    """完整状态：麦克风、模型、授权与开关。TUI 面板的数据源。"""
    models = _model_states()
    deps_ok, deps_error = deps_available()
    return {
        "installed": MODELS_ROOT.is_dir(),
        "sdk_available": sdk_available(),
        "deps_ok": deps_ok,
        "deps_error": deps_error,
        "mic_available": _check_mic(),
        "mic_consent": bool(config.get("voice.mic_consent", False)),
        "asr_enabled": bool(config.get("voice.asr_enabled", False)),
        "wakeup_enabled": bool(config.get("voice.wakeup_enabled", False)),
        "models": models,
        "models_ready": all(models.values()),
        "download_running": _download_state["running"],
        "download_error": _download_state["error"],
        "models_root": str(MODELS_ROOT),
        "listener_running": listener_running(),
        "listener_message": _listener_message,
    }


def set_config(mic_consent=None, asr_enabled=None, wakeup_enabled=None):
    """更新语音开关。约束：开启语音唤醒必须同时开启语音识别。"""
    # 依赖可能在向导装完后才有——每次改开关重探一次（缓存廉价）
    refresh()
    if mic_consent is not None:
        config.set("voice.mic_consent", bool(mic_consent))
    if asr_enabled is not None:
        config.set("voice.asr_enabled", bool(asr_enabled))
    if wakeup_enabled is not None:
        wakeup = bool(wakeup_enabled)
        config.set("voice.wakeup_enabled", wakeup)
        if wakeup:
            config.set("voice.asr_enabled", True)
    config.save()
    status = get_status()
    status["need_download"] = (
        (status["asr_enabled"] or status["wakeup_enabled"])
        and not status["models"]["sensevoice"]
    )
    return status


def download_model(emit_event):
    """后台线程下载缺失的模型（实际只有 SenseVoice 229MB 需要下载）。

    emit_event(type, payload): 向 TUI 推送事件的回调。
    返回 (started: bool, message: str)。
    """
    refresh()
    if not sdk_available():
        _, deps_error = deps_available()
        msg = deps_error or "语音 SDK（haru_kws_sdk）未安装，本机语音功能不可用"
        _download_state["error"] = msg
        # TUI 等待 download_complete 事件复位进度条——拒绝时也要发终态
        try:
            emit_event("voice.download_complete", {"ok": False, "error": msg})
        except Exception:
            pass
        return False, msg
    with _download_lock:
        if _download_state["running"]:
            return False, "下载已在进行中"
        _download_state["running"] = True
        _download_state["error"] = None

    def _run():
        try:
            from haru_kws_sdk.asr import ensure_models

            last_emit = [0.0]

            def _progress(downloaded, total):
                import time as _time
                now = _time.monotonic()
                if now - last_emit[0] < 0.5:
                    return
                last_emit[0] = now
                emit_event("voice.download_progress", {
                    "downloaded_bytes": downloaded,
                    "total_bytes": total,
                    "percent": round(downloaded * 100.0 / total, 1) if total else 0.0,
                })

            emit_event("voice.download_progress", {"downloaded_bytes": 0, "total_bytes": 0, "percent": 0.0})
            ensure_models(str(MODELS_ROOT), progress=_progress)
            emit_event("voice.download_complete", {"ok": True, "status": get_status()})
            logger.info("voice: model download completed")
        except Exception as exc:
            _download_state["error"] = str(exc)
            emit_event("voice.download_complete", {"ok": False, "error": str(exc)})
            logger.error("voice: model download failed: %s", exc, exc_info=True)
        finally:
            _download_state["running"] = False

    threading.Thread(target=_run, daemon=True, name="voice-download").start()
    return True, "下载已开始"


def listener_running():
    with _listener_lock:
        return _listener is not None


def start_listener(emit_event):
    """启动唤醒监听（VoiceListener：唤醒词"你好" → 收音 → 转写）。

    emit_event(type, payload): 转写结果/状态变化的推送回调。
    返回 (ok: bool, message: str)。
    """
    global _listener, _listener_message
    refresh()
    if not sdk_available():
        _, deps_error = deps_available()
        return False, deps_error or "语音 SDK（haru_kws_sdk）未安装，本机语音功能不可用"
    with _listener_lock:
        if _listener is not None:
            _listener_message = "已在运行"
            return True, _listener_message
        models = _model_states()
        if not all(models.values()):
            _listener_message = "模型未就绪，请先下载 SenseVoice 模型"
            return False, _listener_message
        try:
            _sdk_path()
            from haru_kws_sdk import VoiceListener
            listener = VoiceListener.from_models_root(str(MODELS_ROOT), wake_word="你好")
            listener.on_utterance(lambda text: emit_event("voice.transcript", {"text": text}))
            listener.on_state(lambda st: emit_event("voice.state", {"state": st}))
            listener.start()
            _listener = listener
            _listener_message = "唤醒监听已启动（说「你好」）"
            logger.info("voice: wake listener started (wake_word=你好)")
            emit_event("voice.activated", {
                "wake_word": "你好",
                "message": _listener_message,
                "indicator": _led_support(),
            })
            return True, _listener_message
        except Exception as exc:
            _listener_message = f"启动失败：{exc}"
            logger.error("voice: start_listener failed: %s", exc, exc_info=True)
            return False, _listener_message


def _led_support() -> dict:
    """LED 指示器能力裁决（供激活事件携带）。异常一律降级为 unknown。"""
    try:
        from Gateway.voice_indicator import led_support
        return led_support()
    except Exception as exc:
        logger.debug("voice: led support probe failed: %s", exc)
        return {"status": "unknown", "interface": None, "reason": str(exc)}


def stop_listener():
    """停止唤醒监听（Gateway shutdown 时调用，释放麦克风）。"""
    global _listener, _listener_message
    with _listener_lock:
        if _listener is None:
            return
        try:
            _listener.stop()
        except Exception as exc:
            logger.warning("voice: stop_listener error: %s", exc)
        _listener = None
        _listener_message = "唤醒监听已停止"
        logger.info("voice: wake listener stopped")
