@echo off
chcp 65001 >nul
title 编译 Steam Input 备份管理器为 EXE

echo ========================================
echo   Steam Input 备份管理器 - 编译为 EXE
echo ========================================
echo.

:: 检查是否在正确目录
if not exist "steam_input_backup.py" (
    echo [错误] 未找到 steam_input_backup.py
    echo 请确保本脚本与 steam_input_backup.py、icon.png 放在同一文件夹
    pause
    exit /b 1
)

echo [1/4] 正在检查并安装依赖...
python -m pip install --upgrade pip >nul 2>&1
python -m pip install pyinstaller pystray Pillow -q

if errorlevel 1 (
    echo [错误] 依赖安装失败，请检查网络或 Python 环境
    pause
    exit /b 1
)

echo [2/4] 正在清理旧的编译文件...
if exist "build" rmdir /s /q "build"
if exist "dist" rmdir /s /q "dist"
if exist "SteamInputBackup.spec" del /q "SteamInputBackup.spec"

echo [3/4] 正在编译为单文件 EXE（无控制台窗口）...
python -m PyInstaller ^
    --noconfirm ^
    --onefile ^
    --windowed ^
    --name "SteamInputBackup" ^
    --icon=icon.png ^
    --add-data "icon.png;." ^
    --hidden-import=pystray ^
    --hidden-import=PIL ^
    steam_input_backup.py

if errorlevel 1 (
    echo.
    echo [错误] 编译失败！请查看上方错误信息
    pause
    exit /b 1
)

echo [4/4] 编译完成！
echo.
echo EXE 文件位置：
echo   dist\SteamInputBackup.exe
echo.
echo 你可以直接把这个 exe 复制到任意地方使用（无需 Python 环境）
echo.
pause
