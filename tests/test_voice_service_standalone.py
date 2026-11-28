"""语音服务契约测试：依赖探测 / refresh / 激活事件 / 模型缺失拒绝启动。

运行：python tests/test_voice_service_standalone.py
"""

import os
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)   # configurationLoader 依赖仓库内 config.yaml

import Gateway.voice_service as vs  # noqa: E402

PASS, FAIL = 0, 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {detail}")


def _reset():
    vs._sdk_ok = None
    vs._deps_state = None


def test_deps_missing():
    print("== 依赖缺失 → 不可用且有 deps_error ==")
    old = vs._missing_deps
    try:
        _reset()
        vs._missing_deps = lambda: "缺少语音依赖: sherpa_onnx"
        ok, err = vs.deps_available()
        check("deps_available=False", ok is False)
        check("deps_error 说明缺失项", "sherpa_onnx" in (err or ""), err)
        check("sdk_available=False", vs.sdk_available() is False)
        started, msg = vs.start_listener(lambda *_a: None)
        check("激活被拒绝并说明原因", started is False and "sherpa_onnx" in msg, msg)
    finally:
        vs._missing_deps = old
        _reset()


def test_refresh_reprobe():
    print("== refresh() 后重新探测 ==")
    old = vs._missing_deps
    try:
        _reset()
        vs._missing_deps = lambda: "缺少语音依赖: pypinyin"
        check("首次探测失败", vs.deps_available()[0] is False)
        vs._missing_deps = lambda: None
        vs.refresh()
        check("refresh 后依赖就绪", vs.deps_available() == (True, None))
    finally:
        vs._missing_deps = old
        _reset()


def test_model_missing_rejects():
    print("== 模型缺失拒绝启动 ==")
    old_models, old_sdk = vs._model_states, vs.sdk_available
    try:
        vs.sdk_available = lambda: True
        vs._model_states = lambda: {"kws": True, "vad": True, "tokens": True,
                                    "sensevoice": False}
        started, msg = vs.start_listener(lambda *_a: None)
        check("模型未就绪不启动", started is False and "模型" in msg, msg)
        check("listener 未运行", vs.listener_running() is False)
    finally:
        vs._model_states, vs.sdk_available = old_models, old_sdk
        vs.stop_listener()


def test_activation_event():
    print("== 激活触发 voice.activated ==")
    fake = types.ModuleType("haru_kws_sdk")

    class FakeListener:
        @classmethod
        def from_models_root(cls, root, wake_word=None):
            return cls()

        def on_utterance(self, cb):
            self._utterance = cb

        def on_state(self, cb):
            self._state = cb

        def start(self):
            pass

        def stop(self):
            pass

    fake.VoiceListener = FakeListener
    old_models, old_sdk, old_path = vs._model_states, vs.sdk_available, vs._sdk_path
    had_module = "haru_kws_sdk" in sys.modules
    old_module = sys.modules.get("haru_kws_sdk")
    try:
        sys.modules["haru_kws_sdk"] = fake
        vs._sdk_path = lambda: None
        vs.sdk_available = lambda: True
        vs._model_states = lambda: {"kws": True, "vad": True, "tokens": True,
                                    "sensevoice": True}
        events = []
        ok, msg = vs.start_listener(lambda t, p: events.append((t, p)))
        check("启动成功", ok is True, msg)
        check("发出 voice.activated",
              any(t == "voice.activated" and p.get("wake_word") == "你好"
                  for t, p in events), events)
        check("激活事件携带 LED 裁决",
              any(t == "voice.activated" and isinstance(p.get("indicator"), dict)
                  for t, p in events), events)
        check("listener_running=True", vs.listener_running() is True)
        vs.stop_listener()
        check("stop 后 listener_running=False", vs.listener_running() is False)
    finally:
        vs._model_states, vs.sdk_available, vs._sdk_path = (
            old_models, old_sdk, old_path)
        if had_module:
            sys.modules["haru_kws_sdk"] = old_module
        else:
            sys.modules.pop("haru_kws_sdk", None)
        vs.stop_listener()


def main():
    print("=" * 60)
    print("语音服务契约测试")
    print("=" * 60)
    test_deps_missing()
    test_refresh_reprobe()
    test_model_missing_rejects()
    test_activation_event()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
