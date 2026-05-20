use windows::core::{w, PCWSTR};
use windows::Win32::Foundation::{HINSTANCE, HWND, LPARAM, LRESULT, WPARAM};
use windows::Win32::System::LibraryLoader::GetModuleHandleW;
use windows::Win32::UI::Input::KeyboardAndMouse::{EnableWindow, GetKeyState, SetFocus};
use windows::Win32::UI::WindowsAndMessaging::{
    CreateWindowExW, DefWindowProcW, DestroyWindow, DispatchMessageW,
    GetMessageW, GetWindowLongPtrW, GetWindowTextLengthW, GetWindowTextW, IsWindow,
    LoadCursorW, RegisterClassW, SendMessageW, SetForegroundWindow, SetWindowLongPtrW,
    SetWindowPos, SetWindowTextW, ShowWindow, TranslateMessage, BS_DEFPUSHBUTTON, CS_HREDRAW, CS_VREDRAW,
    ES_AUTOHSCROLL, ES_AUTOVSCROLL, ES_LEFT, ES_MULTILINE, GWLP_USERDATA,
    HMENU, HWND_TOPMOST, IDC_ARROW, MSG, SW_SHOW, SWP_NOMOVE,
    SWP_NOSIZE, SWP_SHOWWINDOW, WINDOW_EX_STYLE, WINDOW_STYLE, WM_CLOSE, WM_COMMAND, WM_DESTROY,
    WNDCLASSW, WM_KEYDOWN, WM_PASTE, WS_BORDER, WS_CAPTION, WS_CHILD, WS_EX_TOPMOST, WS_OVERLAPPED,
    WS_SYSMENU, WS_TABSTOP, WS_VISIBLE, WS_VSCROLL,
};

const CLASS_NAME: PCWSTR = w!("WanChatNativeInputDialog");
const ID_EDIT: usize = 1001;
const ID_ROOM_EDIT: usize = 1002;
const ID_COOKIE_EDIT: usize = 1003;
const ID_PASTE_COOKIE: usize = 1004;
const ID_OK: usize = 1;
const ID_CANCEL: usize = 2;
const EM_SETLIMITTEXT: u32 = 0x00C5;
const EM_SETSEL: u32 = 0x00B1;
const VK_CONTROL_KEY: i32 = 0x11;

struct DialogState {
    edits: Vec<HWND>,
    result: Option<Vec<String>>,
    done: bool,
}

pub fn show_connect_dialog(saved_room: u64, saved_cookie: &str) -> Option<(u64, String)> {
    let room_initial = if saved_room > 0 { saved_room.to_string() } else { String::new() };
    let values = show_connect_form(&room_initial, saved_cookie)?;
    let room_id = parse_room_id(values.first()?)?;
    let cookie = values.get(1).cloned().unwrap_or_default();
    Some((room_id, cookie))
}

pub fn show_multiline_dialog(owner: HWND, title: &str, label: &str, initial: &str) -> Option<String> {
    show_text_dialog(owner, title, label, initial, true, true)
}

