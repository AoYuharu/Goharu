---
name: voice-wakeup
description: >-
  Offline Chinese voice wakeup (keyword spotting), microphone capture, and optional
  speech-to-text toolkit for agents landing on a new device. Use whenever the user asks
  to add voice wakeup, wake-word detection, voice commands, keyword spotting (KWS),
  offline speech trigger, microphone PCM capture, or offline Chinese speech
  recognition / transcription (ASR) on any platform (Windows, Linux, macOS,
  Android-Termux, embedded ARM). Ships with SDK source plus a bundled int8 KWS model
  (~5MB), runs fully offline on CPU, includes step-by-step agent self-install with
  smoke tests. ASR (SenseVoice + Silero VAD) models are downloaded on demand from
  ModelScope per the instructions in this document.
license: proprietary
metadata:
  version: "1.2.0"
  package_file: voice-wakeup-sdk.tar.gz
  package_size_mb: 5.6
  sdk_modules: "KeywordEngine, MicSource, VoiceWakeup, keywords, asr(SpeechRecognizer/VoiceListener/ensure_models/mic_available)"
  bundled_model: "KWS: sherpa-onnx-kws-zipformer-wenetspeech-3.3M (int8 encoder/decoder/joiner + tokens.txt); ASR: SenseVoice tokens.txt; VAD: silero_vad.onnx (2.3MB)。仅 SenseVoice model.int8.onnx (229MB) 需联网下载"
  platforms: "windows, linux, macos, android-termux, linux-aarch64"
  requires_python: ">=3.9"
  requires_network: "false for KWS; true once for ASR model download"
  mic_backends: "arecord (Linux, zero-dep); sounddevice (Windows/macOS); sox rec pipe (Android-Termux)"
---

# voice-wakeup

离线中文语音技能：给当前设备装上「唤醒词 → 动作」、麦克风采集，以及（可选）「说话 → 文字」转写。用户要求语音唤醒、语音控制、关键词识别、麦克风收音、离线语音识别/转写时使用本技能。

本 skill 是自包含的 agent 自助安装手册：按顺序执行、每步冒烟验证通过后再继续。

## 交付物

同目录 `voice-wakeup-sdk.tar.gz`（5.6MB）解开后是 `voice-wakeup-sdk/`：

- `haru_kws_sdk/` — SDK 源码 v1.2.0：`KeywordEngine`（KWS 引擎）、`MicSource`（跨平台麦克风）、`VoiceWakeup`（唤醒+动作门面）、`keywords.py`（中文→拼音 token）、`asr.py`（SenseVoice 转写 + VoiceListener 唤醒→收音→转写全链路 + 模型自检/下载 + 麦克风检测）
- `kws_model/` — sherpa-onnx KWS 中文模型（int8 ~5MB：encoder/decoder/joiner onnx + tokens.txt）
- `silero-vad/` — Silero VAD 模型（silero_vad.onnx 2.3MB）
- `sense-voice-asr/` — SenseVoice 词表（tokens.txt 309KB）；229MB 的 model.int8.onnx 按需自动下载

**目录约定**：解包后的 `voice-wakeup-sdk/` 目录本身就是 models_root——kws_model、sense-voice-asr、silero-vad 都在其下，`VoiceListener.from_models_root("解包目录/voice-wakeup-sdk")` 开箱即用。

> **Goharu 内约定**：在 Goharu 仓库里 models_root 固定为 `extras/voice-wakeup/voice-wakeup-sdk`（不是 `~/voice-wakeup`），由 `Gateway/voice_service.py` 的 `MODELS_ROOT` 决定；安装向导把 `Skills/voice-wakeup/voice-wakeup-sdk.tar.gz` 解到这里。

特性：完全离线、CPU 推理、识别延迟 <1s、唤醒词运行时热更新（中文自动转拼音）。

## 第一部分：KWS 唤醒（必装）

### 1. 解包

