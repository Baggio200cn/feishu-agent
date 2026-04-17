@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 飞书智能对话助理（命令行版）
echo 输入 exit 退出
echo ================================
:loop
set /p msg=你:
if /i "%msg%"=="exit" goto end
python agent_cli.py %msg%
echo.
goto loop
:end
