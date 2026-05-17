use crate::danmaku::item::{TextColor, TextStyle};

pub fn parse_styled_text(raw: &str, base_style: TextStyle) -> (String, TextStyle) {
    let mut style = base_style;
    let lower = raw.to_lowercase();

    if let Some(value) = attribute_value(&lower, "color") {
        if let Some(color) = TextColor::from_hex(&value) {
            style.color = color;
        }
    }

    if let Some(value) = attribute_value(&lower, "size") {
        if let Ok(size) = value.parse::<f32>() {
            style.font_size = normalize_font_size(size, base_style.font_size);
        }
    }

    if let Some(value) = css_property(&lower, "color") {
        if let Some(color) = TextColor::from_hex(&value) {
            style.color = color;
        }
    }

    if let Some(value) = css_property(&lower, "font-size") {
        let normalized = value.trim_end_matches("px").trim();
        if let Ok(size) = normalized.parse::<f32>() {
            style.font_size = normalize_font_size(size, base_style.font_size);
        }
    }

    (strip_html_tags(raw), style)
}

fn normalize_font_size(size: f32, fallback: f32) -> f32 {
    if (8.0..=96.0).contains(&size) {
        size
    } else {
        fallback
    }
}

fn attribute_value(raw: &str, name: &str) -> Option<String> {
    let marker = format!("{}=", name);
    let start = raw.find(&marker)? + marker.len();
    let tail = &raw[start..];
    let mut chars = tail.chars();
    let quote = chars.next()?;

    if quote == '\'' || quote == '"' {
        let end = tail[1..].find(quote)? + 1;
        Some(tail[1..end].trim().to_string())
    } else {
        Some(tail.split_whitespace().next().unwrap_or_default().trim_matches('>').to_string())
    }
}

fn css_property(raw: &str, name: &str) -> Option<String> {
    let marker = format!("{}:", name);
    let start = raw.find(&marker)? + marker.len();
    let tail = &raw[start..];
    Some(tail.split(';').next().unwrap_or_default().trim().to_string())
}

fn strip_html_tags(raw: &str) -> String {
    let mut output = String::with_capacity(raw.len());
    let mut inside_tag = false;

    for ch in raw.chars() {
        match ch {
            '<' => inside_tag = true,
            '>' => inside_tag = false,
            _ if !inside_tag => output.push(ch),
            _ => {}
        }
    }

    output
}
