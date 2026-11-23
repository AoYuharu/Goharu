@echo off
REM Goharu 安装入口（Windows）—— 转入交互式安装向导 install.py
REM 直接双击或在 cmd 运行本脚本即进入分步引导页。
cd /d "%~dp0"
python install.py %*