```bash
mkdir -p ~/voice-wakeup && cd ~/voice-wakeup
tar -xzf <本skill目录>/voice-wakeup-sdk.tar.gz
# Windows 上 tar 解含盘符路径要加 --force-local（"C:" 会被误认为远程主机）
```

### 2. 安装依赖（国内镜像源，禁止直连国外源）

```bash
pip install sherpa-onnx pypinyin numpy -i https://pypi.tuna.tsinghua.edu.cn/simple
# 麦克风后端：Linux 用系统 arecord（零依赖，跳过下行）；
# Windows/macOS/Android-Termux 等无 arecord 的环境：
pip install sounddevice -i https://pypi.tuna.tsinghua.edu.cn/simple
```

sherpa-onnx 官方 wheel 覆盖 win-amd64 / linux-x86_64 / linux-aarch64 / macos。Termux（Android）的 Python 是 bionic libc，wheel 装不上时需 `proot-distro install ubuntu` 后在 Ubuntu 环境里装。

### 3. 冒烟验证（必须通过才算安装成功）

```bash
cd ~/voice-wakeup/voice-wakeup-sdk   # haru_kws_sdk 的父目录（注意多一层）
python -c "
from haru_kws_sdk import KeywordEngine, MicSource
import haru_kws_sdk
print('version:', haru_kws_sdk.__version__)
m = MicSource()
print('mic backend:', m.backend)   # Linux 应为 arecord，Win/Mac 应为 sounddevice
e = KeywordEngine(model_dir='kws_model')
e.add_keyword('打开风扇')
print('keywords:', e.list_keywords())
print('OK')
"
```

### 4. 采集链路验证（有麦克风时）

```python
from haru_kws_sdk import MicSource
import time
mic = MicSource(); mic.start()
q = mic.subscribe()
t0 = time.time(); n = 0
while time.time() - t0 < 3:
    q.get(); n += 1
mic.stop()
assert n > 100, "采集块不足，检查麦克风权限/设备"
print(f"mic OK, {n} chunks/3s")
```

Android/Termux 无 arecord/sounddevice 时：`pkg install sox`，用 `rec -t raw -r 16000 -c 1 -b 16 -` 管道采集（stdout 即 16k s16le PCM，可直接喂 `KeywordEngine.feed()`）。

### 5. 使用

```python
from haru_kws_sdk import VoiceWakeup
vw = VoiceWakeup(model_dir="<解包目录>/voice-wakeup-sdk/kws_model", threshold=0.25, cooldown=2.0)
vw.bind("打开风扇", command=["/path/to/cmd", "1"])    # 执行命令
vw.bind("汇报状态", callback=my_func)                  # 调 Python 函数
vw.bind("提醒我", webhook="http://127.0.0.1:9000/hook") # POST {"keyword":..., "ts":...}
vw.on_detect(lambda w: print("heard:", w))            # 所有识别事件
vw.start()   # 后台线程
```

已有音频管道只要引擎：`KeywordEngine` + `engine.feed(pcm)`（16kHz s16le，任意长度，线程安全）。

## 第二部分：ASR 语音转写（可选扩展）

唤醒之外需要「说话转文字」时安装。SDK v1.2.0 已内置完整 ASR 框架（`haru_kws_sdk.asr`）：模型自检+自动下载、麦克风检测、唤醒→收音→转写全链路。**优先用 SDK 完成安装，不要手写下载脚本。**

### 1. 一键自检 + 装模型

```python
from haru_kws_sdk import ensure_models, mic_available

print("mic:", mic_available())        # False → 没麦克风/驱动问题，停止并告知用户
# models_root 直接用解包后的 voice-wakeup-sdk 目录：VAD 和 tokens.txt 包内已有，
# 只会下载缺失的 SenseVoice model.int8.onnx（229MB，ModelScope 国内 CDN）
paths = ensure_models("<解包目录>/voice-wakeup-sdk")
# 返回 {"sense_voice": ".../sense-voice-asr", "vad": ".../silero-vad/silero_vad.onnx"}
```

