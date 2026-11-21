# haru_kws_sdk — 离线中文语音唤醒 SDK

完全离线的关键词唤醒 + 音频采集工具包，基于 sherpa-onnx。设计目标：**任何有麦克风的设备，装上就能用**。

## 模块

| 模块 | 说明 | 依赖 |
|---|---|---|
| `KeywordEngine` | 纯识别引擎：喂 PCM → 回调关键词 | sherpa-onnx, numpy, pypinyin |
| `MicSource` | 跨平台麦克风采集，多订阅者 | Linux 零依赖（arecord）；其他平台需 sounddevice |
| `VoiceWakeup` | 高层门面：采集+识别+动作绑定 | 以上全部 |
| `keywords.py` | 中文词 → 拼音 token 转换 | pypinyin |

子模块惰性导入：只用 `MicSource` 不需要 sherpa-onnx。

## 快速开始

```python
from haru_kws_sdk import VoiceWakeup

vw = VoiceWakeup(model_dir="/path/to/sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01",
                 threshold=0.25, cooldown=2.0)
vw.bind("打开风扇", command=["/path/to/openfan", "1"])   # 执行命令
vw.bind("现在几点", callback=lambda w: print(w))          # 调函数
vw.bind("汇报状态", webhook="http://127.0.0.1:9000/hook")  # POST JSON
vw.on_detect(lambda word: print("heard:", word))
vw.start()   # 后台线程，不阻塞
```

关键词运行时热更新：`vw.bind("新词", ...)` / `vw.unbind("词")` / `engine.add_keyword()`，自动拼音转 token 立即生效。

## 跨平台

| 平台 | 采集后端 | 识别 |
|---|---|---|
| Linux (x86/ARM) | arecord（零依赖） | sherpa-onnx ✓ |
| Windows | sounddevice | sherpa-onnx ✓ |
| macOS | sounddevice | sherpa-onnx ✓ |

- 统一输出 16kHz 单声道 s16le PCM，下游与平台无关
- 设备不支持 16kHz → 自动用原生采样率采集 + numpy 重采样
- 采集异常退出 → 自动重启（退避 2s）
- 模型文件（.onnx）全平台通用

## 嵌入已有音频管道（只要引擎）

```python
from haru_kws_sdk import KeywordEngine

engine = KeywordEngine(model_dir="...", threshold=0.25)
engine.add_keyword("打开风扇")     # token 不在模型词表时抛 ValueError
engine.on_keyword(my_handler)    # my_handler(word: str)，可注册多个
engine.feed(pcm_bytes)           # 16kHz s16le，任意长度，线程安全
engine.reset()                   # 音频源断流重连后清状态
```

**重要**：`on_keyword` 回调在引擎锁内执行——**不要在回调里调 `add_keyword/remove_keyword/reset`**（会死锁），要改词表请丢到主线程队列执行。

## 只采集不识别（喂自己的 ASR）

```python
from haru_kws_sdk import MicSource

mic = MicSource()                # 全自动选后端和设备
mic.start()
q = mic.subscribe()              # queue.Queue，~20ms PCM 块
```

## 已知限制

- 命令词建议 2 字以上、发音差异大；单字/高频词（如"你好"）误触发率高
- pypinyin 多音字取最常见读音，转错则该词永不触发且 add_keyword 不报错
- 模型建模单元是带声调拼音，token 不在模型词表的词会在 add_keyword 时抛 ValueError

## 参考部署（haru RK3588 开发板）

模型：`sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01`（36MB，int8）。
SenseVoice ASR（`model.int8.onnx` 229MB）+ Silero VAD（2.3MB）组合的完整
唤醒→收音→转写实现见 haru 上 `~/cam_server.py`（含端点检测、幻觉过滤、网页字幕推送）。
