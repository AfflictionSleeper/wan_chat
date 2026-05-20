use anyhow::Context;
use windows::core::{w, PCWSTR};
use std::sync::atomic::{AtomicBool, Ordering};

use windows::Win32::Foundation::{COLORREF, HINSTANCE, HWND, LPARAM, LRESULT, RECT, WPARAM};
use windows::Win32::System::LibraryLoader::GetModuleHandleW;
use windows::Win32::UI::WindowsAndMessaging::{
    CreateWindowExW, DefWindowProcW, DestroyWindow, GetClientRect, GetWindowLongW, LoadCursorW,
    GetWindowRect, PostQuitMessage, RegisterClassW, SetLayeredWindowAttributes,
    SetWindowLongW, SetWindowPos, CS_HREDRAW, CS_VREDRAW, GWL_EXSTYLE,
    HTBOTTOM, HTBOTTOMLEFT, HTBOTTOMRIGHT, HTCAPTION, HTCLIENT, HTLEFT, HTRIGHT,
    HTTOP, HTTOPLEFT, HTTOPRIGHT, HTTRANSPARENT, HWND_TOPMOST, IDC_ARROW,
    LWA_ALPHA, LWA_COLORKEY,
    SWP_FRAMECHANGED, SWP_NOMOVE, SWP_NOSIZE, SWP_SHOWWINDOW, WM_DESTROY,
    WM_NCHITTEST, WNDCLASSW, WS_EX_LAYERED,
    WS_EX_NOACTIVATE, WS_EX_TOOLWINDOW, WS_EX_TOPMOST, WS_EX_TRANSPARENT,
    WS_POPUP, WS_VISIBLE,
};

use crate::config::{Config, WindowConfig};

const CLASS_NAME: PCWSTR = w!("WanChatNativeOverlay");
const TRANSPARENT_COLOR: u32 = 0x00010101;
static CLICK_THROUGH: AtomicBool = AtomicBool::new(true);

#[derive(Debug, Clone, Copy)]
pub struct ClientSize {
    pub width: u32,
    pub height: u32,
}

pub struct OverlayWindow {
    hwnd: HWND,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum OverlayMode {
    Edit,
    ClickThrough,
}

impl OverlayMode {
    pub fn from_config(value: &str) -> Self {
        if value.eq_ignore_ascii_case("edit") {
            Self::Edit
        } else {
            Self::ClickThrough
        }
    }
}

impl OverlayWindow {
    pub fn create(config: &Config) -> anyhow::Result<Self> {
        unsafe {
            let module = GetModuleHandleW(None).context("GetModuleHandleW failed")?;
            let instance = HINSTANCE(module.0);
            register_class(instance)?;

            let hwnd = CreateWindowExW(
                WS_EX_LAYERED | WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_TRANSPARENT | WS_EX_NOACTIVATE,
                CLASS_NAME,
                w!("WanChat Native"),
                WS_POPUP | WS_VISIBLE,
                config.window.x,
                config.window.y,
                config.window.width,
                config.window.height,
                None,
                None,
                Some(instance),
                None,
            )?;

            SetLayeredWindowAttributes(hwnd, COLORREF(TRANSPARENT_COLOR), (config.danmaku.opacity * 255.0) as u8, LWA_COLORKEY | LWA_ALPHA)?;
            SetWindowPos(hwnd, Some(HWND_TOPMOST), 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW | SWP_FRAMECHANGED)?;

            let window = Self { hwnd };
            window.set_mode(OverlayMode::from_config(&config.mode))?;
            Ok(window)
        }
    }

    pub fn hwnd(&self) -> HWND {
        self.hwnd
    }

    pub fn client_size(&self) -> ClientSize {
        unsafe {
            let mut rect = RECT::default();
            let _ = GetClientRect(self.hwnd, &mut rect);
            ClientSize {
                width: (rect.right - rect.left).max(1) as u32,
                height: (rect.bottom - rect.top).max(1) as u32,
            }
        }
    }