模型明细（ensure_models 自动处理，供排错参考）：仅 SenseVoice `model.int8.onnx` 229MB 需下载（ModelScope 仓库 `fengge2024/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17`）；VAD 2.3MB 和 tokens.txt 309KB 已内置。ensure_models 失败时改用手动 curl（在 sense-voice-asr/ 目录下执行）：

```bash
curl -L -o sense-voice-asr/model.int8.onnx "https://modelscope.cn/models/fengge2024/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17/resolve/master/model.int8.onnx"
```

依赖同第一部分（sherpa-onnx 已含 VAD 和 SenseVoice 支持，无需额外包）。

### 2. 冒烟验证（转写链路）

```python
from haru_kws_sdk import SpeechRecognizer
rec = SpeechRecognizer("<解包目录>/voice-wakeup-sdk/sense-voice-asr")
print("transcript:", rec.transcribe_file("test.wav"))   # 16kHz 单声道 wav
```

### 3. 使用：唤醒 → 收音 → 转写（完整链路已封装）

```python
from haru_kws_sdk import VoiceListener

listener = VoiceListener.from_models_root("<解包目录>/voice-wakeup-sdk", wake_word="你好")
listener.on_utterance(lambda text: print("said:", text))      # 识别出完整话语
listener.on_state(lambda st: print("state:", st))             # idle/listening/recognizing
listener.start()   # 后台线程：麦克风常开，听到唤醒词自动收音转写
```

判停策略已内置（VAD 静音 1.2s 起试探性转写，按句子完整度动态再等 2.8s/4s/放弃，上限 30s；有新语音则静音清零继续听），可通过 VoiceListener 的 `tentative_s/done_s/incomplete_s/abort_s/max_speech_s` 参数调整。

只要离线转写、不要唤醒流程时，单用 `SpeechRecognizer.transcribe(pcm)`（16kHz s16le bytes）。

### 4. ASR 陷阱（前两条已内置于 SDK 的 clean_text，供排错参考）

- **元标签**：SenseVoice 输出含 `<|zh|><|HAPPY|><|Speech|>` 等标签，SDK 已自动剥除
- **幻觉文本**：对纯静音/噪音会输出短外语碎片（如 "그."）。SDK 的过滤规则：文本含 CJK 字符或长度 >4 才采信
- **判停不能只看标点**：SenseVoice 会给残句脑补句号——SDK 的判停结合 VAD 累计语音时长 + 动态静音策略，不要绕开它自己按标点截断
- **回调死锁**：唤醒回调在引擎锁内执行，回调里只投递队列，录音/转写/`engine.reset()` 全部放主循环做（VoiceListener 已遵守）

## 通用陷阱（必读，都是实测踩过的坑）

1. **回调死锁**：`on_keyword`/`on_detect` 回调在引擎锁内执行——**绝不能在回调里调 `bind/add_keyword/remove_keyword/reset`**，要改词表请丢到主线程/队列里执行
2. **唤醒词长度**：单字/高频二字词（如"你好"）误触发率高；推荐 3 字以上、发音差异大的词
3. **多音字**：pypinyin 取最常见读音，转错则该词永不触发且 add_keyword 不报错——新词上线后必须实测一次
4. **麦克风独占**：同一时间只能一个进程采集；若设备上已有服务占用麦克风，用 `MicSource.subscribe()` 的多订阅模式从现有管道分路，不要重复开设备
5. **Linux 上 pkill 自杀**：远程 ssh 命令行里含服务进程名时，`pkill -f <名字>` 会把 ssh 会话自己杀掉——重启脚本里调用、命令行不要带进程名
6. **模型词表**：token 不在模型词表的词会在 `add_keyword` 抛 `ValueError`（这是好事，说明该词不可识别）
