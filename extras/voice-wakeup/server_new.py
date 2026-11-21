#!/usr/bin/env python3
"""语音唤醒与识别服务器"""
import os, sys, time, threading, queue, logging
from pathlib import Path

SDK_DIR = Path(__file__).parent / 'voice-wakeup-sdk'
sys.path.insert(0, str(SDK_DIR))

import sherpa_onnx
from haru_kws_sdk import VoiceWakeup, MicSource

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

MODEL_DIR = str(SDK_DIR / 'kws_model')

class VoiceSystem:
    def __init__(self):
        self.voice_wakeup = None
        self.vad = None
        self.is_running = False
        self.is_recording = False
        self.records = []
        self.lock = threading.Lock()
        self.audio_buffer = []
        self.last_speech_time = 0
        self.SILENCE_TIMEOUT = 3.0
        self.has_speech = False  # VAD 检测到语音
        self.vad_stream = None
        
    def init_vad(self):
        """初始化 Silero VAD"""
        try:
            vad_model = str(Path(__file__).parent / 'asr_model' / 'silero-vad.onnx')
            if not os.path.exists(vad_model):
                logger.warning(f'VAD模型不存在: {vad_model}')
                return False
            vad_config = sherpa_onnx.SileroVadModelConfig(
                model=vad_model,
                threshold=0.5,
                min_speech_duration_ms=250,
                min_silence_duration_ms=500,
            )
            self.vad = sherpa_onnx.VadModel(config=vad_config)
            self.vad_stream = self.vad.create_stream()
            logger.info('VAD 初始化成功')
            return True
        except Exception as e:
            logger.warning(f'VAD初始化失败: {e}')
            return False
        
    def init_voice_wakeup(self):
        try:
            # Windows上 device=None 让 MicSource 自动选择默认麦克风
            from haru_kws_sdk import VoiceWakeup
            self.voice_wakeup = VoiceWakeup(model_dir=MODEL_DIR, device=None, threshold=0.25, cooldown=2.0)
            self.voice_wakeup.bind('你好助手', callback=self.on_wake)
            self.voice_wakeup.on_detect(lambda w: logger.info(f'检测到: {w}'))
            logger.info('唤醒初始化成功')
            return True
        except Exception as e:
            logger.error(f'唤醒初始化失败: {e}')
            return False
    
    def on_wake(self, keyword):
        logger.info(f'唤醒词: {keyword}')
        self.start_recording()
        self.add_record(f'[唤醒] {keyword}')
        return True
    
    def start_recording(self):
        with self.lock:
            if self.is_recording: return
            self.is_recording = True
            self.audio_buffer = []
        logger.info('开始录音')
    
    def stop_recording(self):
        with self.lock:
            if not self.is_recording: return
            self.is_recording = False
            audio_data = self.audio_buffer
            self.audio_buffer = []
        
        if audio_data:
            import numpy as np
            audio = np.concatenate([np.frombuffer(a, dtype=np.int16) for a in audio_data])
            if len(audio) > 1600:
                self.add_record(f'[已录制 {len(audio)/16000:.1f}秒音频]')
            else:
                self.add_record('[音频太短]')
    
    def add_record(self, text):
        with self.lock:
            self.records.insert(0, {'time': time.strftime("%H:%M:%S"), 'text': text})
            if len(self.records) > 100: self.records.pop()
    
    def audio_loop(self):
        import numpy as np
        mic = MicSource()
        mic.start()
        q = mic.subscribe()
        logger.info('音频处理线程启动')
        
        while self.is_running:
            try:
                chunk = q.get(timeout=1)
                
                # VAD 检测语音活动
                if self.vad and self.vad_stream:
                    samples = np.frombuffer(chunk, dtype=np.int16)
                    self.vad.accept_waveform(self.vad_stream, samples)
                    
                    # 检查是否检测到语音
                    is_speech = False
                    while self.vad.is_ready(self.vad_stream):
                        self.vad.decode(self.vad_stream)
                        if self.vad.is_speech(self.vad_stream, chunk):
                            is_speech = True
                    
                    with self.lock:
                        if self.is_recording:
                            # VAD检测到语音时才重置计时器
                            if is_speech:
                                self.last_speech_time = time.time()
                                self.has_speech = True
                            # 缓冲音频
                            self.audio_buffer.append(bytes(chunk))
                        
                        # 检查静默超时
                        if self.is_recording and self.audio_buffer:
                            if time.time() - self.last_speech_time > self.SILENCE_TIMEOUT:
                                self.stop_recording()
                
                # 备用：无VAD时的能量检测
                if not self.vad:
                    import numpy as np
                    energy = np.abs(np.frombuffer(chunk, dtype=np.int16)).mean()
                    with self.lock:
                        if self.is_recording:
                            # 缓冲音频
                            self.audio_buffer.append(bytes(chunk))
                            if energy > 500:  # 有声音
                                self.last_speech_time = time.time()
                            elif time.time() - self.last_speech_time > self.SILENCE_TIMEOUT:
                                self.stop_recording()
            except queue.Empty:
                continue
            except Exception as e:
                logger.error(f'错误: {e}')
        
        mic.stop()
    
    def start(self):
        if self.is_running: return {'status': 'already_running'}
        if not self.voice_wakeup and not self.init_voice_wakeup():
            return {'status': 'error'}
        self.init_vad()  # 初始化 VAD
        self.is_running = True
        self.voice_wakeup.start()
        self.audio_thread = threading.Thread(target=self.audio_loop, daemon=True)
        self.audio_thread.start()
        logger.info('系统已启动')
        return {'status': 'started'}
    
    def stop(self):
        if not self.is_running: return {'status': 'not_running'}
        self.is_running = False
        self.is_recording = False
        if self.voice_wakeup: self.voice_wakeup.stop()
        logger.info('系统已停止')
        return {'status': 'stopped'}
    
    def get_status(self):
        with self.lock:
            return {'running': self.is_running, 'recording': self.is_recording, 'records_count': len(self.records)}
    
    def get_records(self):
        with self.lock:
            return list(self.records)
    
    def clear_records(self):
        with self.lock: self.records.clear()
        return {'status': 'cleared'}

from flask import Flask, render_template, jsonify
from flask_cors import CORS

app = Flask(__name__, template_folder='templates')
CORS(app)
vs = VoiceSystem()

@app.route('/')
def index(): return render_template('voice_app.html')

@app.route('/api/status')
def status(): return jsonify(vs.get_status())

@app.route('/api/start', methods=['POST'])
def start(): return jsonify(vs.start())

@app.route('/api/stop', methods=['POST'])
def stop(): return jsonify(vs.stop())

@app.route('/api/records')
def get_records(): return jsonify(vs.get_records())

@app.route('/api/records', methods=['DELETE'])
def clear_records(): return jsonify(vs.clear_records())

if __name__ == '__main__':
    print('\n🎤 语音唤醒与识别系统\n地址: http://localhost:8765\n')
    if vs.init_voice_wakeup(): print('✅ 语音唤醒已就绪')
    app.run(host="0.0.0.0", port=8765, debug=False, threaded=True)
