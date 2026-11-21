"""
Voice Service — 语音能力的安装状态管理与模型下载。

语音是 harness 的输入通道而非工具：这里只负责"环境是否就绪"的管理
（麦克风、模型文件、用户授权、启停开关），实际的唤醒/转写服务由
VoiceListener 在后续版本中接入主回路。

状态持久化在 config.yaml 的 voice.* 键下。
"""
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


def _sdk_path():
    """让 haru_kws_sdk 可导入。"""
    sdk_dir = str(MODELS_ROOT)
    if sdk_dir not in sys.path:
        sys.path.insert(0, sdk_dir)


def sdk_available():
    """探测 haru_kws_sdk 是否可导入（结果缓存，缺失只告警一次）。"""
    global _sdk_ok
    if _sdk_ok is not None:
        return _sdk_ok
    try:
        _sdk_path()
        import haru_kws_sdk  # noqa: F401
        _sdk_ok = True
    except Exception:
        _sdk_ok = False
        logger.warning(
            "voice: haru_kws_sdk 不可用（%s 下未安装），语音功能已禁用；"
            "后续语音相关操作将直接返回不可用，不再重复报错", MODELS_ROOT,
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
    return {
        "installed": MODELS_ROOT.is_dir(),
        "sdk_available": sdk_available(),
        "mic_available": _check_mic(),
        "mic_consent": bool(config.get("voice.mic_consent", False)),
        "asr_enabled": bool(config.get("voice.asr_enabled", False)),
        "wakeup_enabled": bool(config.get("voice.wakeup_enabled", False)),
        "models": models,
        "models_ready": all(models.values()),
        "download_running": _download_state["running"],
        "download_error": _download_state["error"],
        "models_root": str(MODELS_ROOT),
    }


def set_config(mic_consent=None, asr_enabled=None, wakeup_enabled=None):
    """更新语音开关。约束：开启语音唤醒必须同时开启语音识别。"""
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
    if not sdk_available():
        msg = "语音 SDK（haru_kws_sdk）未安装，本机语音功能不可用"
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
    global _listener
    if not sdk_available():
        return False, "语音 SDK（haru_kws_sdk）未安装，本机语音功能不可用"
    with _listener_lock:
        if _listener is not None:
            return True, "已在运行"
        models = _model_states()
        if not all(models.values()):
            return False, "模型未就绪，请先下载 SenseVoice 模型"
        try:
            _sdk_path()
            from haru_kws_sdk import VoiceListener
            listener = VoiceListener.from_models_root(str(MODELS_ROOT), wake_word="你好")
            listener.on_utterance(lambda text: emit_event("voice.transcript", {"text": text}))
            listener.on_state(lambda st: emit_event("voice.state", {"state": st}))
            listener.start()
            _listener = listener
            logger.info("voice: wake listener started (wake_word=你好)")
            return True, "唤醒监听已启动（说「你好」）"
        except Exception as exc:
            logger.error("voice: start_listener failed: %s", exc, exc_info=True)
            return False, str(exc)


def stop_listener():
    """停止唤醒监听。"""
    global _listener
    with _listener_lock:
        if _listener is None:
            return
        try:
            _listener.stop()
        except Exception as exc:
            logger.warning("voice: stop_listener error: %s", exc)
        _listener = None
        logger.info("voice: wake listener stopped")
