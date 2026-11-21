"""MicSource: 跨平台麦克风采集，统一输出 16kHz 单声道 s16le PCM。

后端自动选择（也可显式指定 backend=）：
  - "arecord":      Linux/ALSA，调用系统 arecord，零额外依赖
  - "sounddevice":  Windows/macOS（或没有 arecord 的 Linux），
                    需 pip install sounddevice（PortAudio 封装，三平台都有 wheel）
  - "auto":         默认。Linux 且有 arecord → arecord，否则 sounddevice

设备选择：
  - device=None → 自动：arecord 用 "default"（plug 插件自动重采样），
    sounddevice 用系统默认输入设备
  - arecord 可传 "plughw:CARD=Camera,DEV=0" 之类 ALSA 设备名
  - sounddevice 可传设备索引(int)或名称(str)，见 sounddevice.query_devices()

鲁棒性：
  - 采集进程/流异常退出 → 自动重启（退避 2s），断流恢复
  - 设备不支持 16kHz → 自动用设备原生采样率采集 + numpy 重采样
  - 多订阅者互不影响：每个订阅者独立队列，消费慢自动丢最旧数据

用法:
    mic = MicSource()        # 全自动
    mic.start()
    q = mic.subscribe()      # queue.Queue，收到 ~20ms 的 s16le PCM bytes
    ...
    mic.unsubscribe(q); mic.stop()
"""
import logging
import queue
import shutil
import subprocess
import sys
import threading
import time

logger = logging.getLogger(__name__)


class MicSource:
    def __init__(self, device=None, rate=16000, chunk_ms=20, backend="auto"):
        self.rate = rate
        self.chunk_bytes = int(rate * chunk_ms / 1000) * 2
        self.backend = self._resolve_backend(backend)
        self.device = device if device is not None else self._default_device()
        self._subs = []
        self._lock = threading.Lock()
        self._running = False
        self._thread = None
        self._proc = None    # arecord 子进程

    # ------------------------------------------------------------------
    # 后端选择
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_backend(backend):
        if backend != "auto":
            return backend
        if sys.platform.startswith("linux") and shutil.which("arecord"):
            return "arecord"
        return "sounddevice"

    def _default_device(self):
        if self.backend == "arecord":
            return "default"   # ALSA default（plug 层自动重采样）
        return None            # sounddevice: None = 系统默认输入设备

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    @property
    def running(self):
        return self._running

    def start(self):
        if self._running:
            return
        self._running = True
        target = self._loop_arecord if self.backend == "arecord" else self._loop_sd
        self._thread = threading.Thread(target=target, daemon=True)
        self._thread.start()
        logger.info("MicSource started: backend=%s device=%s rate=%d",
                    self.backend, self.device, self.rate)

    def stop(self):
        self._running = False
        if self._proc:
            try:
                self._proc.kill()
            except Exception:
                pass
        if self._thread:
            self._thread.join(timeout=3)

    # ------------------------------------------------------------------
    # 订阅（多订阅者，满则丢最旧）
    # ------------------------------------------------------------------

    def subscribe(self, max_chunks=200):
        """返回 queue.Queue，收到 bytes PCM 块。max_chunks 约等于缓冲秒数*50。"""
        q = queue.Queue(maxsize=max_chunks)
        with self._lock:
            self._subs.append(q)
        return q

    def unsubscribe(self, q):
        with self._lock:
            if q in self._subs:
                self._subs.remove(q)

    def _publish(self, chunk):
        with self._lock:
            subs = list(self._subs)
        for q in subs:
            try:
                q.put_nowait(chunk)
            except queue.Full:
                try:
                    q.get_nowait()
                except queue.Empty:
                    pass
                try:
                    q.put_nowait(chunk)
                except queue.Full:
                    pass

    # ------------------------------------------------------------------
    # 后端 1: arecord（Linux/ALSA）
    # ------------------------------------------------------------------

    def _loop_arecord(self):
        while self._running:
            try:
                self._proc = subprocess.Popen(
                    ["arecord", "-q", "-D", self.device,
                     "-f", "S16_LE", "-r", str(self.rate), "-c", "1", "-t", "raw"],
                    stdout=subprocess.PIPE, bufsize=0,
                )
            except Exception as e:
                logger.warning("MicSource: arecord spawn failed (%s), retry in 3s", e)
                time.sleep(3)
                continue
            while self._running:
                chunk = self._proc.stdout.read(self.chunk_bytes)
                if not chunk:
                    break
                self._publish(chunk)
            self._proc.wait()
            if self._running:
                logger.warning("MicSource: arecord exited, restarting in 1s")
                time.sleep(1)

    # ------------------------------------------------------------------
    # 后端 2: sounddevice（Windows/macOS/无 arecord 的 Linux）
    # ------------------------------------------------------------------

    def _loop_sd(self):
        try:
            import sounddevice as sd
        except ImportError:
            logger.error(
                "MicSource: sounddevice 未安装，无法在此平台采集麦克风。"
                "请执行: pip install sounddevice -i https://pypi.tuna.tsinghua.edu.cn/simple"
            )
            self._running = False
            return

        native_rate = self.rate
        while self._running:
            try:
                with sd.RawInputStream(
                    device=self.device, samplerate=native_rate, channels=1,
                    dtype="int16", blocksize=int(native_rate * 0.02),
                ) as stream:
                    logger.info("MicSource: sounddevice opened (rate=%d)", native_rate)
                    while self._running:
                        data, _overflowed = stream.read(int(native_rate * 0.02))
                        chunk = bytes(data)
                        if native_rate != self.rate:
                            chunk = self._resample(chunk, native_rate)
                        self._publish(chunk)
            except Exception as e:
                if native_rate == self.rate:
                    # 设备可能不支持 16kHz：退回原生采样率 + 软件重采样
                    try:
                        info = sd.query_devices(self.device, "input")
                        native_rate = int(info["default_samplerate"])
                        logger.warning(
                            "MicSource: 设备不支持 %dHz (%s)，退回原生 %dHz + 重采样",
                            self.rate, e, native_rate,
                        )
                        continue
                    except Exception:
                        pass
                logger.warning("MicSource: sounddevice error (%s), retry in 2s", e)
                time.sleep(2)

    def _resample(self, pcm_bytes, src_rate):
        """线性插值重采样到 self.rate。语音唤醒/识别场景下足够。"""
        import numpy as np
        samples = np.frombuffer(pcm_bytes, dtype=np.int16)
        if len(samples) == 0:
            return b""
        n_out = int(len(samples) * self.rate / src_rate)
        if n_out <= 0:
            return b""
        idx = np.linspace(0, len(samples) - 1, n_out)
        out = np.interp(idx, np.arange(len(samples)), samples.astype(np.float32))
        return out.astype(np.int16).tobytes()
