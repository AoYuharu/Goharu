#!/usr/bin/env python3
"""
语音唤醒与识别服务器
使用 haru_kws_sdk 进行离线中文语音唤醒
"""

import os
import sys
import threading
import time
import base64
import json
import logging
from pathlib import Path
from flask import Flask, render_template, request, jsonify, Response
from flask_cors import CORS

# 添加 SDK 路径
SDK_DIR = Path(__file__).parent / "voice-wakeup-sdk"
sys.path.insert(0, str(SDK_DIR))

from haru_kws_sdk import VoiceWakeup, KeywordEngine

# 配置
HOST = "0.0.0.0"
PORT = 8765
WAKE_THRESHOLD = 0.25
COOLDOWN = 2.0

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__, 
            template_folder="templates",
            static_folder=None)
CORS(app)

# 全局变量
voice_wakeup = None
is_running = False
wake_thread = None
records = []
records_lock = threading.Lock()

def init_voice_wakeup():
    """初始化语音唤醒"""
    global voice_wakeup
    
    model_dir = SDK_DIR / "kws_model"
    
    if not model_dir.exists():
        logger.error(f"模型目录不存在: {model_dir}")
        return False
    
    try:
        # Windows 上不指定 device，让 MicSource 自动选择默认设备
        voice_wakeup = VoiceWakeup(
            model_dir=str(model_dir),
            threshold=WAKE_THRESHOLD,
            cooldown=COOLDOWN
        )
        
        # 绑定唤醒词
        voice_wakeup.bind("你好助手", callback=on_wake)
        voice_wakeup.bind("开始记录", callback=on_start_record)
        voice_wakeup.bind("停止记录", callback=on_stop_record)
        
        logger.info("语音唤醒初始化成功")
        return True
        
    except Exception as e:
        logger.error(f"初始化失败: {e}")
        return False

def on_wake(keyword):
    """唤醒回调"""
    logger.info(f"🎤 唤醒词: {keyword}")
    add_record(f"[唤醒] {keyword}")
    return True

def on_start_record(keyword):
    """开始记录"""
    logger.info("开始记录语音")
    add_record("[系统] 开始记录")
    return True

def on_stop_record(keyword):
    """停止记录"""
    logger.info("停止记录语音")
    add_record("[系统] 停止记录")
    return True

def add_record(text):
    """添加记录"""
    with records_lock:
        records.insert(0, {
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "text": text
        })
        # 只保留最近100条
        if len(records) > 100:
            records.pop()

def voice_loop():
    """语音监听主循环"""
    global is_running
    
    logger.info("语音监听线程已启动 (后台自动运行)")
    
    # VoiceWakeup.start() 已经创建后台线程，无需额外轮询
    # 我们只需要检查 running 状态
    while is_running:
        try:
            if voice_wakeup and not voice_wakeup.running:
                logger.warning("检测到 VoiceWakeup 意外停止")
                break
            time.sleep(0.5)  # 每0.5秒检查一次状态
        except Exception as e:
            logger.error(f"语音循环错误: {e}")
            time.sleep(1)

@app.route("/")
def index():
    """主页"""
    return render_template("voice_client.html")

@app.route("/api/status")
def status():
    """获取状态"""
    return jsonify({
        "running": is_running,
        "records_count": len(records)
    })

@app.route("/api/start", methods=["POST"])
def start():
    """启动语音监听"""
    global is_running, wake_thread
    
    if is_running:
        return jsonify({"status": "already_running"})
    
    if not voice_wakeup:
        if not init_voice_wakeup():
            return jsonify({"status": "error", "message": "初始化失败"})
    
    is_running = True
    voice_wakeup.start()
    
    wake_thread = threading.Thread(target=voice_loop, daemon=True)
    wake_thread.start()
    
    logger.info("语音监听已启动")
    return jsonify({"status": "started"})

@app.route("/api/stop", methods=["POST"])
def stop():
    """停止语音监听"""
    global is_running
    
    if not is_running:
        return jsonify({"status": "not_running"})
    
    is_running = False
    
    if voice_wakeup:
        voice_wakeup.stop()
    
    logger.info("语音监听已停止")
    return jsonify({"status": "stopped"})

@app.route("/api/records")
def get_records():
    """获取记录"""
    with records_lock:
        return jsonify(records)

@app.route("/api/records", methods=["DELETE"])
def clear_records():
    """清空记录"""
    with records_lock:
        records.clear()
    return jsonify({"status": "cleared"})

@app.route("/api/speak", methods=["POST"])
def speak():
    """文字转语音"""
    from flask import request
    text = request.json.get("text", "")
    
    if not text:
        return jsonify({"error": "text required"})
    
    # 使用系统 TTS（如果有）
    try:
        if os.name == "nt":
            import winsound
            # Windows TTS
            import comtypes.client as cc
            cc.cInitCoCreateInstance()
            speaker = cc.CreateObject("SAPI.SpVoice")
            speaker.Speak(text)
        else:
            # Linux/Mac: 使用 espeak 或 say
            os.system(f'espeak "{text}"' if os.name != "nt" else f'say "{text}"')
    except Exception as e:
        logger.warning(f"TTS 失败: {e}")
    
    return jsonify({"status": "ok"})

@app.route("/api/keywords", methods=["GET"])
def get_keywords():
    """获取当前唤醒词"""
    if voice_wakeup and hasattr(voice_wakeup, 'engine'):
        try:
            return jsonify(voice_wakeup.engine.list_keywords())
        except:
            pass
    return jsonify(["你好助手", "开始记录", "停止记录"])

if __name__ == "__main__":
    print(f"""
╔══════════════════════════════════════╗
║    🎤 语音唤醒服务器                 ║
╠══════════════════════════════════════╣
║  地址: http://localhost:{PORT}        ║
║  按 Ctrl+C 停止                     ║
╚══════════════════════════════════════╝
    """)
    
    # 尝试初始化
    if init_voice_wakeup():
        print("✅ 语音唤醒已就绪")
    else:
        print("⚠️ 语音唤醒初始化失败，但服务器仍可运行")
    
    app.run(host=HOST, port=PORT, debug=False, threaded=True)
