use std::collections::VecDeque;

use crate::config::DanmakuConfig;

use super::filter::DanmakuFilter;
use super::item::{DanmakuItem, TextStyle};
use super::parser::parse_styled_text;

pub struct DanmakuEngine {
    config: DanmakuConfig,
    filter: DanmakuFilter,
    pending: VecDeque<DanmakuItem>,
    active: Vec<DanmakuItem>,
    spawn_delay: f32,
    rng_state: u64,
}

impl DanmakuEngine {
    pub fn new(config: DanmakuConfig) -> Self {
        Self {
            filter: DanmakuFilter::new(&config),
            config,
            pending: VecDeque::new(),
            active: Vec::new(),
            spawn_delay: 0.0,
            rng_state: random_seed(),
        }
    }

    pub fn enqueue_text(&mut self, raw_text: String, user_id: Option<String>, color: Option<String>) {
        if self.pending.len() >= self.config.pending_limit {
            let _ = self.pending.pop_front();
        }

        let base_style = TextStyle::default_with(self.config.font_size, self.config.opacity);
        let (text, mut style) = parse_styled_text(&raw_text, base_style);

        if let Some(color) = color.as_deref().and_then(super::item::TextColor::from_hex) {
            style.color = color;
        }

        if text.trim().is_empty() || !self.filter.allows(&text, user_id.as_deref()) {
            return;
        }

        let width = estimate_text_width(&text, style.font_size);
        self.pending.push_back(DanmakuItem {
            text,
            x: 0.0,
            y: 0.0,
            width,
            height: style.font_size * 1.35,
            speed: self.config.speed,
            style,
        });
    }

    pub fn update(&mut self, dt: f32, window_width: f32, window_height: f32) {
        for item in &mut self.active {
            item.x -= item.speed * dt;
        }

        self.active.retain(|item| item.x + item.width > 0.0);
        self.spawn_pending(dt, window_width, window_height);
    }

    pub fn active_items(&self) -> &[DanmakuItem] {
        &self.active
    }

    pub fn set_config(&mut self, config: DanmakuConfig) {
        self.filter = DanmakuFilter::new(&config);
        self.config = config;
        for item in &mut self.active {
            item.speed = self.config.speed;
        }
    }

    pub fn clear_all(&mut self) {
        self.pending.clear();
        self.active.clear();
    }

    fn spawn_pending(&mut self, dt: f32, window_width: f32, window_height: f32) {
        if self.pending.is_empty() || self.active.len() >= self.config.max_items {
            return;
        }

        self.spawn_delay -= dt;
        if self.spawn_delay > 0.0 {
            return;
        }

        let Some(mut item) = self.pending.pop_front() else {
            return;
        };

        if let Some((x, y)) = self.find_spawn_position(&item, window_width, window_height) {
            item.x = x;
            item.y = y;
            self.active.push(item);
            self.spawn_delay = self.next_spawn_delay();
        } else {
            self.pending.push_front(item);
            self.spawn_delay = self.rand_range(0.035, 0.075);
        }
    }

    fn find_spawn_position(&mut self, item: &DanmakuItem, window_width: f32, window_height: f32) -> Option<(f32, f32)> {
        let track_height = (item.height.max(self.config.font_size * 1.45) + 8.0).max(24.0);
        let track_count = ((window_height - 16.0).max(track_height) / track_height).floor().max(1.0) as usize;
        let start_track = self.rand_usize(track_count);
        let attempts = track_count.min(self.config.render_budget.max(1));

        for offset in 0..attempts {
            let track = (start_track + offset) % track_count;
            let y_jitter = self.rand_range(0.0, (track_height - item.height).max(0.0));
            let y = 8.0 + track as f32 * track_height + y_jitter;
            let x = window_width + self.rand_range(24.0, 120.0);

            if self.is_spawn_area_clear(x, y, item) {
                return Some((x, y));
            }
        }

        None
    }

    fn is_spawn_area_clear(&self, spawn_x: f32, spawn_y: f32, new_item: &DanmakuItem) -> bool {
        let safe_gap = (self.config.font_size * 2.0).max(48.0);
        let new_top = spawn_y;
        let new_bottom = spawn_y + new_item.height;

        !self.active.iter().any(|item| {
            let item_top = item.y;
            let item_bottom = item.y + item.height;
            let vertically_overlaps = new_top < item_bottom + 2.0 && new_bottom + 2.0 > item_top;
            vertically_overlaps && item.x + item.width + safe_gap > spawn_x
        })
    }

    fn next_spawn_delay(&mut self) -> f32 {
        if self.pending.len() > self.config.max_items / 2 {
            self.rand_range(0.018, 0.060)
        } else {
            self.rand_range(0.045, 0.140)
        }
    }

    fn rand_usize(&mut self, upper: usize) -> usize {
        if upper <= 1 {
            0
        } else {
            (self.next_random_u32() as usize) % upper
        }
    }

    fn rand_range(&mut self, min: f32, max: f32) -> f32 {
        min + (max - min) * self.rand_unit()
    }

    fn rand_unit(&mut self) -> f32 {
        self.next_random_u32() as f32 / u32::MAX as f32
    }

    fn next_random_u32(&mut self) -> u32 {
        let mut x = self.rng_state;
        x ^= x << 13;
        x ^= x >> 7;
        x ^= x << 17;
        self.rng_state = x;
        (x >> 32) as u32
    }
}

fn estimate_text_width(text: &str, font_size: f32) -> f32 {
    text.chars().map(|ch| if ch.is_ascii() { 0.58 } else { 1.0 }).sum::<f32>() * font_size
}

fn random_seed() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|value| value.as_nanos() as u64)
        .unwrap_or(0x9E37_79B9_7F4A_7C15)
}
