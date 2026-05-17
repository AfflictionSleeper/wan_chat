# WanChat Native

Native rewrite prototype for WanChat.

Target stack:

- Rust
- windows-rs
- Win32 transparent topmost overlay window
- Direct2D + DirectWrite renderer

This folder is intentionally separate from the Python/Tkinter version.

## Build On Windows

Install Rust from <https://rustup.rs/>, then run one of:

```powershell
.\build.ps1
```

or:

```bat
build.bat
```

Output:

```text
dist\WanChat.exe
```

For a fast compile check without producing an exe:

```powershell
cargo check --target x86_64-pc-windows-msvc
```

## Current Scope

Implemented native scope:

- Transparent topmost overlay window
- Click-through mode and edit mode
- Borderless move/resize in edit mode
- Direct2D/DirectWrite danmaku renderer
- Danmaku queue, active item cap, and per-frame render budget
- Style parser and filters ported as pure Rust logic
- System tray menu
- Opacity, speed, font size settings
- Keyword and user ID filters
- Bilibili WBI + WebSocket danmaku client
- Brotli/Zlib protocol decompression
- Auto-connect from `config.json`

`config.json` is read from the executable directory and may contain Bilibili Cookie. Do not commit it.
