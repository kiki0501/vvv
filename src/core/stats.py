"""Token统计管理"""

import asyncio
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Any

from .constants import STATS_FILE, DAILY_STATS_FILE


class TokenStatsManager:
    """Token统计管理器"""
    
    CHARS_PER_TOKEN_EN = 4.0
    CHARS_PER_TOKEN_ZH = 1.5
    
    def __init__(self, filepath=STATS_FILE, daily_filepath=DAILY_STATS_FILE):
        self.filepath = filepath
        self.daily_filepath = daily_filepath
        self.stats = {"total_requests": 0, "total_tokens": 0, "prompt_tokens": 0, "completion_tokens": 0}
        self.daily_stats = {
            "date": "",
            "reset_time": "",
            "models": {}
        }
        self.lock = asyncio.Lock()
        # 当前请求的token计数（用于流式响应）
        self._current_prompt_tokens = 0
        self._current_completion_tokens = 0
        
        self.load_stats()
        self.load_daily_stats()
        
        # 初始化或检查每日统计是否需要重置
        self._check_and_reset_daily_stats()

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

    def load_daily_stats(self):
        try:
            with open(self.daily_filepath, 'r', encoding='utf-8') as f:
                self.daily_stats = json.load(f)
        except FileNotFoundError:
            self._reset_daily_stats_structure()
            self.save_daily_stats()
        except Exception as e:
            print(f"⚠️ 加载每日统计失败: {e}")
            self._reset_daily_stats_structure()

    def save_daily_stats(self):
        try:
            with open(self.daily_filepath, 'w', encoding='utf-8') as f:
                json.dump(self.daily_stats, f, indent=2)
        except Exception as e:
            print(f"⚠️ 保存每日统计失败: {e}")

    def _get_beijing_time(self) -> datetime:
        """获取北京时间"""
        utc_now = datetime.now(timezone.utc)
        beijing_tz = timezone(timedelta(hours=8))
        return utc_now.astimezone(beijing_tz)

    def _reset_daily_stats_structure(self):
        """重置每日统计结构"""
        now = self._get_beijing_time()
        # 计算下一个0点
        next_reset = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        
        self.daily_stats = {
            "date": now.strftime("%Y-%m-%d"),
            "reset_time": next_reset.isoformat(),
            "models": {}
        }

    def _check_and_reset_daily_stats(self):
        """检查并重置每日统计"""
        now = self._get_beijing_time()
        current_date = now.strftime("%Y-%m-%d")
        
        if self.daily_stats.get("date") != current_date:
            print(f"🔄 重置每日统计: {self.daily_stats.get('date')} -> {current_date}")
            self._reset_daily_stats_structure()
            self.save_daily_stats()

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

    async def update(self, prompt_tokens: int, completion_tokens: int, model: str = "unknown", success: bool = True):
        """更新统计数据"""
        async with self.lock:
            # 1. 更新总体统计
            self.stats["total_requests"] += 1
            self.stats["prompt_tokens"] += prompt_tokens
            self.stats["completion_tokens"] += completion_tokens
            self.stats["total_tokens"] += (prompt_tokens + completion_tokens)
            self.save_stats()
            
            # 2. 更新每日统计
            self._check_and_reset_daily_stats()
            
            if model not in self.daily_stats["models"]:
                self.daily_stats["models"][model] = {
                    "total_requests": 0,
                    "success_requests": 0,
                    "failed_requests": 0,
                    "total_tokens": 0,
                    "prompt_tokens": 0,
                    "completion_tokens": 0
                }
            
            model_stats = self.daily_stats["models"][model]
            model_stats["total_requests"] += 1
            if success:
                model_stats["success_requests"] += 1
            else:
                model_stats["failed_requests"] += 1
                
            model_stats["prompt_tokens"] += prompt_tokens
            model_stats["completion_tokens"] += completion_tokens
            model_stats["total_tokens"] += (prompt_tokens + completion_tokens)
            
            self.save_daily_stats()
    
    def set_current_request_tokens(self, prompt_tokens: int = 0, completion_tokens: int = 0):
        self._current_prompt_tokens = prompt_tokens
        self._current_completion_tokens = completion_tokens
    
    def get_current_usage(self) -> Dict[str, int]:
        return {
            "prompt_tokens": self._current_prompt_tokens,
            "completion_tokens": self._current_completion_tokens,
            "total_tokens": self._current_prompt_tokens + self._current_completion_tokens
        }
    
    def get_daily_stats(self) -> Dict[str, Any]:
        """获取每日统计数据"""
        self._check_and_reset_daily_stats()
        return self.daily_stats
        
    def get_total_stats(self) -> Dict[str, Any]:
        """获取总体统计数据"""
        return self.stats
    
    def print_summary(self):
        print(f"📊 累计统计: 请求={self.stats['total_requests']}, Token={self.stats['total_tokens']} (P:{self.stats['prompt_tokens']} C:{self.stats['completion_tokens']})")