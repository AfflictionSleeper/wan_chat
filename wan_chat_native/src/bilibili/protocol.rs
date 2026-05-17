use std::io::Read;

use flate2::read::ZlibDecoder;
use serde_json::Value;

const HEADER_LENGTH: usize = 16;
const OP_HEARTBEAT: u32 = 2;
pub const OP_HEARTBEAT_REPLY: u32 = 3;
pub const OP_MESSAGE: u32 = 5;
pub const OP_AUTH: u32 = 7;
pub const OP_AUTH_REPLY: u32 = 8;
const VER_ZLIB: u16 = 2;
const VER_BROTLI: u16 = 3;

#[derive(Debug, Clone)]
pub struct DanmakuMessage {
    pub text: String,
    pub username: String,
    pub user_id: Option<String>,
    pub color: Option<String>,
}

pub fn create_auth_packet(body: &[u8]) -> Vec<u8> {
    create_packet(body, OP_AUTH, 1)
}

pub fn create_heartbeat_packet() -> Vec<u8> {
    create_packet(&[], OP_HEARTBEAT, 1)
}

pub fn create_packet(body: &[u8], op: u32, ver: u16) -> Vec<u8> {
    let mut packet = Vec::with_capacity(HEADER_LENGTH + body.len());
    packet.extend_from_slice(&((HEADER_LENGTH + body.len()) as u32).to_be_bytes());
    packet.extend_from_slice(&(HEADER_LENGTH as u16).to_be_bytes());
    packet.extend_from_slice(&ver.to_be_bytes());
    packet.extend_from_slice(&op.to_be_bytes());
    packet.extend_from_slice(&1_u32.to_be_bytes());
    packet.extend_from_slice(body);
    packet
}

pub fn parse_packets(data: &[u8]) -> Vec<(u32, Vec<u8>)> {
    let mut offset = 0;
    let mut packets = Vec::new();

    while offset + HEADER_LENGTH <= data.len() {
        let total = u32::from_be_bytes(data[offset..offset + 4].try_into().unwrap()) as usize;
        let hlen = u16::from_be_bytes(data[offset + 4..offset + 6].try_into().unwrap()) as usize;
        let ver = u16::from_be_bytes(data[offset + 6..offset + 8].try_into().unwrap());
        let op = u32::from_be_bytes(data[offset + 8..offset + 12].try_into().unwrap());

        if total <= hlen || offset + total > data.len() {
            break;
        }

        let body = &data[offset + hlen..offset + total];
        match ver {
            VER_BROTLI => {
                if let Some(decoded) = decompress_brotli(body) {
                    packets.extend(parse_packets(&decoded));
                }
            }
            VER_ZLIB => {
                if let Some(decoded) = decompress_zlib(body) {
                    packets.extend(parse_packets(&decoded));
                }
            }
            _ => packets.push((op, body.to_vec())),
        }

        offset += total;
    }

    packets
}

pub fn parse_danmaku_message(payload: &[u8]) -> Option<DanmakuMessage> {
    let value: Value = serde_json::from_slice(payload).ok()?;
    let mut cmd = value.get("cmd")?.as_str()?.to_string();
    if let Some(pos) = cmd.find(':') {
        cmd.truncate(pos);
    }
    if cmd != "DANMU_MSG" {
        return None;
    }

    let info = value.get("info")?.as_array()?;
    let text = info.get(1)?.as_str().unwrap_or_default().to_string();
    let username = extract_username(info);
    let user_id = extract_user_id(info);
    let color = info
        .get(0)
        .and_then(Value::as_array)
        .and_then(|arr| arr.get(3))
        .and_then(Value::as_u64)
        .map(decimal_color_to_hex);

    Some(DanmakuMessage { text, username, user_id, color })
}

pub fn parse_online_count(payload: &[u8]) -> Option<u32> {
    if payload.len() >= 4 {
        Some(u32::from_be_bytes(payload[0..4].try_into().ok()?))
    } else {
        None
    }
}

fn extract_username(info: &[Value]) -> String {
    if let Some(name) = info
        .get(2)
        .and_then(Value::as_array)
        .and_then(|arr| arr.get(1))
        .and_then(Value::as_str)
    {
        if !name.trim().is_empty() {
            return name.trim().to_string();
        }
    }

    if let Some(obj) = info.get(15).and_then(Value::as_object) {
        for key in ["uname", "nickname", "name", "username"] {
            if let Some(value) = obj.get(key).and_then(Value::as_str) {
                if !value.trim().is_empty() {
                    return value.trim().to_string();
                }
            }
        }
    }

    String::new()
}

fn extract_user_id(info: &[Value]) -> Option<String> {
    if let Some(uid) = info
        .get(2)
        .and_then(Value::as_array)
        .and_then(|arr| arr.get(0))
        .and_then(|value| value.as_u64().map(|n| n.to_string()).or_else(|| value.as_str().map(str::to_string)))
    {
        if !uid.trim().is_empty() {
            return Some(uid);
        }
    }

    if let Some(obj) = info.get(15).and_then(Value::as_object) {
        for key in ["uid", "mid", "user_id"] {
            if let Some(value) = obj.get(key) {
                if let Some(uid) = value.as_u64().map(|n| n.to_string()).or_else(|| value.as_str().map(str::to_string)) {
                    if !uid.trim().is_empty() {
                        return Some(uid);
                    }
                }
            }
        }
    }

    None
}

fn decimal_color_to_hex(decimal_color: u64) -> String {
    format!("#{:06X}", decimal_color & 0xFF_FFFF)
}

fn decompress_brotli(body: &[u8]) -> Option<Vec<u8>> {
    let mut decoder = brotli::Decompressor::new(body, 4096);
    let mut output = Vec::new();
    decoder.read_to_end(&mut output).ok()?;
    Some(output)
}

fn decompress_zlib(body: &[u8]) -> Option<Vec<u8>> {
    let mut decoder = ZlibDecoder::new(body);
    let mut output = Vec::new();
    decoder.read_to_end(&mut output).ok()?;
    Some(output)
}
