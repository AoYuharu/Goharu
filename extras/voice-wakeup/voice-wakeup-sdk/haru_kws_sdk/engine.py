"""KeywordEngine: sherpa-onnx KWS 的纯识别引擎.

只关心音频输入 -> 关键词事件, 不涉及音频来源(可由 MicSource 或任何
已有音频管道喂数据)和动作执行(由上层注册回调).

用法:
    engine = KeywordEngine(model_dir="~/sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01")
    engine.add_keyword("打开风扇")
    engine.on_keyword(lambda word: print("detected:", word))
    engine.feed(pcm_s16le_16khz_bytes)   # 循环喂 16kHz 单声道 s16le PCM
"""
import os
import threading
import time

import numpy as np
import sherpa_onnx

from .keywords import load_tokens, keywords_file_line


class KeywordEngine:
    def __init__(
        self,
        model_dir,
        threshold=0.25,
        num_threads=4,
        sample_rate=16000,
        cooldown=2.0,
        work_dir=None,
        encoder="encoder-epoch-12-avg-2-chunk-16-left-64.int8.onnx",
        decoder="decoder-epoch-12-avg-2-chunk-16-left-64.int8.onnx",
        joiner="joiner-epoch-12-avg-2-chunk-16-left-64.int8.onnx",
    ):
        """
        model_dir:   模型目录(含 tokens.txt 和 onnx 文件)
        threshold:   触发阈值, 越低越灵敏(误报也越多), 常用 0.1~0.3
        cooldown:    相邻两次触发的最小间隔(秒)
        work_dir:    keywords.txt 的存放目录, 默认 <model_dir>/runtime
        """
        self.model_dir = os.path.expanduser(model_dir)
        self.sample_rate = sample_rate
        self.cooldown = cooldown
        self.threshold = threshold
        self.num_threads = num_threads
        self._files = dict(
            tokens=os.path.join(self.model_dir, "tokens.txt"),
            encoder=os.path.join(self.model_dir, encoder),
            decoder=os.path.join(self.model_dir, decoder),
            joiner=os.path.join(self.model_dir, joiner),
        )
        self.work_dir = os.path.expanduser(work_dir or os.path.join(self.model_dir, "runtime"))
        os.makedirs(self.work_dir, exist_ok=True)
        self.keywords_path = os.path.join(self.work_dir, "keywords.txt")
        self._tokens = load_tokens(self._files["tokens"])
        self._words = []
        self._callbacks = []
        self._buf = b""
        self._chunk_bytes = int(sample_rate * 0.02) * 2  # 20ms
        self._last_trigger = 0.0
        self._lock = threading.Lock()
        self._spotter = None
        self._stream = None
        if os.path.exists(self.keywords_path):
            with open(self.keywords_path, encoding="utf-8") as f:
                self._words = [ln.split("@", 1)[1].strip() for ln in f if "@" in ln]
        self._rebuild()

    # ---------- 关键词管理 ----------

    def add_keyword(self, word):
        """注册关键词(自动转拼音 token), 立即生效."""
        if word not in self._words:
            keywords_file_line(word, self._tokens)  # 先校验 token
            self._words.append(word)
            self._sync_file()
            self._rebuild()

    def remove_keyword(self, word):
        if word in self._words:
            self._words.remove(word)
            self._sync_file()
            self._rebuild()

    def list_keywords(self):
        return list(self._words)

    def _sync_file(self):
        with open(self.keywords_path, "w", encoding="utf-8") as f:
            for w in self._words:
                f.write(keywords_file_line(w, self._tokens) + "\n")

    def _rebuild(self):
        with self._lock:
            if not self._words:
                self._spotter = None
                self._stream = None
                self._buf = b""
                return
            self._spotter = sherpa_onnx.KeywordSpotter(
                tokens=self._files["tokens"],
                encoder=self._files["encoder"],
                decoder=self._files["decoder"],
                joiner=self._files["joiner"],
                keywords_file=self.keywords_path,
                num_threads=self.num_threads,
                provider="cpu",
                max_active_paths=4,
                keywords_threshold=self.threshold,
                num_trailing_blanks=1,
            )
            self._stream = self._spotter.create_stream()
            self._buf = b""

    # ---------- 事件 ----------

    def on_keyword(self, func):
        """注册回调 func(word: str). 每次识别到关键词(含未绑定动作的词)都会调用."""
        self._callbacks.append(func)

    # ---------- 音频输入 ----------

    def feed(self, pcm):
        """喂入 16kHz 单声道 s16le PCM 字节流(任意长度, 内部自动切 20ms 块)."""
        with self._lock:
            if self._spotter is None:
                return
            spotter, stream = self._spotter, self._stream
            self._buf += pcm
            while len(self._buf) >= self._chunk_bytes:
                piece, self._buf = self._buf[:self._chunk_bytes], self._buf[self._chunk_bytes:]
                samples = np.frombuffer(piece, dtype=np.int16).astype(np.float32) / 32768.0
                stream.accept_waveform(self.sample_rate, samples)
                while spotter.is_ready(stream):
                    spotter.decode_stream(stream)
                word = spotter.get_result(stream)
                if word:
                    spotter.reset_stream(stream)
                    now = time.monotonic()
                    if now - self._last_trigger >= self.cooldown:
                        self._last_trigger = now
                        for cb in self._callbacks:
                            cb(word)

    def reset(self):
        """清空识别状态(例如音频源中断重连后)."""
        with self._lock:
            if self._spotter is not None:
                self._spotter.reset_stream(self._stream)
            self._buf = b""
