"""VoiceWakeup: 高层门面, 一行代码接入语音唤醒.

把 MicSource(麦克风采集) + KeywordEngine(识别) + 动作绑定串起来.

用法:
    from haru_kws_sdk import VoiceWakeup

    vw = VoiceWakeup(model_dir="~/sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01")
    vw.bind("打开风扇", command=["/home/AoYuharu/.local/bin/openfan", "1"])
    vw.bind("关闭风扇", command=["/home/AoYuharu/.local/bin/openfan", "0"])
    vw.bind("开灯", callback=lambda: print("light on"))
    vw.bind("报告状态", webhook="http://127.0.0.1:9000/hook")
    vw.on_detect(lambda word: print("heard:", word))   # 所有识别事件
    vw.start()   # 后台运行
    ...
    vw.stop()
"""
import json
import subprocess
import threading
import urllib.request

from .engine import KeywordEngine
from .mic import MicSource


class VoiceWakeup:
    def __init__(self, model_dir, device="plughw:CARD=Camera,DEV=0",
                 threshold=0.25, cooldown=2.0, num_threads=4, **engine_kwargs):
        self.mic = MicSource(device=device)
        self.engine = KeywordEngine(model_dir=model_dir, threshold=threshold,
                                    cooldown=cooldown, num_threads=num_threads,
                                    **engine_kwargs)
        self._actions = {}
        self._pump = None
        self._running = False
        self.engine.on_keyword(self._dispatch)

    # ---------- 动作绑定 ----------

    def bind(self, word, callback=None, command=None, webhook=None):
        """把关键词绑定到一个动作(三选一):

        callback: python 可调用对象, callback(word)
        command:  argv 列表, 用 subprocess 执行, 如 ["/path/to/openfan", "1"]
        webhook:  URL 字符串, 触发时 POST JSON {"keyword": word, "ts": ...}
        """
        self.engine.add_keyword(word)
        if callback is not None:
            self._actions[word] = lambda w: callback(w)
        elif command is not None:
            self._actions[word] = lambda w: subprocess.Popen(command)
        elif webhook is not None:
            self._actions[word] = lambda w: self._post(webhook, w)

    def unbind(self, word):
        self._actions.pop(word, None)
        self.engine.remove_keyword(word)

    def on_detect(self, func):
        """注册原始识别事件回调 func(word), 无论该词有没有绑定动作."""
        self.engine.on_keyword(func)

    # ---------- 生命周期 ----------

    def start(self):
        """启动(后台线程): 开麦克风, 持续喂给引擎."""
        if self._running:
            return
        self._running = True
        self.mic.start()
        self._pump = threading.Thread(target=self._pump_loop, daemon=True)
        self._pump.start()

    def stop(self):
        self._running = False
        self.mic.stop()
        if self._pump:
            self._pump.join(timeout=3)

    @property
    def running(self):
        return self._running

    # ---------- 内部 ----------

    def _pump_loop(self):
        q = self.mic.subscribe()
        while self._running:
            self.engine.feed(q.get())

    def _dispatch(self, word):
        action = self._actions.get(word)
        if action:
            try:
                action(word)
            except Exception:
                pass

    @staticmethod
    def _post(url, word):
        import time
        data = json.dumps({"keyword": word, "ts": time.time()}).encode()
        req = urllib.request.Request(url, data=data,
                                     headers={"Content-Type": "application/json"})
        threading.Thread(target=lambda: urllib.request.urlopen(req, timeout=5),
                         daemon=True).start()
