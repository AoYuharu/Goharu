#!/usr/bin/env python3
"""麦克风测试脚本"""
import sys
from pathlib import Path

SDK_DIR = Path(__file__).parent / "voice-wakeup-sdk"
sys.path.insert(0, str(SDK_DIR))

from haru_kws_sdk import MicSource
import time

print("=" * 50)
print("麦克风测试")
print("=" * 50)

# 测试麦克风采集
print("\n1. 测试麦克风采集...")
try:
    mic = MicSource()
    print(f"   后端: {mic.backend}")
    print(f"   设备: {mic.device}")
    print("   启动麦克风...")
    mic.start()
    
    q = mic.subscribe()
    print("   等待音频数据...")
    
    for i in range(5):
        chunk = q.get(timeout=2)
        print(f"   收到音频块 {i+1}: {len(chunk)} 字节")
    
    mic.stop()
    print("   ✅ 麦克风采集正常!")
except Exception as e:
    print(f"   ❌ 麦克风采集失败: {e}")

# 测试唤醒词检测
print("\n2. 测试唤醒词检测...")
MODEL_DIR = SDK_DIR / "kws_model"
if not MODEL_DIR.exists():
    print(f"   ❌ 模型目录不存在: {MODEL_DIR}")
else:
    try:
        from haru_kws_sdk import VoiceWakeup
        vw = VoiceWakeup(model_dir=str(MODEL_DIR), threshold=0.25, cooldown=1.0)
        vw.bind("你好助手", callback=lambda w: print(f"   唤醒词检测成功: {w}"))
        print("   启动监听...")
        vw.start()
        print("   ✅ 说'你好助手'测试（10秒）...")
        print("   按 Ctrl+C 退出")
        time.sleep(10)
        vw.stop()
    except Exception as e:
        print(f"   ❌ 唤醒词检测失败: {e}")

print("\n" + "=" * 50)
print("测试完成")
print("=" * 50)
