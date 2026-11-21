# 语音唤醒与识别程序

## 功能
- 离线中文语音唤醒
- 麦克风收音测试
- 唤醒词检测演示

## 使用方法

```bash
cd E:\Goharu\voice-wakeup\voice-wakeup-sdk
python demo.py
```

## 演示模式

程序会：
1. 列出可用的中文唤醒词
2. 尝试启动麦克风采集
3. 等待用户说"你好助手"
4. 检测到后打印提示

## 注意事项

- 需要麦克风权限
- Windows 需要安装 sounddevice (已安装)
- 唤醒词需要发音准确
