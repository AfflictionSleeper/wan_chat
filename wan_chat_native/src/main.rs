#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod app;
mod bilibili;
mod config;
mod danmaku;
mod dialogs;
mod overlay_window;
mod renderer;
mod tray;

fn main() -> anyhow::Result<()> {
    app::run()
}
