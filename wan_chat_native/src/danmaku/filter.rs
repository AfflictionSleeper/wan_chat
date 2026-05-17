use std::collections::HashSet;

use crate::config::DanmakuConfig;

pub struct DanmakuFilter {
    blocked_keywords: Vec<String>,
    blocked_user_ids: HashSet<String>,
}

impl DanmakuFilter {
    pub fn new(config: &DanmakuConfig) -> Self {
        Self {
            blocked_keywords: config
                .blocked_keywords
                .iter()
                .map(|value| value.to_lowercase())
                .filter(|value| !value.is_empty())
                .collect(),
            blocked_user_ids: config
                .blocked_user_ids
                .iter()
                .filter(|value| !value.is_empty())
                .cloned()
                .collect(),
        }
    }

    pub fn allows(&self, text: &str, user_id: Option<&str>) -> bool {
        if let Some(user_id) = user_id {
            if self.blocked_user_ids.contains(user_id) {
                return false;
            }
        }

        let lower = text.to_lowercase();
        !self.blocked_keywords.iter().any(|keyword| lower.contains(keyword))
    }
}
