@echo off
setlocal
cd /d "%~dp0"

where cargo >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Rust/Cargo not found. Install from https://rustup.rs/ first.
  exit /b 1
)

cargo build --release
if errorlevel 1 exit /b 1

if not exist dist mkdir dist
copy /Y target\release\wan_chat_native.exe dist\WanChat.exe >nul
echo Built dist\WanChat.exe