fn show_text_dialog(owner: HWND, title: &str, label: &str, initial: &str, wide: bool, multiline: bool) -> Option<String> {
    unsafe {
        let module = GetModuleHandleW(None).ok()?;
        let instance = HINSTANCE(module.0);
        register_class(instance).ok()?;

        let width = if wide { 520 } else { 380 };
        let height = if multiline { 460 } else { 170 };
        let title_w = wide_string(title);
        let hwnd = CreateWindowExW(
            WS_EX_TOPMOST,
            CLASS_NAME,
            PCWSTR(title_w.as_ptr()),
            WS_OVERLAPPED | WS_CAPTION | WS_SYSMENU,
            320,
            220,
            width,
            height,
            Some(owner),
            None,
            Some(instance),
            None,
        ).ok()?;

        let mut state = Box::new(DialogState { edits: Vec::new(), result: None, done: false });
        let state_ptr = &mut *state as *mut DialogState;
        SetWindowLongPtrW(hwnd, GWLP_USERDATA, state_ptr as isize);

        let label_w = wide_string(label);
        let _label = CreateWindowExW(
            WINDOW_EX_STYLE(0),
            w!("STATIC"),
            PCWSTR(label_w.as_ptr()),
            WS_CHILD | WS_VISIBLE,
            14,
            12,
            width - 34,
            24,
            Some(hwnd),
            None,
            Some(instance),
            None,
        ).ok()?;

        let edit_style = if multiline {
            WINDOW_STYLE(WS_CHILD.0 | WS_VISIBLE.0 | WS_BORDER.0 | WS_VSCROLL.0 | ES_MULTILINE as u32 | ES_AUTOVSCROLL as u32 | ES_LEFT as u32)
        } else {
            WINDOW_STYLE(WS_CHILD.0 | WS_VISIBLE.0 | WS_BORDER.0 | ES_AUTOHSCROLL as u32 | ES_LEFT as u32)
        };
        let edit_h = if multiline { height - 140 } else { 26 };
        let edit = CreateWindowExW(
            WINDOW_EX_STYLE(0),
            w!("EDIT"),
            PCWSTR::null(),
            edit_style,
            14,
            40,
            width - 34,
            edit_h,
            Some(hwnd),
            Some(control_id(ID_EDIT)),
            Some(instance),
            None,
        ).ok()?;
        state.edits.push(edit);
        let initial_w = wide_string(initial);
        let _ = SetWindowTextW(edit, PCWSTR(initial_w.as_ptr()));

        let button_y = if multiline { height - 82 } else { 82 };
        let ok_w = wide_string("确定");
        let cancel_w = wide_string("取消");
        let _ok = CreateWindowExW(
            WINDOW_EX_STYLE(0),
            w!("BUTTON"),
            PCWSTR(ok_w.as_ptr()),
            WINDOW_STYLE(WS_CHILD.0 | WS_VISIBLE.0 | BS_DEFPUSHBUTTON as u32),
            width - 190,
            button_y,
            76,
            28,
            Some(hwnd),
            Some(control_id(ID_OK)),
            Some(instance),
            None,
        ).ok()?;
        let _cancel = CreateWindowExW(
            WINDOW_EX_STYLE(0),
            w!("BUTTON"),
            PCWSTR(cancel_w.as_ptr()),
            WS_CHILD | WS_VISIBLE,
            width - 104,
            button_y,
            76,
            28,
            Some(hwnd),
            Some(control_id(ID_CANCEL)),
            Some(instance),
            None,
        ).ok()?;

        let _ = EnableWindow(owner, false);
        let _ = ShowWindow(hwnd, SW_SHOW);
        let _ = SetWindowPos(hwnd, Some(HWND_TOPMOST), 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW);
        let _ = SetForegroundWindow(hwnd);

        let mut msg = MSG::default();
        while IsWindow(Some(hwnd)).as_bool() && !state.done && GetMessageW(&mut msg, None, 0, 0).as_bool() {
            if handle_dialog_shortcut(hwnd, &msg) {
                continue;
            }
            let _ = TranslateMessage(&msg);
            DispatchMessageW(&msg);
        }

        let _ = EnableWindow(owner, true);
        let _ = SetForegroundWindow(owner);
        let result = state.result.as_ref().and_then(|values| values.first().cloned());
        SetWindowLongPtrW(hwnd, GWLP_USERDATA, 0);
        result
    }
}

