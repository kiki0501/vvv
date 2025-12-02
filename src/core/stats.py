"""Token统计管理"""

import asyncio
import json
from typing import Dict, List, Any

from .constants import STATS_FILE


class TokenStatsManager:
    """Token统计管理器"""
    
    CHARS_PER_TOKEN_EN = 4.0
    CHARS_PER_TOKEN_ZH = 1.5
    
    def __init__(self, filepath=STATS_FILE):
        self.filepath = filepath
        self.stats = {"total_requests": 0, "total_tokens": 0, "prompt_tokens": 0, "completion_tokens": 0}
        self.lock = asyncio.Lock()
        # 当前请求的token计数（用于流式响应）
        self._current_prompt_tokens = 0
        self._current_completion_tokens = 0
        self.load_stats()

    def load_stats(self):
        try:
            with open(self.filepath, 'r', encoding='utf-8') as f:
                self.stats = json.load(f)
        except FileNotFoundError:
            self.save_stats()
        except Exception as e:
            print(f"⚠️ 加载统计失败: {e}")

    def save_stats(self):
        try:
            with open(self.filepath, 'w', encoding='utf-8') as f:
                json.dump(self.stats, f, indent=2)
        except Exception as e:
            print(f"⚠️ 保存统计失败: {e}")

    def estimate_tokens(self, text: str) -> int:
        """估算token数量"""
        if not text:
            return 0
        
        chinese_chars = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
        non_chinese_chars = len(text) - chinese_chars
        
        chinese_tokens = chinese_chars / self.CHARS_PER_TOKEN_ZH
        non_chinese_tokens = non_chinese_chars / self.CHARS_PER_TOKEN_EN
        
        return max(1, int(chinese_tokens + non_chinese_tokens))
    
    def estimate_messages_tokens(self, messages: List[Dict]) -> int:
        """估算消息列表的token数"""
        total = 0
        for msg in messages:
            total += 4
            content = msg.get('content', '')
            if isinstance(content, str):
                total += self.estimate_tokens(content)
            elif isinstance(content, list):
                for part in content:
                    if part.get('type') == 'text':
                        total += self.estimate_tokens(part.get('text', ''))
                    elif part.get('type') == 'image_url':
                        total += 765
        return total

    async def update(self, prompt_tokens: int, completion_tokens: int):
        async with self.lock:
            self.stats["total_requests"] += 1
            self.stats["prompt_tokens"] += prompt_tokens
            self.stats["completion_tokens"] += completion_tokens
            self.stats["total_tokens"] += (prompt_tokens + completion_tokens)
            self.save_stats()
    
    def set_current_request_tokens(self, prompt_tokens: int = 0, completion_tokens: int = 0):
        self._current_prompt_tokens = prompt_tokens
        self._current_completion_tokens = completion_tokens
    
    def get_current_usage(self) -> Dict[str, int]:
        return {
            "prompt_tokens": self._current_prompt_tokens,
            "completion_tokens": self._current_completion_tokens,
            "total_tokens": self._current_prompt_tokens + self._current_completion_tokens
        }
    
    def print_summary(self):
        print(f"📊 累计统计: 请求={self.stats['total_requests']}, Token={self.stats['total_tokens']} (P:{self.stats['prompt_tokens']} C:{self.stats['completion_tokens']})")