    pub fn set_click_through(&self, enabled: bool) -> anyhow::Result<()> {
        CLICK_THROUGH.store(enabled, Ordering::Relaxed);
        unsafe {
            let mut style = GetWindowLongW(self.hwnd, GWL_EXSTYLE) as u32;
            if enabled {
                style |= WS_EX_TRANSPARENT.0 | WS_EX_NOACTIVATE.0;
            } else {
                style &= !WS_EX_TRANSPARENT.0;
                style &= !WS_EX_NOACTIVATE.0;
            }
            SetWindowLongW(self.hwnd, GWL_EXSTYLE, style as i32);
            SetWindowPos(self.hwnd, Some(HWND_TOPMOST), 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_FRAMECHANGED)?;
        }
        Ok(())
    }

    pub fn set_mode(&self, mode: OverlayMode) -> anyhow::Result<()> {
        self.set_click_through(mode == OverlayMode::ClickThrough)
    }

    pub fn set_opacity(&self, opacity: f32, mode: OverlayMode) -> anyhow::Result<()> {
        unsafe {
            let alpha = if mode == OverlayMode::Edit { 180 } else { (opacity.clamp(0.1, 1.0) * 255.0) as u8 };
            let flags = if mode == OverlayMode::Edit { LWA_ALPHA } else { LWA_COLORKEY | LWA_ALPHA };
            let color_key = if mode == OverlayMode::Edit { 0 } else { TRANSPARENT_COLOR };
            SetLayeredWindowAttributes(self.hwnd, COLORREF(color_key), alpha, flags)?;
        }
        Ok(())
    }

    pub fn window_config(&self) -> WindowConfig {
        unsafe {
            let mut rect = RECT::default();
            let _ = GetWindowRect(self.hwnd, &mut rect);
            WindowConfig {
                x: rect.left,
                y: rect.top,
                width: (rect.right - rect.left).max(1),
                height: (rect.bottom - rect.top).max(1),
            }
        }
    }
}

impl Drop for OverlayWindow {
    fn drop(&mut self) {
        unsafe {
            let _ = DestroyWindow(self.hwnd);
        }
    }
}

unsafe fn register_class(instance: HINSTANCE) -> anyhow::Result<()> {
    let class = WNDCLASSW {
        style: CS_HREDRAW | CS_VREDRAW,
        lpfnWndProc: Some(wnd_proc),
        hInstance: instance,
        hCursor: LoadCursorW(None, IDC_ARROW)?,
        lpszClassName: CLASS_NAME,
        ..Default::default()
    };

    let atom = RegisterClassW(&class);
    if atom == 0 {
        // It may already be registered in dev reload cases; keep going.
    }
    Ok(())
}

extern "system" fn wnd_proc(hwnd: HWND, msg: u32, wparam: WPARAM, lparam: LPARAM) -> LRESULT {
    unsafe {
        match msg {
            WM_NCHITTEST => {
                if CLICK_THROUGH.load(Ordering::Relaxed) {
                    LRESULT(HTTRANSPARENT as isize)
                } else {
                    edit_hit_test(hwnd, lparam)
                }
            }
            WM_DESTROY => {
                PostQuitMessage(0);
                LRESULT(0)
            }
            _ => DefWindowProcW(hwnd, msg, wparam, lparam),
        }
    }
}

unsafe fn edit_hit_test(hwnd: HWND, lparam: LPARAM) -> LRESULT {
    let x = ((lparam.0 & 0xffff) as i16) as i32;
    let y = (((lparam.0 >> 16) & 0xffff) as i16) as i32;
    let mut rect = RECT::default();
    if GetWindowRect(hwnd, &mut rect).is_err() {
        return LRESULT(HTCLIENT as isize);
    }

    let edge = 12;
    let left = x <= rect.left + edge;
    let right = x >= rect.right - edge;
    let top = y <= rect.top + edge;
    let bottom = y >= rect.bottom - edge;

    let value = match (left, right, top, bottom) {
        (true, _, true, _) => HTTOPLEFT,
        (_, true, true, _) => HTTOPRIGHT,
        (true, _, _, true) => HTBOTTOMLEFT,
        (_, true, _, true) => HTBOTTOMRIGHT,
        (true, _, _, _) => HTLEFT,
        (_, true, _, _) => HTRIGHT,
        (_, _, true, _) => HTTOP,
        (_, _, _, true) => HTBOTTOM,
        _ => HTCAPTION,
    };
    LRESULT(value as isize)
}