fn show_connect_form(room_initial: &str, cookie_initial: &str) -> Option<Vec<String>> {
    unsafe {
        let module = GetModuleHandleW(None).ok()?;
        let instance = HINSTANCE(module.0);
        register_class(instance).ok()?;

        let width = 620;
        let height = 320;
        let title_w = wide_string("连接B站直播间");
        let hwnd = CreateWindowExW(
            WS_EX_TOPMOST,
            CLASS_NAME,
            PCWSTR(title_w.as_ptr()),
            WS_OVERLAPPED | WS_CAPTION | WS_SYSMENU,
            320,
            220,
            width,
            height,
            None,
            None,
            Some(instance),
            None,
        ).ok()?;

        let mut state = Box::new(DialogState { edits: Vec::new(), result: None, done: false });
        let state_ptr = &mut *state as *mut DialogState;
        SetWindowLongPtrW(hwnd, GWLP_USERDATA, state_ptr as isize);

        create_static(hwnd, instance, "直播间房间号:", 14, 12, width - 34, 22)?;
        let room_edit = create_edit(hwnd, instance, ID_ROOM_EDIT, 14, 38, width - 34, 26, false)?;
        set_text(room_edit, room_initial);

        create_static(hwnd, instance, "Cookie（保存在本地 config.json，不会上传到其他地方）:", 14, 76, width - 34, 22)?;
        let cookie_edit = create_edit(hwnd, instance, ID_COOKIE_EDIT, 14, 102, width - 34, 104, true)?;
        let _ = SendMessageW(cookie_edit, EM_SETLIMITTEXT, Some(WPARAM(1024 * 1024)), Some(LPARAM(0)));
        set_text(cookie_edit, cookie_initial);

        state.edits.push(room_edit);
        state.edits.push(cookie_edit);

        let ok_w = wide_string("连接");
        let cancel_w = wide_string("取消");
        let paste_w = wide_string("粘贴Cookie");
        let _paste = CreateWindowExW(
            WINDOW_EX_STYLE(0),
            w!("BUTTON"),
            PCWSTR(paste_w.as_ptr()),
            WS_CHILD | WS_VISIBLE | WS_TABSTOP,
            14,
            224,
            108,
            28,
            Some(hwnd),
            Some(control_id(ID_PASTE_COOKIE)),
            Some(instance),
            None,
        ).ok()?;
        let _ok = CreateWindowExW(
            WINDOW_EX_STYLE(0),
            w!("BUTTON"),
            PCWSTR(ok_w.as_ptr()),
            WINDOW_STYLE(WS_CHILD.0 | WS_VISIBLE.0 | BS_DEFPUSHBUTTON as u32),
            width - 190,
            224,
            76,
            28,
            Some(hwnd),
            Some(control_id(ID_OK)),
            Some(instance),
            None,
        ).ok()?;
        let _cancel = CreateWindowExW(
            WINDOW_EX_STYLE(0),
            w!("BUTTON"),
            PCWSTR(cancel_w.as_ptr()),
            WS_CHILD | WS_VISIBLE,
            width - 104,
            224,
            76,
            28,
            Some(hwnd),
            Some(control_id(ID_CANCEL)),
            Some(instance),
            None,
        ).ok()?;

        let _ = ShowWindow(hwnd, SW_SHOW);
        let _ = SetWindowPos(hwnd, Some(HWND_TOPMOST), 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW);
        let _ = SetFocus(Some(room_edit));
        let _ = SetForegroundWindow(hwnd);

        let mut msg = MSG::default();
        while IsWindow(Some(hwnd)).as_bool() && !state.done && GetMessageW(&mut msg, None, 0, 0).as_bool() {
            if handle_dialog_shortcut(hwnd, &msg) {
                continue;
            }
            let _ = TranslateMessage(&msg);
            DispatchMessageW(&msg);
        }

        let result = state.result.clone();
        SetWindowLongPtrW(hwnd, GWLP_USERDATA, 0);
        result
    }
}

unsafe fn register_class(instance: HINSTANCE) -> windows::core::Result<()> {
    let class = WNDCLASSW {
        style: CS_HREDRAW | CS_VREDRAW,
        lpfnWndProc: Some(dialog_proc),
        hInstance: instance,
        hCursor: LoadCursorW(None, IDC_ARROW)?,
        lpszClassName: CLASS_NAME,
        ..Default::default()
    };
    let _ = RegisterClassW(&class);
    Ok(())
}

extern "system" fn dialog_proc(hwnd: HWND, msg: u32, wparam: WPARAM, lparam: LPARAM) -> LRESULT {
    unsafe {
        match msg {
            WM_COMMAND => {
                let id = wparam.0 & 0xffff;
                if id == ID_OK {
                    if let Some(state) = dialog_state(hwnd) {
                        state.result = Some(state.edits.iter().map(|edit| window_text(*edit)).collect());
                        state.done = true;
                    }
                    let _ = DestroyWindow(hwnd);
                    LRESULT(0)
                } else if id == ID_PASTE_COOKIE {
                    if let Some(state) = dialog_state(hwnd) {
                        if let Some(cookie_edit) = state.edits.get(1) {
                            let _ = SetFocus(Some(*cookie_edit));
                            let _ = SendMessageW(*cookie_edit, WM_PASTE, None, None);
                        }
                    }
                    LRESULT(0)
                } else if id == ID_CANCEL {
                    if let Some(state) = dialog_state(hwnd) {
                        state.done = true;
                    }
                    let _ = DestroyWindow(hwnd);
                    LRESULT(0)
                } else {
                    DefWindowProcW(hwnd, msg, wparam, lparam)
                }
            }
            WM_CLOSE => {
                if let Some(state) = dialog_state(hwnd) {
                    state.done = true;
                }
                let _ = DestroyWindow(hwnd);
                LRESULT(0)
            }
            WM_DESTROY => LRESULT(0),
            _ => DefWindowProcW(hwnd, msg, wparam, lparam),
        }
    }
}

