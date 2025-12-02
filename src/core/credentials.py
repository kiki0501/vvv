"""凭证管理"""

import asyncio
import json
import time
from typing import Dict, Any, Optional

from .constants import CREDENTIALS_FILE


class CredentialManager:
    """凭证管理器，支持并发刷新和请求队列"""
    def __init__(self, filepath=CREDENTIALS_FILE):
        self.filepath = filepath
        self.latest_harvest: Optional[Dict[str, Any]] = None
        self.last_updated: float = 0
        self.credential_version: int = 0  # 凭证版本号
        self.refresh_event = asyncio.Event()
        self.refresh_complete_event = asyncio.Event()
        self.refresh_lock = asyncio.Lock()
        self.refresh_event.set()
        self.refresh_complete_event.set()
        self.pending_requests = 0
        self._is_refreshing = False
        self.pending_request_queue: list = []  # 等待队列
        self.queue_lock = asyncio.Lock()
        self.load_from_disk()

    def load_from_disk(self):
        try:
            with open(self.filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)
                self.latest_harvest = data.get('harvest')
                self.last_updated = data.get('timestamp', 0)
                print(f"📂 已加载凭证 (已存在: {int(time.time() - self.last_updated)}秒)")
        except FileNotFoundError:
            print("📂 未找到已保存的凭证")
        except Exception as e:
            print(f"⚠️ 加载凭证失败: {e}")

    def save_to_disk(self):
        try:
            with open(self.filepath, 'w', encoding='utf-8') as f:
                json.dump({
                    'harvest': self.latest_harvest,
                    'timestamp': self.last_updated
                }, f, indent=2)
            print(f"💾 凭证已保存")
        except Exception as e:
            print(f"⚠️ 保存凭证失败: {e}")

    def update(self, data: Dict[str, Any]):
        # 更新凭证（旧凭证会被Python垃圾回收器自动清理）
        if self.latest_harvest:
            print(f"🔄 替换旧凭证...")
        
        # 保存新凭证并递增版本号
        self.latest_harvest = data
        self.last_updated = time.time()
        self.credential_version += 1
        print(f"✅ 凭证已更新 v{self.credential_version} @ {time.strftime('%H:%M:%S')}")
        self.save_to_disk()
        self.refresh_event.set()
        
        # 立即通知所有等待队列中的请求
        asyncio.create_task(self._notify_pending_requests())

    def update_token(self, token: str):
        if self.latest_harvest and 'headers' in self.latest_harvest:
            formatted_token = json.dumps([token])
            self.latest_harvest['headers']['X-Goog-First-Party-Reauth'] = formatted_token
            
            self.last_updated = time.time()
            self.credential_version += 1
            print(f"🔄 Token 已刷新 v{self.credential_version} @ {time.strftime('%H:%M:%S')}")
            self.save_to_disk()
            self.refresh_event.set()
            
            # 通知等待队列
            asyncio.create_task(self._notify_pending_requests())

    async def wait_for_refresh(self, timeout=30):
        """
        等待凭证刷新完成
        
        使用时间戳检查 + 事件通知的混合机制：
        - 主动检查凭证时间戳是否更新
        - 使用事件通知加速检测
        - 不完全依赖事件，避免错过通知
        """
        self.pending_requests += 1
        request_id = id(asyncio.current_task())
        print(f"   ⏳ [请求 {request_id}] 等待凭证刷新... (队列: {self.pending_requests})")
        
        # 记录开始等待时的凭证时间戳
        start_time = time.time()
        old_timestamp = self.last_updated
        
        # 只有第一个等待者才清除事件
        async with self.refresh_lock:
            if not self._is_refreshing:
                self._is_refreshing = True
                self.refresh_event.clear()
                self.refresh_complete_event.clear()
                print(f"   🔍 [请求 {request_id}] 触发刷新，旧凭证时间戳: {old_timestamp}")
        
        try:
            # 轮询检查凭证是否已更新
            while time.time() - start_time < timeout:
                # 首先检查凭证时间戳是否已更新
                if self.last_updated > old_timestamp:
                    elapsed = time.time() - start_time
                    print(f"   ✅ [请求 {request_id}] 检测到新凭证 (等待 {elapsed:.1f}秒)")
                    print(f"      旧时间戳: {old_timestamp}, 新时间戳: {self.last_updated}")
                    return True
                
                # 等待事件通知（最多 1 秒），加速检测
                try:
                    await asyncio.wait_for(self.refresh_event.wait(), timeout=1.0)
                    # 事件被触发，立即检查凭证
                    if self.last_updated > old_timestamp:
                        elapsed = time.time() - start_time
                        print(f"   ✅ [请求 {request_id}] 事件通知收到，凭证已更新 (等待 {elapsed:.1f}秒)")
                        return True
                    else:
                        # 事件被触发但凭证未更新，可能是误触发，继续等待
                        # print(f"   ⚠️ [请求 {request_id}] 事件触发但凭证未更新，继续等待...")
                        pass
                except asyncio.TimeoutError:
                    # 1 秒超时，继续轮询
                    pass
            
            # 超时
            elapsed = time.time() - start_time
            print(f"   ⚠️ [请求 {request_id}] 凭证刷新超时 ({elapsed:.1f}秒)")
            return False
            
        finally:
            self.pending_requests -= 1
            if self.pending_requests == 0:
                self._is_refreshing = False
                print(f"   🏁 [请求 {request_id}] 最后一个等待者退出")

    async def wait_for_refresh_complete(self, timeout=30):
        """等待前端UI刷新完成"""
        try:
            print(f"   ⏳ 等待前端 UI 就绪...")
            await asyncio.wait_for(self.refresh_complete_event.wait(), timeout=timeout)
            print("   ✅ 前端 UI 已就绪")
            return True
        except asyncio.TimeoutError:
            print(f"   ⚠️ 前端 UI 超时 ({timeout}秒)")
            return False

    def get_credentials(self) -> Optional[Dict[str, Any]]:
        if not self.latest_harvest:
            return None
        if time.time() - self.last_updated > 1800:
            print("⚠️ 凭证可能已过期 (>30分钟)")
        return self.latest_harvest
    
    def force_reset(self) -> None:
        """
        强制重置刷新状态
        
        当刷新过程卡死时调用此方法恢复
        """
        print("🔄 强制重置凭证刷新状态...")
        self._is_refreshing = False
        self.pending_requests = 0
        self.refresh_event.set()
        self.refresh_complete_event.set()
        print("   ✅ 刷新状态已重置")
    
    def mark_refresh_failed(self) -> None:
        """
        标记刷新失败，解除等待
        
        当浏览器刷新失败时调用
        """
        print("   ⚠️ 标记凭证刷新失败")
        self.refresh_event.set()  # 解除等待
        self.refresh_complete_event.set()
        self._is_refreshing = False
    
    def is_expired(self, max_age: int = 180) -> bool:
        """
        检查凭证是否过期
        
        Args:
            max_age: 最大有效期（秒），默认3分钟
            
        Returns:
            是否过期
        """
        if not self.latest_harvest:
            return True
        return time.time() - self.last_updated > max_age
    
    def should_preemptive_refresh(self, threshold: int = 120) -> bool:
        """
        检查是否应该预刷新凭证
        
        在凭证即将过期前主动刷新，避免在请求进行中失效
        
        Args:
            threshold: 提前刷新的时间阈值（秒），默认120秒（2分钟）
            
        Returns:
            是否应该预刷新
        """
        if not self.latest_harvest:
            return True
        
        age = time.time() - self.last_updated
        max_age = 180  # 3分钟有效期
        remaining = max_age - age
        
        return remaining < threshold
    
    async def _notify_pending_requests(self):
        """
        通知所有等待队列中的请求
        
        当新凭证到达时，立即唤醒所有等待的请求，
        实现批量通知，减少延迟
        """
        async with self.queue_lock:
            count = len(self.pending_request_queue)
            if count > 0:
                print(f"📢 通知 {count} 个等待中的请求使用新凭证")
                # 唤醒所有等待的协程
                for event in self.pending_request_queue:
                    event.set()
                self.pending_request_queue.clear()
    
    async def wait_for_credential_with_queue(self, request_id: str, timeout: int = 30) -> bool:
        """
        使用队列机制等待凭证更新
        
        优势：
        1. 新凭证到达时立即通知（无轮询延迟）
        2. 支持批量唤醒多个等待请求
        3. 避免轮询开销，提升性能
        
        Args:
            request_id: 请求标识符
            timeout: 超时时间（秒）
            
        Returns:
            是否成功获取新凭证
        """
        event = asyncio.Event()
        
        # 加入等待队列
        async with self.queue_lock:
            self.pending_request_queue.append(event)
            queue_position = len(self.pending_request_queue)
            print(f"   📥 [请求 {request_id}] 加入等待队列 (位置: {queue_position})")
        
        try:
            # 等待通知或超时
            await asyncio.wait_for(event.wait(), timeout=timeout)
            print(f"   ✅ [请求 {request_id}] 收到凭证更新通知")
            return True
        except asyncio.TimeoutError:
            print(f"   ⏰ [请求 {request_id}] 等待超时 ({timeout}秒)")
            return False
        finally:
            # 清理队列（如果还在队列中）
            async with self.queue_lock:
                if event in self.pending_request_queue:
                    self.pending_request_queue.remove(event)