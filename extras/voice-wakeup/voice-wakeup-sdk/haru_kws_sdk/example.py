#!/usr/bin/env python3
"""haru_kws_sdk 使用示例: 语音控制 openfan(风扇+灯).

在 haru 上运行:
    ~/venv-kws/bin/python example.py
"""
import time

from haru_kws_sdk import VoiceWakeup

MODEL_DIR = "/home/AoYuharu/sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01"
OPENFAN = "/home/AoYuharu/.local/bin/openfan"

vw = VoiceWakeup(model_dir=MODEL_DIR, threshold=0.25, cooldown=2.0)

vw.bind("打开风扇", command=[OPENFAN, "1"])
vw.bind("开灯", command=[OPENFAN, "1"])
vw.bind("关闭风扇", command=[OPENFAN, "0"])
vw.bind("关灯", command=[OPENFAN, "0"])

vw.on_detect(lambda word: print(f"[{time.strftime('%H:%M:%S')}] heard: {word}", flush=True))

vw.start()
print("listening... 说「打开风扇/关闭风扇/开灯/关灯」", flush=True)
try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    vw.stop()
