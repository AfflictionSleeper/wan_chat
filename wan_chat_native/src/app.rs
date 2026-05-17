use std::sync::mpsc;
use std::time::{Duration, Instant};

use windows::Win32::System::Com::{CoInitializeEx, COINIT_APARTMENTTHREADED};
use windows::Win32::UI::WindowsAndMessaging::{DispatchMessageW, PeekMessageW, TranslateMessage, MSG, PM_REMOVE, WM_QUIT};

use crate::bilibili::client::{BilibiliClient, BilibiliEvent};
use crate::config::{normalize_cookie, normalize_keywords, normalize_user_ids, Config};
use crate::danmaku::engine::DanmakuEngine;
use crate::dialogs::{show_connect_dialog, show_error, show_multiline_dialog};
use crate::overlay_window::{OverlayMode, OverlayWindow};
use crate::renderer::d2d::D2DRenderer;
use crate::tray::{AppCommand, TrayController};

const TARGET_FRAME: Duration = Duration::from_millis(16);

pub fn run() -> anyhow::Result<()> {
    unsafe {
        CoInitializeEx(None, COINIT_APARTMENTTHREADED).ok()?;
    }

    let mut config = Config::load_or_default();
    let _ = config.save();
    let window = OverlayWindow::create(&config)?;
    let mut renderer = D2DRenderer::new(window.hwnd())?;
    let mut engine = DanmakuEngine::new(config.danmaku.clone());
    let (command_tx, command_rx) = mpsc::channel::<AppCommand>();
    let (bili_tx, bili_rx) = mpsc::channel::<BilibiliEvent>();
    let _tray = TrayController::new(command_tx)?;
    let mut bili_client = BilibiliClient::new(bili_tx);

    if config.bilibili.room_id > 0 && !config.bilibili.cookie.is_empty() {
        bili_client.connect(config.bilibili.room_id, config.bilibili.cookie.clone());
    }

    let mut last_tick = Instant::now();

    loop {
        if pump_messages() {
            break;
        }

        while let Ok(command) = command_rx.try_recv() {
            if handle_command(command, &window, &mut renderer, &mut engine, &mut config, &mut bili_client)? {
                return Ok(());
            }
        }

        while let Ok(event) = bili_rx.try_recv() {
            handle_bili_event(event, &mut engine, window.hwnd());
        }

        let now = Instant::now();
        let dt = now.duration_since(last_tick).as_secs_f32().min(0.05);
        last_tick = now;

        let size = window.client_size();
        engine.update(dt, size.width as f32, size.height as f32);
        renderer.render(size, engine.active_items())?;

        std::thread::sleep(TARGET_FRAME);
    }

    Ok(())
}

fn handle_command(
    command: AppCommand,
    window: &OverlayWindow,
    renderer: &mut D2DRenderer,
    engine: &mut DanmakuEngine,
    config: &mut Config,
    bili_client: &mut BilibiliClient,
) -> anyhow::Result<bool> {
    match command {
        AppCommand::ToggleMode => {
            let new_mode = if config.mode == "edit" { "danmaku" } else { "edit" };
            apply_mode(window, renderer, config, new_mode)?;
        }
        AppCommand::SetMode(mode) => {
            let value = if mode == OverlayMode::Edit { "edit" } else { "danmaku" };
            apply_mode(window, renderer, config, value)?;
        }
        AppCommand::SetOpacity(value) => {
            config.danmaku.opacity = value.clamp(0.1, 1.0);
            window.set_opacity(config.danmaku.opacity, OverlayMode::from_config(&config.mode))?;
            config.save()?;
        }
        AppCommand::SetSpeed(value) => {
            config.danmaku.speed = value;
            engine.set_config(config.danmaku.clone());
            config.save()?;
        }
        AppCommand::SetFontSize(value) => {
            config.danmaku.font_size = value;
            engine.set_config(config.danmaku.clone());
            config.save()?;
        }
        AppCommand::ConnectDialog => {
            if bili_client.is_connected() {
                bili_client.disconnect();
            } else if let Some((room_id, cookie)) = show_connect_dialog(window.hwnd(), config.bilibili.room_id, &config.bilibili.cookie) {
                config.bilibili.room_id = room_id;
                config.bilibili.cookie = normalize_cookie(&cookie);
                config.save()?;
                bili_client.connect(config.bilibili.room_id, config.bilibili.cookie.clone());
            }
        }
        AppCommand::BlockKeywordsDialog => {
            let current = config.danmaku.blocked_keywords.join("\r\n");
            if let Some(raw) = show_multiline_dialog(window.hwnd(), "弹幕屏蔽关键字", "每行一个关键字，也可用逗号/分号分隔:", &current) {
                config.danmaku.blocked_keywords = normalize_keywords(&raw);
                engine.set_config(config.danmaku.clone());
                config.save()?;
            }
        }
        AppCommand::BlockUserIdsDialog => {
            let current = config.danmaku.blocked_user_ids.join("\r\n");
            if let Some(raw) = show_multiline_dialog(window.hwnd(), "ID弹幕屏蔽", "每行一个用户ID，也可用逗号/空格/分号分隔:", &current) {
                config.danmaku.blocked_user_ids = normalize_user_ids(&raw);
                engine.set_config(config.danmaku.clone());
                config.save()?;
            }
        }
        AppCommand::Exit => {
            config.window = window.window_config();
            let _ = config.save();
            bili_client.disconnect();
            return Ok(true);
        }
    }

    config.window = window.window_config();
    Ok(false)
}

fn apply_mode(window: &OverlayWindow, renderer: &mut D2DRenderer, config: &mut Config, mode: &str) -> anyhow::Result<()> {
    config.mode = mode.to_string();
    let mode = OverlayMode::from_config(&config.mode);
    window.set_mode(mode)?;
    window.set_opacity(config.danmaku.opacity, mode)?;
    renderer.set_edit_mode(mode == OverlayMode::Edit);
    config.save()?;
    Ok(())
}

fn handle_bili_event(event: BilibiliEvent, engine: &mut DanmakuEngine, owner: windows::Win32::Foundation::HWND) {
    match event {
        BilibiliEvent::Danmaku(message) => {
            let text = if message.username.trim().is_empty() {
                message.text
            } else {
                format!("{}: {}", message.username, message.text)
            };
            engine.enqueue_text(text, message.user_id, message.color);
        }
        BilibiliEvent::Status(status) => {
            println!("[Bili] {status}");
        }
        BilibiliEvent::Error(error) => {
            eprintln!("[Bili Error] {error}");
            show_error(owner, &error);
        }
    }
}

fn pump_messages() -> bool {
    unsafe {
        let mut msg = MSG::default();
        while PeekMessageW(&mut msg, None, 0, 0, PM_REMOVE).as_bool() {
            if msg.message == WM_QUIT {
                return true;
            }
            let _ = TranslateMessage(&msg);
            DispatchMessageW(&msg);
        }
    }
    false
}
