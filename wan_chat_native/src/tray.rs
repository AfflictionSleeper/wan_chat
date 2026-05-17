use std::sync::mpsc::Sender;

use windows::core::{w, PCWSTR};
use windows::Win32::Foundation::{HINSTANCE, HWND, LPARAM, LRESULT, POINT, WPARAM};
use windows::Win32::System::LibraryLoader::GetModuleHandleW;
use windows::Win32::UI::Shell::{
    Shell_NotifyIconW, NIF_ICON, NIF_MESSAGE, NIF_TIP, NIM_ADD, NIM_DELETE,
    NOTIFYICONDATAW,
};
use windows::Win32::UI::WindowsAndMessaging::{
    AppendMenuW, CreatePopupMenu, CreateWindowExW, DefWindowProcW, DestroyMenu,
    DestroyWindow, GetCursorPos, GetWindowLongPtrW, LoadIconW, PostQuitMessage,
    RegisterClassW, SetForegroundWindow, SetWindowLongPtrW, TrackPopupMenu, HMENU,
    IDI_APPLICATION, MF_POPUP, MF_SEPARATOR, MF_STRING, TPM_LEFTALIGN,
    TPM_RIGHTBUTTON, WINDOW_EX_STYLE, WINDOW_STYLE,
    WM_APP, WM_COMMAND, WM_DESTROY, WM_RBUTTONUP, WNDCLASSW, GWLP_USERDATA,
};

use crate::overlay_window::OverlayMode;

const CLASS_NAME: PCWSTR = w!("WanChatNativeTray");
const TRAY_UID: u32 = 1;
const WM_TRAYICON: u32 = WM_APP + 1;

const ID_OPACITY_BASE: usize = 1100;
const ID_SPEED_BASE: usize = 1200;
const ID_FONT_BASE: usize = 1300;
const ID_BLOCK_KEYWORDS: usize = 2001;
const ID_BLOCK_UIDS: usize = 2002;
const ID_CONNECT: usize = 2003;
const ID_TOGGLE: usize = 2004;
const ID_MODE_DANMAKU: usize = 2005;
const ID_MODE_EDIT: usize = 2006;
const ID_EXIT: usize = 2007;

const OPACITIES: [f32; 19] = [0.1, 0.15, 0.2, 0.25, 0.3, 0.35, 0.4, 0.45, 0.5, 0.55, 0.6, 0.65, 0.7, 0.75, 0.8, 0.85, 0.9, 0.95, 1.0];
const SPEEDS: [(f32, &str); 9] = [
    (80.0, "很慢"),
    (120.0, "慢"),
    (160.0, "偏慢"),
    (200.0, "中"),
    (260.0, "偏快"),
    (320.0, "快"),
    (400.0, "很快"),
    (500.0, "极快"),
    (650.0, "超快"),
];
const FONT_SIZES: [f32; 20] = [8.0, 9.0, 10.0, 11.0, 12.0, 13.0, 14.0, 15.0, 16.0, 18.0, 20.0, 22.0, 24.0, 26.0, 28.0, 32.0, 36.0, 40.0, 48.0, 56.0];

#[derive(Debug, Clone)]
pub enum AppCommand {
    ToggleMode,
    SetMode(OverlayMode),
    SetOpacity(f32),
    SetSpeed(f32),
    SetFontSize(f32),
    ConnectDialog,
    BlockKeywordsDialog,
    BlockUserIdsDialog,
    Exit,
}

struct TrayState {
    tx: Sender<AppCommand>,
}

pub struct TrayController {
    hwnd: HWND,
}

