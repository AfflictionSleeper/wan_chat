use std::fs;
use std::path::PathBuf;

use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Config {
    pub window: WindowConfig,
    pub danmaku: DanmakuConfig,
    pub bilibili: BilibiliConfig,
    pub mode: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct WindowConfig {
    pub x: i32,
    pub y: i32,
    pub width: i32,
    pub height: i32,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DanmakuConfig {
    pub font_size: f32,
    pub speed: f32,
    pub opacity: f32,
    #[serde(default)]
    pub direction: DanmakuDirection,
    pub max_items: usize,
    pub render_budget: usize,
    pub pending_limit: usize,
    pub blocked_keywords: Vec<String>,
    pub blocked_user_ids: Vec<String>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum DanmakuDirection {
    Right,
    Left,
    Top,
    Bottom,
}

impl Default for DanmakuDirection {
    fn default() -> Self {
        Self::Right
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BilibiliConfig {
    pub room_id: u64,
    pub cookie: String,
}

impl Default for Config {
    fn default() -> Self {
        Self {
            window: WindowConfig { x: 200, y: 100, width: 1000, height: 420 },
            danmaku: DanmakuConfig {
                font_size: 26.0,
                speed: 200.0,
                opacity: 0.85,
                direction: DanmakuDirection::Right,
                max_items: 500,
                render_budget: 40,
                pending_limit: 3000,
                blocked_keywords: Vec::new(),
                blocked_user_ids: Vec::new(),
            },
            bilibili: BilibiliConfig { room_id: 0, cookie: String::new() },
            mode: "danmaku".to_string(),
        }
    }
}

impl Config {
    pub fn load_or_default() -> Self {
        let path = config_path();
        let Ok(raw) = fs::read_to_string(path) else {
            let mut config = Self::default();
            let _ = config.save();
            return config;
        };
        let mut config: Self = serde_json::from_str(&raw).unwrap_or_else(|_| Self::default());
        config.normalize();
        let _ = config.save();
        config
    }

    pub fn save(&mut self) -> anyhow::Result<()> {
        self.normalize();
        let raw = serde_json::to_string_pretty(self)?;
        fs::write(config_path(), raw)?;
        Ok(())
    }

    pub fn normalize(&mut self) {
        self.bilibili.cookie = normalize_cookie(&self.bilibili.cookie);
        self.danmaku.blocked_keywords = normalize_list(&self.danmaku.blocked_keywords.join("\n"), false);
        self.danmaku.blocked_user_ids = normalize_list(&self.danmaku.blocked_user_ids.join("\n"), true);
        self.danmaku.opacity = self.danmaku.opacity.clamp(0.1, 1.0);
        self.danmaku.font_size = self.danmaku.font_size.clamp(8.0, 96.0);
        self.danmaku.speed = self.danmaku.speed.clamp(40.0, 1000.0);
        self.danmaku.max_items = self.danmaku.max_items.clamp(20, 5000);
        self.danmaku.render_budget = self.danmaku.render_budget.clamp(1, 500);
        self.danmaku.pending_limit = self.danmaku.pending_limit.clamp(50, 20000);
    }
}

fn config_path() -> PathBuf {
    std::env::current_exe()
        .ok()
        .and_then(|p| p.parent().map(|d| d.join("config.json")))
        .unwrap_or_else(|| PathBuf::from("config.json"))
}

pub fn normalize_cookie(cookie: &str) -> String {
    cookie
        .replace('\r', "\n")
        .split('\n')
        .flat_map(|line| line.split(';'))
        .map(str::trim)
        .filter(|part| !part.is_empty())
        .collect::<Vec<_>>()
        .join("; ")
}

pub fn normalize_keywords(raw: &str) -> Vec<String> {
    normalize_list(raw, false)
}

pub fn normalize_user_ids(raw: &str) -> Vec<String> {
    normalize_list(raw, true)
}

fn normalize_list(raw: &str, split_whitespace: bool) -> Vec<String> {
    let mut values = Vec::new();
    let mut seen = Vec::new();
    let separators: &[char] = if split_whitespace {
        &['\n', '\r', ',', '，', ';', '；', ' ', '\t']
    } else {
        &['\n', '\r', ',', '，', ';', '；']
    };

    for piece in raw.split(separators) {
        let value = piece.trim();
        if value.is_empty() {
            continue;
        }
        let key = value.to_lowercase();
        if !seen.iter().any(|item| item == &key) {
            values.push(value.to_string());
            seen.push(key);
        }
    }

    values
}
