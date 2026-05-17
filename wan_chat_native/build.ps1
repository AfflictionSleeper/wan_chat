$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Get-Command cargo -ErrorAction SilentlyContinue)) {
    throw "Rust/Cargo not found. Install from https://rustup.rs/ first."
}

cargo build --release
New-Item -ItemType Directory -Force -Path dist | Out-Null
Copy-Item target\release\wan_chat_native.exe dist\WanChat.exe -Force
Write-Host "Built dist\WanChat.exe"
