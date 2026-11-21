"""haru_kws_sdk - 离线中文语音唤醒 + 转写 SDK (sherpa-onnx 封装)

模块:
  engine   - KeywordEngine: 纯关键词识别引擎, 喂 PCM 数据, 回调检测到的词
  mic      - MicSource: 跨平台麦克风采集 (Linux=arecord 零依赖, 其他=sounddevice),
             多订阅者, 统一输出 16kHz s16le PCM
  wakeup   - VoiceWakeup: 高层门面, 麦克风 + 引擎 + 动作绑定, 一键启动
  asr      - SpeechRecognizer: 离线转写 (SenseVoice); VoiceListener: 唤醒→收音→转写;
             ensure_models(): 模型自检+自动下载; mic_available(): 麦克风检测
  keywords - 中文词 -> 模型 token 序列的转换工具

子模块惰性导入：只 import MicSource 时不需要 sherpa-onnx/pypinyin，
自传播部署时可以按需裁剪依赖。
"""

__version__ = "1.2.0"
__all__ = ["KeywordEngine", "MicSource", "VoiceWakeup",
           "SpeechRecognizer", "VoiceListener", "ensure_models", "mic_available"]


def __getattr__(name):
    if name == "KeywordEngine":
        from .engine import KeywordEngine
        return KeywordEngine
    if name == "MicSource":
        from .mic import MicSource
        return MicSource
    if name == "VoiceWakeup":
        from .wakeup import VoiceWakeup
        return VoiceWakeup
    if name in ("SpeechRecognizer", "VoiceListener", "ensure_models", "mic_available"):
        from . import asr
        return getattr(asr, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
