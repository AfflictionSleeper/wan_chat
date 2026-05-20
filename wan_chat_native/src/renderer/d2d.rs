use std::collections::HashMap;

use windows::core::w;
use windows::Win32::Foundation::HWND;
use windows::Win32::Graphics::Direct2D::Common::{D2D1_ALPHA_MODE_PREMULTIPLIED, D2D1_COLOR_F, D2D1_PIXEL_FORMAT, D2D_RECT_F};
use windows::Win32::Graphics::Direct2D::{
    D2D1CreateFactory, ID2D1Factory, ID2D1HwndRenderTarget, ID2D1SolidColorBrush,
    D2D1_FACTORY_TYPE_SINGLE_THREADED, D2D1_FEATURE_LEVEL_DEFAULT,
    D2D1_HWND_RENDER_TARGET_PROPERTIES, D2D1_PRESENT_OPTIONS_NONE,
    D2D1_RENDER_TARGET_PROPERTIES, D2D1_RENDER_TARGET_TYPE_DEFAULT,
    D2D1_RENDER_TARGET_USAGE_NONE,
};
use windows::Win32::Graphics::DirectWrite::{
    DWriteCreateFactory, IDWriteFactory, IDWriteTextFormat, DWRITE_FACTORY_TYPE_SHARED,
    DWRITE_FONT_STRETCH_NORMAL, DWRITE_FONT_STYLE_NORMAL, DWRITE_FONT_WEIGHT_NORMAL,
    DWRITE_MEASURING_MODE_NATURAL, DWRITE_PARAGRAPH_ALIGNMENT_NEAR,
    DWRITE_TEXT_ALIGNMENT_LEADING,
};
use windows::Win32::Graphics::Dxgi::Common::DXGI_FORMAT_UNKNOWN;

use crate::danmaku::item::{DanmakuItem, TextColor};
use crate::overlay_window::ClientSize;

const TRANSPARENT_COLOR: D2D1_COLOR_F = D2D1_COLOR_F { r: 1.0 / 255.0, g: 1.0 / 255.0, b: 1.0 / 255.0, a: 1.0 };

pub struct D2DRenderer {
    _hwnd: HWND,
    _factory: ID2D1Factory,
    write_factory: IDWriteFactory,
    target: ID2D1HwndRenderTarget,
    text_brush: ID2D1SolidColorBrush,
    outline_brush: ID2D1SolidColorBrush,
    border_brush: ID2D1SolidColorBrush,
    formats: HashMap<u32, IDWriteTextFormat>,
    edit_mode: bool,
}

impl D2DRenderer {
    pub fn new(hwnd: HWND) -> anyhow::Result<Self> {
        unsafe {
            let factory: ID2D1Factory = D2D1CreateFactory(D2D1_FACTORY_TYPE_SINGLE_THREADED, None)?;
            let write_factory: IDWriteFactory = DWriteCreateFactory(DWRITE_FACTORY_TYPE_SHARED)?;
            let target = create_hwnd_target(&factory, hwnd, ClientSize { width: 1, height: 1 })?;
            let white = color(TextColor::WHITE, 1.0);
            let black = color(TextColor::BLACK, 0.9);
            let green = D2D1_COLOR_F { r: 0.0, g: 1.0, b: 0.0, a: 1.0 };
            let text_brush = target.CreateSolidColorBrush(&white as *const _, None)?;
            let outline_brush = target.CreateSolidColorBrush(&black as *const _, None)?;
            let border_brush = target.CreateSolidColorBrush(&green as *const _, None)?;

            Ok(Self { _hwnd: hwnd, _factory: factory, write_factory, target, text_brush, outline_brush, border_brush, formats: HashMap::new(), edit_mode: false })
        }
    }

    pub fn set_edit_mode(&mut self, edit_mode: bool) {
        self.edit_mode = edit_mode;
    }

    pub fn render(&mut self, size: ClientSize, items: &[DanmakuItem]) -> anyhow::Result<()> {
        unsafe {
            let pixel_size = self.target.GetPixelSize();
            if pixel_size.width != size.width || pixel_size.height != size.height {
                let new_size = windows::Win32::Graphics::Direct2D::Common::D2D_SIZE_U {
                    width: size.width,
                    height: size.height,
                };
                self.target.Resize(&new_size as *const _)?;
            }

            self.target.BeginDraw();
            let bg = if self.edit_mode {
                D2D1_COLOR_F { r: 0.02, g: 0.02, b: 0.02, a: 0.70 }
            } else {
                TRANSPARENT_COLOR
            };
            self.target.Clear(Some(&bg as *const _));

            for item in items {
                self.draw_item(item)?;
            }

            if self.edit_mode {
                self.draw_edit_border(size);
            }

            self.target.EndDraw(None, None)?;
        }

        Ok(())
    }

