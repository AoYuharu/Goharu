# 语音识别与唤醒技能

## 概述
启用语音识别和语音唤醒功能，允许通过语音控制应用程序和与 AI 助手交互。

## 技术实现

### Web Speech API

#### 语音识别 (SpeechRecognition)
```javascript
// 检查浏览器支持
if ('webkitSpeechRecognition' in window || 'SpeechRecognition' in window) {
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    const recognition = new SpeechRecognition();
    
    // 配置
    recognition.continuous = false;      // 持续识别
    recognition.interimResults = true;    // 返回临时结果
    recognition.lang = 'zh-CN';          // 中文
    recognition.maxAlternatives = 1;      // 返回最佳结果数
    
    // 事件处理
    recognition.onresult = (event) => {
        const transcript = event.results[0][0].transcript;
        console.log('识别结果:', transcript);
    };
    
    recognition.onerror = (event) => {
        console.error('识别错误:', event.error);
    };
    
    recognition.onend = () => {
        console.log('识别结束');
    };
    
    // 开始识别
    recognition.start();
}
```

#### 语音唤醒 (Wake Word Detection)
```javascript
class WakeWordDetector {
    constructor(wakeWords = ['你好助手', '唤醒', '嘿']) {
        this.wakeWords = wakeWords;
        this.isListening = false;
        this.recognition = null;
    }
    
    start() {
        this.recognition = new (window.SpeechRecognition || window.webkitSpeechRecognition)();
        this.recognition.continuous = true;
        this.recognition.interimResults = false;
        this.recognition.lang = 'zh-CN';
        
        this.recognition.onresult = (event) => {
            const transcript = event.results[event.results.length - 1][0].transcript
                .toLowerCase().trim();
            
            // 检查唤醒词
            for (const wakeWord of this.wakeWords) {
                if (transcript.includes(wakeWord.toLowerCase())) {
                    this.onWake();
                    break;
                }
            }
        };
        
        this.recognition.onerror = (event) => {
            if (event.error !== 'no-speech') {
                console.error('唤醒错误:', event.error);
            }
        };
        
        this.recognition.start();
        this.isListening = true;
    }
    
    stop() {
        if (this.recognition) {
            this.recognition.stop();
            this.isListening = false;
        }
    }
    
    onWake() {
        console.log('🎤 唤醒成功！');
        // 触发后续交互
    }
}
```

### 语音合成 (Text-to-Speech)
```javascript
function speak(text, lang = 'zh-CN') {
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.lang = lang;
    utterance.rate = 1.0;      // 语速
    utterance.pitch = 1.0;    // 音调
    utterance.volume = 1.0;    // 音量
    
    // 选择中文语音
    const voices = speechSynthesis.getVoices();
    const chineseVoice = voices.find(v => v.lang.includes('zh'));
    if (chineseVoice) {
        utterance.voice = chineseVoice;
    }
    
    speechSynthesis.speak(utterance);
}

// 获取可用语音列表
speechSynthesis.onvoiceschanged = () => {
    const voices = speechSynthesis.getVoices();
    console.log('可用语音:', voices.map(v => `${v.name} (${v.lang})`));
};
```

## 完整示例：AI 助手语音交互

```javascript
class VoiceAssistant {
    constructor() {
        this.recognition = null;
        this.isActive = false;
        this.wakeWords = ['你好助手', '嘿'];
        this.onCommand = null;  // 语音命令回调
    }
    
    init() {
        const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
        if (!SpeechRecognition) {
            console.error('浏览器不支持语音识别');
            return false;
        }
        
        this.recognition = new SpeechRecognition();
        this.recognition.continuous = true;
        this.recognition.interimResults = true;
        this.recognition.lang = 'zh-CN';
        
        this.recognition.onresult = (event) => {
            const results = event.results;
            const last = results[results.length - 1];
            const transcript = last[0].transcript.trim();
            
            // 检查是否是唤醒词
            if (this.isWakeWord(transcript)) {
                this.onWake();
                return;
            }
            
            // 如果已激活，处理命令
            if (this.isActive && last.isFinal) {
                this.processCommand(transcript);
            }
        };
        
        this.recognition.onerror = (event) => {
            console.error('语音错误:', event.error);
        };
        
        return true;
    }
    
    isWakeWord(text) {
        const lower = text.toLowerCase();
        return this.wakeWords.some(w => lower.includes(w.toLowerCase()));
    }
    
    onWake() {
        console.log('🎤 被唤醒！');
        this.isActive = true;
        this.speak('我在，请说');
        
        // 3秒后自动休眠
        setTimeout(() => {
            if (this.isActive) {
                this.isActive = false;
                this.speak('好的，我去休息了');
            }
        }, 5000);
    }
    
    processCommand(text) {
        console.log('📝 命令:', text);
        if (this.onCommand) {
            this.onCommand(text);
        }
    }
    
    speak(text) {
        const utterance = new SpeechSynthesisUtterance(text);
        utterance.lang = 'zh-CN';
        speechSynthesis.speak(utterance);
    }
    
    start() {
        if (this.recognition) {
            this.recognition.start();
            console.log('🎤 语音助手已启动，等待唤醒词...');
        }
    }
    
    stop() {
        if (this.recognition) {
            this.recognition.stop();
        }
    }
}

// 使用示例
const assistant = new VoiceAssistant();
if (assistant.init()) {
    assistant.start();
    
    assistant.onCommand = (text) => {
        assistant.speak('收到命令: ' + text);
        // 处理命令逻辑
    };
}
```

## 应用场景

### 1. 语音控制游戏
```javascript
// 在游戏中添加语音控制
const gameVoice = new VoiceAssistant();
gameVoice.wakeWords = ['开始游戏', '暂停'];
gameVoice.onCommand = (cmd) => {
    if (cmd.includes('开始')) startGame();
    if (cmd.includes('暂停')) pauseGame();
    if (cmd.includes('射击')) player.fire();
};
```

### 2. 语音输入文本
```javascript
// 语音转文字
const voiceInput = new SpeechRecognition();
voiceInput.continuous = true;
voiceInput.onresult = (e) => {
    const input = document.getElementById('input');
    input.value += e.results[e.results.length-1][0].transcript;
};
voiceInput.start();
```

### 3. 语音反馈
```javascript
// 游戏事件语音反馈
function playVoice(type) {
    const sounds = {
        hit: '击中目标',
        damage: '生命值下降',
        win: '恭喜通关',
        gameOver: '游戏结束'
    };
    if (sounds[type]) speak(sounds[type]);
}
```

## 浏览器兼容性

| 浏览器 | 支持情况 |
|--------|----------|
| Chrome | ✅ 完全支持 |
| Edge | ✅ 完全支持 |
| Safari | ⚠️ 部分支持 (需要 webkit 前缀) |
| Firefox | ❌ 不支持 |
| IE | ❌ 不支持 |

## 注意事项

1. **HTTPS 要求**：大多数浏览器要求在 HTTPS 环境下使用语音 API
2. **用户授权**：首次使用需要用户授权麦克风
3. **网络依赖**：语音识别需要网络连接（部分浏览器支持离线）
4. **隐私问题**：确保用户知情并同意语音采集