unsafe fn dialog_state(hwnd: HWND) -> Option<&'static mut DialogState> {
    let ptr = GetWindowLongPtrW(hwnd, GWLP_USERDATA) as *mut DialogState;
    if ptr.is_null() { None } else { Some(&mut *ptr) }
}

unsafe fn window_text(hwnd: HWND) -> String {
    let len = GetWindowTextLengthW(hwnd).max(0) as usize;
    let mut buf = vec![0_u16; len + 1];
    let read = GetWindowTextW(hwnd, &mut buf) as usize;
    String::from_utf16_lossy(&buf[..read])
}

unsafe fn handle_dialog_shortcut(dialog: HWND, msg: &MSG) -> bool {
    if msg.message != WM_KEYDOWN || msg.wParam.0 != b'A' as usize || GetKeyState(VK_CONTROL_KEY) >= 0 {
        return false;
    }

    let Some(state) = dialog_state(dialog) else {
        return false;
    };
    if state.edits.iter().any(|edit| *edit == msg.hwnd) {
        let _ = SendMessageW(msg.hwnd, EM_SETSEL, Some(WPARAM(0)), Some(LPARAM(-1)));
        true
    } else {
        false
    }
}

unsafe fn create_static(hwnd: HWND, instance: HINSTANCE, text: &str, x: i32, y: i32, width: i32, height: i32) -> Option<HWND> {
    let text_w = wide_string(text);
    CreateWindowExW(
        WINDOW_EX_STYLE(0),
        w!("STATIC"),
        PCWSTR(text_w.as_ptr()),
        WS_CHILD | WS_VISIBLE,
        x,
        y,
        width,
        height,
        Some(hwnd),
        None,
        Some(instance),
        None,
    ).ok()
}

unsafe fn create_edit(hwnd: HWND, instance: HINSTANCE, id: usize, x: i32, y: i32, width: i32, height: i32, multiline: bool) -> Option<HWND> {
    let style = if multiline {
        WINDOW_STYLE(WS_CHILD.0 | WS_VISIBLE.0 | WS_TABSTOP.0 | WS_BORDER.0 | WS_VSCROLL.0 | ES_MULTILINE as u32 | ES_AUTOVSCROLL as u32 | ES_LEFT as u32)
    } else {
        WINDOW_STYLE(WS_CHILD.0 | WS_VISIBLE.0 | WS_TABSTOP.0 | WS_BORDER.0 | ES_AUTOHSCROLL as u32 | ES_LEFT as u32)
    };

    CreateWindowExW(
        WINDOW_EX_STYLE(0),
        w!("EDIT"),
        PCWSTR::null(),
        style,
        x,
        y,
        width,
        height,
        Some(hwnd),
        Some(control_id(id)),
        Some(instance),
        None,
    ).ok()
}

unsafe fn set_text(hwnd: HWND, text: &str) {
    let text_w = wide_string(text);
    let _ = SetWindowTextW(hwnd, PCWSTR(text_w.as_ptr()));
}

fn parse_room_id(raw: &str) -> Option<u64> {
    let trimmed = raw.trim().trim_end_matches('/');
    trimmed
        .rsplit('/')
        .next()
        .and_then(|value| value.parse::<u64>().ok())
}

fn wide_string(value: &str) -> Vec<u16> {
    value.encode_utf16().chain(std::iter::once(0)).collect()
}

fn control_id(id: usize) -> HMENU {
    HMENU(id as *mut std::ffi::c_void)
}
