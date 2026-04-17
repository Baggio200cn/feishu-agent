@echo off
chcp 65001 >nul
cd /d "%~dp0"

REM 检查是否有 Python
python --version >nul 2>&1
if errorlevel 1 (
    echo 未找到 Python，请安装 Python 3.8+
    pause
    exit /b 1
)

REM 如果图标还未生成，自动生成
if not exist "assets\icon.ico" (
    echo 首次运行：生成图标...
    python create_icons.py
)

REM 启动 GUI
start "" pythonw launcher.pyw