    fn draw_item(&mut self, item: &DanmakuItem) -> anyhow::Result<()> {
        unsafe {
            let format = self.text_format(item.style.font_size)?;

            let text: Vec<u16> = item.text.encode_utf16().collect();

            let text_color = color(item.style.color, item.style.opacity);
            let outline_color = color(TextColor::BLACK, item.style.opacity);
            self.text_brush.SetColor(&text_color as *const _);
            self.outline_brush.SetColor(&outline_color as *const _);

            for (dx, dy) in [(-0.5_f32, 0.0_f32), (0.5, 0.0), (0.0, 0.5)] {
                let rect = rect_for(item, dx, dy);
                self.target.DrawText(
                    &text,
                    &format,
                    &rect as *const _,
                    &self.outline_brush,
                    windows::Win32::Graphics::Direct2D::D2D1_DRAW_TEXT_OPTIONS_NONE,
                    DWRITE_MEASURING_MODE_NATURAL,
                );
            }

            let rect = rect_for(item, 0.0, 0.0);
            self.target.DrawText(
                &text,
                &format,
                &rect as *const _,
                &self.text_brush,
                windows::Win32::Graphics::Direct2D::D2D1_DRAW_TEXT_OPTIONS_NONE,
                DWRITE_MEASURING_MODE_NATURAL,
            );
        }

        Ok(())
    }

    fn text_format(&mut self, font_size: f32) -> anyhow::Result<IDWriteTextFormat> {
        let key = font_size.to_bits();
        if let Some(format) = self.formats.get(&key) {
            return Ok(format.clone());
        }

        unsafe {
            let format = self.write_factory.CreateTextFormat(
                w!("Microsoft YaHei UI"),
                None,
                DWRITE_FONT_WEIGHT_NORMAL,
                DWRITE_FONT_STYLE_NORMAL,
                DWRITE_FONT_STRETCH_NORMAL,
                font_size,
                w!("zh-cn"),
            )?;
            format.SetTextAlignment(DWRITE_TEXT_ALIGNMENT_LEADING)?;
            format.SetParagraphAlignment(DWRITE_PARAGRAPH_ALIGNMENT_NEAR)?;
            self.formats.insert(key, format.clone());
            Ok(format)
        }
    }

    fn draw_edit_border(&self, size: ClientSize) {
        unsafe {
            let rect = D2D_RECT_F {
                left: 1.0,
                top: 1.0,
                right: size.width as f32 - 2.0,
                bottom: size.height as f32 - 2.0,
            };
            self.target.DrawRectangle(&rect as *const _, &self.border_brush, 2.0, None);
        }
    }
}

unsafe fn create_hwnd_target(factory: &ID2D1Factory, hwnd: HWND, size: ClientSize) -> windows::core::Result<ID2D1HwndRenderTarget> {
    let props = D2D1_RENDER_TARGET_PROPERTIES {
        r#type: D2D1_RENDER_TARGET_TYPE_DEFAULT,
        pixelFormat: D2D1_PIXEL_FORMAT { format: DXGI_FORMAT_UNKNOWN, alphaMode: D2D1_ALPHA_MODE_PREMULTIPLIED },
        dpiX: 0.0,
        dpiY: 0.0,
        usage: D2D1_RENDER_TARGET_USAGE_NONE,
        minLevel: D2D1_FEATURE_LEVEL_DEFAULT,
    };

    let hwnd_props = D2D1_HWND_RENDER_TARGET_PROPERTIES {
        hwnd,
        pixelSize: windows::Win32::Graphics::Direct2D::Common::D2D_SIZE_U { width: size.width, height: size.height },
        presentOptions: D2D1_PRESENT_OPTIONS_NONE,
    };

    factory.CreateHwndRenderTarget(&props as *const _, &hwnd_props as *const _)
}

fn rect_for(item: &DanmakuItem, dx: f32, dy: f32) -> D2D_RECT_F {
    D2D_RECT_F {
        left: item.x + dx,
        top: item.y + dy,
        right: item.x + item.width + 8.0 + dx,
        bottom: item.y + item.height + 8.0 + dy,
    }
}

fn color(value: TextColor, opacity: f32) -> D2D1_COLOR_F {
    D2D1_COLOR_F { r: value.r, g: value.g, b: value.b, a: opacity.clamp(0.0, 1.0) }
}