impl TrayController {
    pub fn new(tx: Sender<AppCommand>) -> anyhow::Result<Self> {
        unsafe {
            let module = GetModuleHandleW(None)?;
            let instance = HINSTANCE(module.0);
            register_class(instance)?;
            let hwnd = CreateWindowExW(
                WINDOW_EX_STYLE(0),
                CLASS_NAME,
                w!("WanChat Native Tray"),
                WINDOW_STYLE(0),
                0,
                0,
                0,
                0,
                None,
                None,
                Some(instance),
                None,
            )?;

            let state = Box::new(TrayState { tx });
            SetWindowLongPtrW(hwnd, GWLP_USERDATA, Box::into_raw(state) as isize);
            add_icon(hwnd)?;
            Ok(Self { hwnd })
        }
    }
}

impl Drop for TrayController {
    fn drop(&mut self) {
        unsafe {
            let mut nid = notify_data(self.hwnd);
            let _ = Shell_NotifyIconW(NIM_DELETE, &mut nid);
            let state = GetWindowLongPtrW(self.hwnd, GWLP_USERDATA) as *mut TrayState;
            if !state.is_null() {
                drop(Box::from_raw(state));
                SetWindowLongPtrW(self.hwnd, GWLP_USERDATA, 0);
            }
            let _ = DestroyWindow(self.hwnd);
        }
    }
}

unsafe fn register_class(instance: HINSTANCE) -> windows::core::Result<()> {
    let class = WNDCLASSW {
        lpfnWndProc: Some(tray_proc),
        hInstance: instance,
        lpszClassName: CLASS_NAME,
        ..Default::default()
    };
    let _ = RegisterClassW(&class);
    Ok(())
}

unsafe fn add_icon(hwnd: HWND) -> windows::core::Result<()> {
    let mut nid = notify_data(hwnd);
    nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP;
    nid.uCallbackMessage = WM_TRAYICON;
    nid.hIcon = LoadIconW(None, IDI_APPLICATION)?;
    set_tip(&mut nid, "WanChat");
    Shell_NotifyIconW(NIM_ADD, &mut nid).ok()
}

unsafe fn notify_data(hwnd: HWND) -> NOTIFYICONDATAW {
    let mut nid = NOTIFYICONDATAW::default();
    nid.cbSize = std::mem::size_of::<NOTIFYICONDATAW>() as u32;
    nid.hWnd = hwnd;
    nid.uID = TRAY_UID;
    nid
}

fn set_tip(nid: &mut NOTIFYICONDATAW, tip: &str) {
    let wide = tip.encode_utf16().chain(std::iter::once(0)).collect::<Vec<_>>();
    let len = wide.len().min(nid.szTip.len());
    nid.szTip[..len].copy_from_slice(&wide[..len]);
}

extern "system" fn tray_proc(hwnd: HWND, msg: u32, wparam: WPARAM, lparam: LPARAM) -> LRESULT {
    unsafe {
        match msg {
            WM_TRAYICON if lparam.0 as u32 == WM_RBUTTONUP => {
                show_menu(hwnd);
                LRESULT(0)
            }
            WM_COMMAND => {
                let id = wparam.0 & 0xffff;
                if let Some(command) = command_from_id(id) {
                    if let Some(state) = tray_state(hwnd) {
                        let _ = state.tx.send(command);
                    }
                }
                LRESULT(0)
            }
            WM_DESTROY => {
                PostQuitMessage(0);
                LRESULT(0)
            }
            _ => DefWindowProcW(hwnd, msg, wparam, lparam),
        }
    }
}

