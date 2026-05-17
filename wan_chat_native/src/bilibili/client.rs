use std::collections::BTreeMap;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{mpsc::Sender, Arc};
use std::thread::{self, JoinHandle};
use std::time::Duration;

use futures_util::{SinkExt, StreamExt};
use reqwest::header::{COOKIE, REFERER, USER_AGENT};
use serde_json::Value;
use tokio::time::interval;
use tokio_tungstenite::connect_async;
use tokio_tungstenite::tungstenite::client::IntoClientRequest;
use tokio_tungstenite::tungstenite::http::HeaderValue;
use tokio_tungstenite::tungstenite::Message;

use super::protocol::{
    create_auth_packet, create_heartbeat_packet, parse_danmaku_message, parse_online_count,
    parse_packets, DanmakuMessage, OP_AUTH_REPLY, OP_HEARTBEAT_REPLY, OP_MESSAGE,
};

const MIXIN_KEY_ENC_TAB: [usize; 64] = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49, 33, 9,
    42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0,
    1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52,
];

#[derive(Debug, Clone)]
pub enum BilibiliEvent {
    Danmaku { session_id: u64, message: DanmakuMessage },
    Status { session_id: u64, message: String },
    Error { session_id: u64, message: String },
}

impl BilibiliEvent {
    pub fn session_id(&self) -> u64 {
        match self {
            Self::Danmaku { session_id, .. } => *session_id,
            Self::Status { session_id, .. } => *session_id,
            Self::Error { session_id, .. } => *session_id,
        }
    }
}

pub struct BilibiliClient {
    tx: Sender<BilibiliEvent>,
    running: Arc<AtomicBool>,
    worker: Option<JoinHandle<()>>,
    session_id: u64,
}

impl BilibiliClient {
    pub fn new(tx: Sender<BilibiliEvent>) -> Self {
        Self { tx, running: Arc::new(AtomicBool::new(false)), worker: None, session_id: 0 }
    }

    pub fn connect(&mut self, room_id: u64, cookie: String) {
        self.disconnect();
        if cookie.trim().is_empty() {
            let _ = self.tx.send(BilibiliEvent::Error { session_id: self.session_id, message: "需要 B站 Cookie 才能连接".to_string() });
            return;
        }

        self.session_id = self.session_id.wrapping_add(1).max(1);
        let session_id = self.session_id;
        let running = Arc::new(AtomicBool::new(true));
        self.running = running.clone();
        let tx = self.tx.clone();
        self.worker = Some(thread::spawn(move || {
            let rt = match tokio::runtime::Runtime::new() {
                Ok(rt) => rt,
                Err(err) => {
                    send_error(&tx, session_id, format!("启动异步运行时失败: {err}"));
                    return;
                }
            };

            rt.block_on(async move {
                if let Err(err) = run_client(room_id, cookie, tx.clone(), running.clone(), session_id).await {
                    send_error(&tx, session_id, err.to_string());
                }
                running.store(false, Ordering::Relaxed);
                send_status(&tx, session_id, "已断开");
            });
        }));
    }

    pub fn disconnect(&mut self) {
        self.running.store(false, Ordering::Relaxed);
    }

    pub fn session_id(&self) -> u64 {
        self.session_id
    }

}

