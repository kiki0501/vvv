"""
凭证抓取模块

从无头浏览器拦截的请求中提取凭证信息。
"""

import json
import time
from typing import Dict, Any, Optional, Callable
from dataclasses import dataclass


@dataclass
class HarvestedCredentials:
    """抓取的凭证数据"""
    headers: Dict[str, str]
    cookies: str
    url: str
    body: Optional[Dict[str, Any]] = None
    timestamp: float = 0
    
    def __post_init__(self):
        if self.timestamp == 0:
            self.timestamp = time.time()
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "headers": self.headers,
            "cookies": self.cookies,
            "url": self.url,
            "body": self.body,
            "timestamp": self.timestamp
        }


class CredentialHarvester:
    """凭证抓取器"""
    
    # 目标请求 URL 特征
    TARGET_PATTERNS = [
        "batchGraphql",
        "StreamGenerateContent"
    ]
    
    # 需要提取的 Headers - 扩展版本，包含所有Google API认证需要的头
    IMPORTANT_HEADERS = [
        # 核心认证头
        "authorization",
        "x-goog-authuser",
        "x-goog-first-party-reauth",
        # 来源验证头
        "x-origin",
        "origin",
        "referer",
        "x-same-domain",
        # Cookie
        "cookie",
        # 其他可能需要的头
        "x-goog-request-params",
        "x-client-data",
        "user-agent",
        "sec-fetch-site",
        "sec-fetch-mode",
        "sec-fetch-dest",
    ]
    
    def __init__(self, on_credentials: Optional[Callable] = None):
        """
        Args:
            on_credentials: 获取到凭证时的回调函数
        """
        self.on_credentials = on_credentials
        self.last_credentials: Optional[HarvestedCredentials] = None
        self._capture_count = 0
    
    def is_target_request(self, url: str) -> bool:
        """检查是否为目标请求"""
        return any(pattern in url for pattern in self.TARGET_PATTERNS)
    
    async def handle_request(self, request) -> None:
        """
        处理拦截到的请求
        
        Args:
            request: Playwright Request 对象
        """
        url = request.url
        
        if not self.is_target_request(url):
            return
        
        try:
            # 提取 Headers
            all_headers = await request.all_headers()
            headers = {}
            
            for key in self.IMPORTANT_HEADERS:
                # Headers 可能是大小写不敏感的
                for h_key, h_value in all_headers.items():
                    if h_key.lower() == key.lower():
                        headers[h_key] = h_value
                        break
            
            # 提取 Cookie
            cookies = all_headers.get("cookie", "")
            
            # 提取请求体 (如果有)
            body = None
            post_data_str = ""
            try:
                post_data = request.post_data
                if post_data:
                    post_data_str = post_data
                    body = json.loads(post_data)
            except:
                pass
            
            # 关键修复：只捕获实际的生成内容请求，忽略UI状态请求
            # 参考油猴脚本的过滤逻辑
            CONTENT_KEYWORDS = ['StreamGenerateContent', 'generateContent', 'Predict', 'Image']
            is_content_request = any(kw in post_data_str for kw in CONTENT_KEYWORDS)
            
            if not is_content_request:
                # 这是UI状态请求，不捕获
                return
            
            # 创建凭证对象
            credentials = HarvestedCredentials(
                headers=headers,
                cookies=cookies,
                url=url,
                body=body
            )
            
            self.last_credentials = credentials
            self._capture_count += 1
            
            print(f"🎯 捕获凭证 #{self._capture_count}")
            print(f"   URL: {url[:80]}...")
            print(f"   Headers: {len(headers)} 个")
            
            # 调用回调
            if self.on_credentials:
                await self._call_callback(credentials)
                
        except Exception as e:
            print(f"⚠️ 处理请求时出错: {e}")
    
    async def _call_callback(self, credentials: HarvestedCredentials) -> None:
        """调用凭证回调"""
        try:
            result = self.on_credentials(credentials.to_dict())
            # 支持异步回调
            if hasattr(result, '__await__'):
                await result
        except Exception as e:
            print(f"⚠️ 凭证回调出错: {e}")
    
    def get_credentials(self) -> Optional[Dict[str, Any]]:
        """获取最新凭证"""
        if self.last_credentials:
            return self.last_credentials.to_dict()
        return None
    
    @property
    def capture_count(self) -> int:
        return self._capture_count