@echo off
chcp 65001 >nul
echo ========================================
echo   B站弹幕机 - Windows 编译脚本
echo ========================================
echo.

REM 检查 Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [错误] 未找到 Python，请先安装 Python 3.8+
    pause
    exit /b 1
)

echo [1/3] 安装依赖...
pip install -r requirements.txt
if %errorlevel% neq 0 (
    echo [错误] 依赖安装失败
    pause
    exit /b 1
)

echo [2/3] 清理旧的构建文件...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
if exist __pycache__ rmdir /s /q __pycache__

echo [3/3] 编译中...
pyinstaller ^
    --onefile ^
    --windowed ^
    --name "DanmakuOverlay" ^
    --add-data "config.json;." ^
    --hidden-import pystray._win32 ^
    --hidden-import pystray._util ^
    --hidden-import pystray._appindicator ^
    --hidden-import pystray._gtk ^
    --hidden-import pystray._dummy ^
    --hidden-import PIL._tkinter_finder ^
    --collect-all pystray ^
    --clean ^
    main.py

if %errorlevel% neq 0 (
    echo [错误] 编译失败
    pause
    exit /b 1
)

echo.
echo ========================================
echo   编译完成！exe 文件在 dist\ 目录下
echo   文件: dist\DanmakuOverlay.exe
echo ========================================
echo.
echo 使用方法:
echo   1. 双击 DanmakuOverlay.exe 启动
echo   2. 任务栏托盘图标右键 -> 菜单控制
echo   3. 选择"编辑模式"调整窗口位置和大小
echo   4. 选择"穿透模式"让鼠标穿过窗口操作游戏
echo ========================================
pause