async fn run_client(room_id: u64, cookie: String, tx: Sender<BilibiliEvent>, running: Arc<AtomicBool>, session_id: u64) -> anyhow::Result<()> {
    let http = reqwest::Client::builder().timeout(Duration::from_secs(10)).build()?;
    send_status(&tx, session_id, "获取连接参数...");

    let real_room_id = resolve_room_id(&http, room_id).await.unwrap_or(room_id);
    if real_room_id != room_id {
        send_status(&tx, session_id, format!("房间 {room_id} -> 真实房间 {real_room_id}"));
    }

    if !running.load(Ordering::Relaxed) {
        return Ok(());
    }

    let danmu_info = get_danmu_info(&http, real_room_id, &cookie).await?;
    let token = danmu_info
        .get("token")
        .and_then(Value::as_str)
        .ok_or_else(|| anyhow::anyhow!("获取连接参数失败，请检查 Cookie 是否有效"))?;

    let host = danmu_info
        .get("host_list")
        .and_then(Value::as_array)
        .and_then(|items| items.first())
        .and_then(Value::as_object);
    let host_name = host
        .and_then(|obj| obj.get("host"))
        .and_then(Value::as_str)
        .unwrap_or("broadcastlv.chat.bilibili.com");
    let port = host
        .and_then(|obj| obj.get("wss_port"))
        .and_then(Value::as_u64)
        .unwrap_or(2245);
    let ws_url = format!("wss://{host_name}:{port}/sub");

    let (uid, buvid3) = parse_cookie(&cookie);
    send_status(&tx, session_id, format!("已登录 UID:{uid}"));

    if !running.load(Ordering::Relaxed) {
        return Ok(());
    }

    let mut request = ws_url.into_client_request()?;
    request.headers_mut().insert("Origin", HeaderValue::from_static("https://live.bilibili.com"));
    request.headers_mut().insert("Referer", HeaderValue::from_static("https://live.bilibili.com/"));
    request.headers_mut().insert("User-Agent", HeaderValue::from_static("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"));

    let (socket, _) = connect_async(request).await?;
    let (mut write, mut read) = socket.split();

    let mut auth_body = serde_json::json!({
        "uid": uid,
        "roomid": real_room_id,
        "protover": 3,
        "platform": "web",
        "type": 2,
        "key": token,
    });
    if !buvid3.is_empty() {
        auth_body["buvid"] = Value::String(buvid3);
    }
    let auth_raw = serde_json::to_vec(&auth_body)?;
    write.send(Message::Binary(create_auth_packet(&auth_raw).into())).await?;
    send_status(&tx, session_id, "已连接");

    let mut heartbeat = interval(Duration::from_secs(30));
    while running.load(Ordering::Relaxed) {
        tokio::select! {
            _ = heartbeat.tick() => {
                write.send(Message::Binary(create_heartbeat_packet().into())).await?;
            }
            message = read.next() => {
                let Some(message) = message else { break; };
                let message = message?;
                let data = match message {
                    Message::Binary(data) => data,
                    Message::Close(_) => break,
                    _ => continue,
                };

                for (op, body) in parse_packets(&data) {
                    match op {
                        OP_HEARTBEAT_REPLY => {
                            if let Some(online) = parse_online_count(&body) {
                                send_status(&tx, session_id, format!("已连接 | 人气 {online}"));
                            }
                        }
                        OP_AUTH_REPLY => {
                            if let Ok(value) = serde_json::from_slice::<Value>(&body) {
                                if value.get("code").and_then(Value::as_i64) == Some(0) {
                                    send_status(&tx, session_id, "已加入房间");
                                } else {
                                    send_error(&tx, session_id, format!("加房失败: {value}"));
                                }
                            }
                        }
                        OP_MESSAGE => {
                            if let Some(message) = parse_danmaku_message(&body) {
                                let _ = tx.send(BilibiliEvent::Danmaku { session_id, message });
                            }
                        }
                        _ => {}
                    }
                }
            }
        }
    }

    let _ = write.send(Message::Close(None)).await;
    Ok(())
}

fn send_status(tx: &Sender<BilibiliEvent>, session_id: u64, message: impl Into<String>) {
    let _ = tx.send(BilibiliEvent::Status { session_id, message: message.into() });
}

fn send_error(tx: &Sender<BilibiliEvent>, session_id: u64, message: impl Into<String>) {
    let _ = tx.send(BilibiliEvent::Error { session_id, message: message.into() });
}

async fn resolve_room_id(http: &reqwest::Client, room_id: u64) -> Option<u64> {
    let url = format!("https://api.live.bilibili.com/room/v1/Room/get_info?room_id={room_id}");
    let data: Value = http.get(url).header(USER_AGENT, user_agent()).send().await.ok()?.json().await.ok()?;
    if data.get("code").and_then(Value::as_i64) == Some(0) {
        data.get("data")?.get("room_id")?.as_u64()
    } else {
        None
    }
}

