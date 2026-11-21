"""asr - SenseVoice 离线语音转写 + Silero VAD 端点检测.

两层 API:
  SpeechRecognizer: 喂 PCM/文件 -> 文字（离线、一次性转写）
  VoiceListener:    唤醒词 -> 自动收音 -> 说完自动转写 -> 回调文本

模型与设备自检（agent 自助安装用）:
  ensure_models(models_root): 检测本地 ASR/VAD 模型，缺失自动从 ModelScope 下载
  mic_available():            检测本机是否有可用麦克风

目录约定:
  models_root/
    sense-voice-asr/   model.int8.onnx (229MB) + tokens.txt
    silero-vad/        silero_vad.onnx (2.3MB)

判停策略（cam_server 实测参数）：静音 1.2s 起试探性转写，按句子完整度
动态决定再等多久；只要有新语音静音计时清零——思考停顿不会腰斩发言。
"""
import logging
import os
import re
import subprocess
import sys
import time
import urllib.request

logger = logging.getLogger(__name__)

# ── 模型来源（ModelScope 直链，已验证）──
_SENSEVOICE_BASE = ("https://modelscope.cn/models/fengge2024/"
                    "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17/resolve/master")
_VAD_URL = "https://modelscope.cn/models/manyeyes/silero-vad-v5-onnx/resolve/master/silero_vad.onnx"

# 预期大小（字节，下限校验用）——下载结果明显偏小即视为失败
_EXPECTED = {
    ("sense-voice-asr", "model.int8.onnx"): 200 * 1024 * 1024,
    ("sense-voice-asr", "tokens.txt"): 100 * 1024,
    ("silero-vad", "silero_vad.onnx"): 1 * 1024 * 1024,
}

_TAG_RE = re.compile(r"<\|[^|]+\|>")
_SENTENCE_END = ("。", "！", "？", "!", "?", ".", "…")


