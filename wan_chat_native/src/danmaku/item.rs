#[derive(Debug, Clone)]
pub struct DanmakuItem {
    pub text: String,
    pub x: f32,
    pub y: f32,
    pub width: f32,
    pub height: f32,
    pub speed: f32,
    pub style: TextStyle,
}

#[derive(Debug, Clone, Copy)]
pub struct TextStyle {
    pub color: TextColor,
    pub font_size: f32,
    pub opacity: f32,
}

#[derive(Debug, Clone, Copy)]
pub struct TextColor {
    pub r: f32,
    pub g: f32,
    pub b: f32,
}

impl TextColor {
    pub const WHITE: Self = Self { r: 1.0, g: 1.0, b: 1.0 };
    pub const BLACK: Self = Self { r: 0.0, g: 0.0, b: 0.0 };

    pub fn from_hex(value: &str) -> Option<Self> {
        let raw = value.trim().trim_start_matches('#');
        if raw.len() != 6 {
            return None;
        }

        let parsed = u32::from_str_radix(raw, 16).ok()?;
        Some(Self {
            r: ((parsed >> 16) & 0xff) as f32 / 255.0,
            g: ((parsed >> 8) & 0xff) as f32 / 255.0,
            b: (parsed & 0xff) as f32 / 255.0,
        })
    }
}

impl TextStyle {
    pub fn default_with(font_size: f32, opacity: f32) -> Self {
        Self { color: TextColor::WHITE, font_size, opacity }
    }
}