async fn get_danmu_info(http: &reqwest::Client, room_id: u64, cookie: &str) -> anyhow::Result<Value> {
    let (img_key, sub_key) = fetch_wbi_keys(http, cookie).await?;
    let params = enc_wbi(BTreeMap::from([("id".to_string(), room_id.to_string()), ("type".to_string(), "0".to_string())]), &img_key, &sub_key);
    let query = params
        .iter()
        .map(|(k, v)| format!("{}={}", urlencoding::encode(k), urlencoding::encode(v)))
        .collect::<Vec<_>>()
        .join("&");
    let url = format!("https://api.live.bilibili.com/xlive/web-room/v1/index/getDanmuInfo?{query}");
    let data: Value = http
        .get(url)
        .header(USER_AGENT, user_agent())
        .header(REFERER, "https://live.bilibili.com/")
        .header(COOKIE, cookie)
        .send()
        .await?
        .json()
        .await?;

    if data.get("code").and_then(Value::as_i64) == Some(0) {
        Ok(data.get("data").cloned().unwrap_or(Value::Null))
    } else {
        anyhow::bail!("getDanmuInfo 返回异常: {data}")
    }
}

async fn fetch_wbi_keys(http: &reqwest::Client, cookie: &str) -> anyhow::Result<(String, String)> {
    let data: Value = http
        .get("https://api.bilibili.com/x/web-interface/nav")
        .header(USER_AGENT, user_agent())
        .header(COOKIE, cookie)
        .send()
        .await?
        .json()
        .await?;

    let img_url = data.pointer("/data/wbi_img/img_url").and_then(Value::as_str).unwrap_or_default();
    let sub_url = data.pointer("/data/wbi_img/sub_url").and_then(Value::as_str).unwrap_or_default();
    let img_key = file_stem(img_url).ok_or_else(|| anyhow::anyhow!("获取 WBI img_key 失败"))?;
    let sub_key = file_stem(sub_url).ok_or_else(|| anyhow::anyhow!("获取 WBI sub_key 失败"))?;
    Ok((img_key, sub_key))
}

fn enc_wbi(mut params: BTreeMap<String, String>, img_key: &str, sub_key: &str) -> BTreeMap<String, String> {
    let mixin_source = format!("{img_key}{sub_key}");
    let mixin_key = MIXIN_KEY_ENC_TAB
        .iter()
        .filter_map(|&idx| mixin_source.as_bytes().get(idx).copied())
        .map(char::from)
        .take(32)
        .collect::<String>();
    params.insert("wts".to_string(), unix_timestamp().to_string());

    let filtered = params
        .iter()
        .map(|(key, value)| {
            let value = value.chars().filter(|ch| !"!'()*".contains(*ch)).collect::<String>();
            (key.clone(), value)
        })
        .collect::<BTreeMap<_, _>>();
    let query = filtered
        .iter()
        .map(|(key, value)| format!("{key}={value}"))
        .collect::<Vec<_>>()
        .join("&");
    let sign = format!("{:x}", md5::compute(format!("{query}{mixin_key}")));

    let mut signed = filtered;
    signed.insert("w_rid".to_string(), sign);
    signed
}

fn parse_cookie(cookie: &str) -> (u64, String) {
    let mut uid = 0;
    let mut buvid3 = String::new();

    for piece in cookie.split(';').map(str::trim) {
        if let Some(value) = piece.strip_prefix("DedeUserID=") {
            uid = value.parse().unwrap_or(0);
        } else if let Some(value) = piece.strip_prefix("buvid3=") {
            buvid3 = value.to_string();
        }
    }

    (uid, buvid3)
}

fn file_stem(url: &str) -> Option<String> {
    let name = url.rsplit('/').next()?.split('.').next()?;
    if name.is_empty() { None } else { Some(name.to_string()) }
}

fn unix_timestamp() -> i64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|value| value.as_secs() as i64)
        .unwrap_or_default()
}

fn user_agent() -> &'static str {
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}