def _download(url, dest, min_size, progress=None):
    """progress(downloaded_bytes, total_bytes) 可选；total 未知时为 0。"""
    if os.path.exists(dest) and os.path.getsize(dest) >= min_size:
        return False
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    logger.info("asr: downloading %s -> %s", url, dest)
    tmp = dest + ".part"
    with urllib.request.urlopen(url, timeout=30) as r, open(tmp, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        downloaded = 0
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
            downloaded += len(chunk)
            if progress:
                progress(downloaded, total)
    if os.path.getsize(tmp) < min_size:
        os.remove(tmp)
        raise RuntimeError(f"下载不完整或地址失效: {url}")
    os.replace(tmp, dest)
    return True


def ensure_models(models_root, progress=None):
    """检测 ASR/VAD 模型是否齐全，缺失的自动从 ModelScope 下载。

    progress(downloaded, total): 下载进度回调（仅在实际下载时触发）。
    返回 {"sense_voice": <dir>, "vad": <onnx path>}。
    联网仅发生在缺失时；已齐全则纯本地检查，秒回。
    """
    root = os.path.expanduser(models_root)
    paths = {
        "sense_voice": os.path.join(root, "sense-voice-asr"),
        "vad": os.path.join(root, "silero-vad", "silero_vad.onnx"),
    }
    files = [
        ("sense-voice-asr", "model.int8.onnx", f"{_SENSEVOICE_BASE}/model.int8.onnx"),
        ("sense-voice-asr", "tokens.txt", f"{_SENSEVOICE_BASE}/tokens.txt"),
        ("silero-vad", "silero_vad.onnx", _VAD_URL),
    ]
    downloaded = []
    for subdir, fname, url in files:
        dest = os.path.join(root, subdir, fname)
        if _download(url, dest, _EXPECTED[(subdir, fname)], progress=progress):
            downloaded.append(dest)
    if downloaded:
        logger.info("asr: downloaded %d model file(s)", len(downloaded))
    return paths


def mic_available():
    """检测本机是否有可用麦克风（不打开设备，只枚举）。"""
    try:
        if sys.platform.startswith("linux") and _which("arecord"):
            out = subprocess.run(["arecord", "-l"], capture_output=True,
                                 text=True, timeout=5)
            return "card" in (out.stdout or "")
        import sounddevice as sd
        return any(d.get("max_input_channels", 0) > 0 for d in sd.query_devices())
    except Exception as exc:
        logger.warning("asr: mic detection failed: %s", exc)
        return False


def _which(name):
    from shutil import which
    return which(name)


def clean_text(text):
    """剥元标签 + 过滤幻觉碎片（无 CJK 且 <=4 字符视为噪音）。"""
    text = _TAG_RE.sub("", text or "").strip()
    has_cjk = any("一" <= ch <= "鿿" for ch in text)
    return text if (has_cjk or len(text) > 4) else ""


class SpeechRecognizer:
    """离线一次性转写：PCM bytes / wav 文件 -> 文字。"""

    def __init__(self, model_dir, num_threads=2, use_itn=True):
        import sherpa_onnx  # 惰性导入，不用 ASR 时不需要 sherpa-onnx
        model_dir = os.path.expanduser(model_dir)
        self.recognizer = sherpa_onnx.OfflineRecognizer.from_sense_voice(
            model=os.path.join(model_dir, "model.int8.onnx"),
            tokens=os.path.join(model_dir, "tokens.txt"),
            num_threads=num_threads,
            use_itn=use_itn,
        )
        self.sample_rate = 16000

    def transcribe(self, pcm_bytes):
        """pcm_bytes: 16kHz 单声道 s16le。返回清洗后的文本（可能为空串）。"""
        import numpy as np
        samples = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        s = self.recognizer.create_stream()
        s.accept_waveform(self.sample_rate, samples)
        self.recognizer.decode_stream(s)
        return clean_text(s.result.text)

    def transcribe_file(self, wav_path):
        import wave
        with wave.open(wav_path, "rb") as w:
            if w.getframerate() != 16000 or w.getnchannels() != 1:
                raise ValueError("只支持 16kHz 单声道 wav")
            pcm = w.readframes(w.getnframes())
        return self.transcribe(pcm)


class VoiceListener:
    """唤醒词 -> 收音 -> 转写的完整链路（后台线程）。

    用法:
        listener = VoiceListener.from_models_root("~/voice-wakeup/models",
                                                  wake_word="你好")
        listener.on_utterance(lambda text: print("said:", text))
        listener.on_state(lambda st: print("state:", st))   # idle/listening/recognizing
        listener.start()
    """

    def __init__(self, kws_model_dir, asr_model_dir, vad_model,
                 wake_word="你好", threshold=0.25, cooldown=2.0,
                 mic=None, num_threads=2,
                 preroll_s=2.0, tentative_s=1.2, probe_gap_s=0.5,
                 abort_s=4.0, done_s=2.8, incomplete_s=4.0, max_speech_s=30.0,
                 wake_delay_s=1.0):
        import sherpa_onnx
        from .engine import KeywordEngine
        from .mic import MicSource

        self.mic = mic or MicSource()
        self.engine = KeywordEngine(model_dir=os.path.expanduser(kws_model_dir),
                                    threshold=threshold, cooldown=cooldown)
        self.engine.add_keyword(wake_word)
        self._wake_word = wake_word
        self.recognizer = SpeechRecognizer(asr_model_dir, num_threads=num_threads)

        vad_cfg = sherpa_onnx.VadModelConfig()
        vad_cfg.silero_vad.model = os.path.expanduser(vad_model)
        vad_cfg.silero_vad.threshold = 0.5
        vad_cfg.sample_rate = 16000
        self.vad = sherpa_onnx.VadModel.create(vad_cfg)
        self._vad_win = self.vad.window_size()  # silero: 576 样本/窗

        self.preroll_s = preroll_s
        self.tentative_s = tentative_s
        self.probe_gap_s = probe_gap_s
        self.abort_s = abort_s
        self.done_s = done_s
        self.incomplete_s = incomplete_s
        self.max_speech_s = max_speech_s
        self.wake_delay_s = wake_delay_s

        self._utterance_cbs = []
        self._state_cbs = []
        self._running = False
        self._thread = None

        import queue
        self._wake_q = queue.Queue(maxsize=4)
        # 回调在引擎锁内执行——只能投递队列，禁止在这里调 engine 方法
        self.engine.on_keyword(self._on_wake)

    @classmethod
    def from_models_root(cls, models_root, wake_word="你好", **kw):
        """自检模型（缺失自动下载）并构造。kws_model 也应在 models_root 下。"""
        root = os.path.expanduser(models_root)
        paths = ensure_models(root)
        return cls(kws_model_dir=os.path.join(root, "kws_model"),
                   asr_model_dir=paths["sense_voice"],
                   vad_model=paths["vad"],
                   wake_word=wake_word, **kw)

    # ---------- 事件 ----------

    def on_utterance(self, func):
        """识别出一段完整话语时回调 func(text)。"""
        self._utterance_cbs.append(func)

    def on_state(self, func):
        """状态变化回调 func(state): wake / listening / recognizing / idle。"""
        self._state_cbs.append(func)

    # ---------- 生命周期 ----------

    def start(self):
        if self._running:
            return
        self._running = True
        self.mic.start()
        import threading
        self._thread = threading.Thread(target=self._main_loop, daemon=True)
        self._thread.start()
        logger.info("VoiceListener started (wake_word=%r)", self.engine.list_keywords())

    def stop(self):
        self._running = False
        self.mic.stop()
        if self._thread:
            self._thread.join(timeout=3)

    @property
    def running(self):
        return self._running

    # ---------- 内部 ----------

    def _on_wake(self, word):
        try:
            self._wake_q.put_nowait(word)
        except Exception:
            pass

    def _emit_state(self, state):
        for cb in self._state_cbs:
            try:
                cb(state)
            except Exception:
                pass

    def _emit_utterance(self, text):
        for cb in self._utterance_cbs:
            try:
                cb(text)
            except Exception:
                pass

    def _strip_wake_word(self, text):
        """去掉转录开头误带的唤醒词（预卷缓冲里含唤醒词本身）。"""
        t = (text or "").strip()
        if not t:
            return ""
        w = self._wake_word
        if not w:
            return t
        # 剥掉开头标点/空白后再看是否以唤醒词开头
        head = t.lstrip("，。！？,.!? …")
        if head.startswith(w):
            t = head[len(w):].lstrip("，。！？,.!? …")
        return t.strip()

    def _record(self, q, initial):
        """收音直到说完。VAD 判语音/静音；静音后试探性转写，按句子
        完整度动态决定再等多久。返回 (pcm_bytes, had_content)。"""
        import numpy as np
        buf = bytearray(initial)
        vad_buf = np.empty(0, dtype=np.float32)
        silence_s = 0.0
        speech_win_count = 0
        target_silence = None
        last_probe_s = 0.0
        start = time.time()
        chunk_s = self.mic.chunk_bytes / 2 / 16000  # 每块秒数
        min_bytes = int(16000 * 1.5) * 2            # 前 1.5s 不判静音
        self.vad.reset()
        while True:
            c = q.get()
            buf += c
            vad_buf = np.concatenate(
                [vad_buf, np.frombuffer(c, dtype=np.int16).astype(np.float32) / 32768.0])
            speech = False
            while len(vad_buf) >= self._vad_win:
                if self.vad.is_speech(vad_buf[:self._vad_win]):
                    speech = True
                    speech_win_count += 1
                vad_buf = vad_buf[self._vad_win:]
            if speech:
                silence_s = 0.0
                target_silence = None
            else:
                silence_s += chunk_s
            if time.time() - start > self.max_speech_s:
                break
            if (silence_s >= self.tentative_s and len(buf) > min_bytes
                    and silence_s - last_probe_s >= self.probe_gap_s):
                last_probe_s = silence_s
                # VAD 累计语音不足 0.6s → 误唤醒/噪音，不转写直接进放弃倒计时
                if speech_win_count * self._vad_win / 16000 < 0.6:
                    target_silence = self.abort_s
                else:
                    text = self.recognizer.transcribe(bytes(buf))
                    if not text:
                        target_silence = self.abort_s
                    elif text.endswith(_SENTENCE_END):
                        target_silence = self.done_s
                    else:
                        target_silence = self.incomplete_s
            if target_silence is not None and silence_s >= target_silence:
                break
        return bytes(buf), target_silence != self.abort_s

    def _main_loop(self):
        import collections
        preroll = collections.deque(
            maxlen=int(self.preroll_s * 16000 * 2 / self.mic.chunk_bytes))
        q = self.mic.subscribe()
        while self._running:
            chunk = q.get()
            self.engine.feed(chunk)
            preroll.append(chunk)
            try:
                self._wake_q.get_nowait()
            except Exception:
                continue

            # 唤醒命中：先闪灯提示 + 留出准备时间，再开始收音
            self._emit_state("wake")
            if self.wake_delay_s > 0:
                time.sleep(self.wake_delay_s)
            # 排空可能重复入队的唤醒事件；丢弃含唤醒词的预卷，从新开始录音
            while True:
                try:
                    self._wake_q.get_nowait()
                except Exception:
                    break
            preroll.clear()

            self._emit_state("listening")
            pcm, had_content = self._record(q, b"")

            self._emit_state("recognizing")
            text = self.recognizer.transcribe(pcm) if had_content else ""
            text = self._strip_wake_word(text)
            if text:
                self._emit_utterance(text)
            else:
                logger.info("asr: no valid content, discarded")
            self.engine.reset()  # 主循环里调（非回调），安全
            preroll.clear()
            self._emit_state("idle")