unsafe fn show_menu(hwnd: HWND) {
    let menu = CreatePopupMenu().unwrap_or_default();
    let opacity_menu = CreatePopupMenu().unwrap_or_default();
    for (idx, value) in OPACITIES.iter().enumerate() {
        let label = format!("{}%", (value * 100.0) as i32);
        append_text(opacity_menu, ID_OPACITY_BASE + idx, &label);
    }

    let speed_menu = CreatePopupMenu().unwrap_or_default();
    for (idx, (_, label)) in SPEEDS.iter().enumerate() {
        append_text(speed_menu, ID_SPEED_BASE + idx, label);
    }

    let font_menu = CreatePopupMenu().unwrap_or_default();
    for (idx, size) in FONT_SIZES.iter().enumerate() {
        append_text(font_menu, ID_FONT_BASE + idx, &format!("{}px", *size as i32));
    }

    append_popup(menu, opacity_menu, "弹幕透明度");
    append_popup(menu, speed_menu, "弹幕速度");
    append_popup(menu, font_menu, "字体大小");
    append_text(menu, ID_BLOCK_KEYWORDS, "弹幕屏蔽关键字...");
    append_text(menu, ID_BLOCK_UIDS, "ID弹幕屏蔽...");
    append_text(menu, ID_CONNECT, "连接/断开B站直播间...");
    let _ = AppendMenuW(menu, MF_SEPARATOR, 0, PCWSTR::null());
    append_text(menu, ID_TOGGLE, "切换 编辑/穿透 模式");
    append_text(menu, ID_MODE_DANMAKU, "-> 穿透模式 (默认)");
    append_text(menu, ID_MODE_EDIT, "-> 编辑模式 (调整大小/位置)");
    let _ = AppendMenuW(menu, MF_SEPARATOR, 0, PCWSTR::null());
    append_text(menu, ID_EXIT, "退出");

    let mut pt = POINT::default();
    let _ = GetCursorPos(&mut pt);
    let _ = SetForegroundWindow(hwnd);
    let _ = TrackPopupMenu(menu, TPM_LEFTALIGN | TPM_RIGHTBUTTON, pt.x, pt.y, Some(0), hwnd, None);
    let _ = DestroyMenu(menu);
}

unsafe fn append_text(menu: HMENU, id: usize, label: &str) {
    let wide = wide_string(label);
    let _ = AppendMenuW(menu, MF_STRING, id, PCWSTR(wide.as_ptr()));
}

unsafe fn append_popup(menu: HMENU, submenu: HMENU, label: &str) {
    let wide = wide_string(label);
    let _ = AppendMenuW(menu, MF_POPUP, submenu.0 as usize, PCWSTR(wide.as_ptr()));
}

fn command_from_id(id: usize) -> Option<AppCommand> {
    if (ID_OPACITY_BASE..ID_OPACITY_BASE + OPACITIES.len()).contains(&id) {
        return Some(AppCommand::SetOpacity(OPACITIES[id - ID_OPACITY_BASE]));
    }
    if (ID_SPEED_BASE..ID_SPEED_BASE + SPEEDS.len()).contains(&id) {
        return Some(AppCommand::SetSpeed(SPEEDS[id - ID_SPEED_BASE].0));
    }
    if (ID_FONT_BASE..ID_FONT_BASE + FONT_SIZES.len()).contains(&id) {
        return Some(AppCommand::SetFontSize(FONT_SIZES[id - ID_FONT_BASE]));
    }

    match id {
        ID_BLOCK_KEYWORDS => Some(AppCommand::BlockKeywordsDialog),
        ID_BLOCK_UIDS => Some(AppCommand::BlockUserIdsDialog),
        ID_CONNECT => Some(AppCommand::ConnectDialog),
        ID_TOGGLE => Some(AppCommand::ToggleMode),
        ID_MODE_DANMAKU => Some(AppCommand::SetMode(OverlayMode::ClickThrough)),
        ID_MODE_EDIT => Some(AppCommand::SetMode(OverlayMode::Edit)),
        ID_EXIT => Some(AppCommand::Exit),
        _ => None,
    }
}

unsafe fn tray_state(hwnd: HWND) -> Option<&'static mut TrayState> {
    let ptr = GetWindowLongPtrW(hwnd, GWLP_USERDATA) as *mut TrayState;
    if ptr.is_null() { None } else { Some(&mut *ptr) }
}

fn wide_string(value: &str) -> Vec<u16> {
    value.encode_utf16().chain(std::iter::once(0)).collect()
}
