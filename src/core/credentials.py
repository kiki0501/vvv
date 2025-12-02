"""凭证管理 - 多凭证池版本"""

import asyncio
import json
import time
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, asdict

from .constants import CREDENTIALS_FILE


@dataclass
class CredentialSlot:
    """凭证槽位数据结构"""
    slot_id: int
    harvest: Optional[Dict[str, Any]]
    timestamp: float
    version: int
    status: str  # 'active', 'expired', 'invalid', 'empty'
    last_used: float
    use_count: int
    
    def to_dict(self):
        """转换为字典（用于序列化）"""
        return {
            'slot_id': self.slot_id,
            'harvest': self.harvest,
            'timestamp': self.timestamp,
            'version': self.version,
            'status': self.status,
            'last_used': self.last_used,
            'use_count': self.use_count
        }
    
    @classmethod
    def from_dict(cls, data: Dict):
        """从字典创建（用于反序列化）"""
        return cls(**data)
    
    def age_seconds(self) -> float:
        """获取凭证年龄（秒）"""
        return time.time() - self.timestamp
    
    def is_healthy(self, max_age: int = 180) -> bool:
        """检查凭证是否健康"""
        if self.status != 'active':
            return False
        if not self.harvest:
            return False
        return self.age_seconds() < max_age


class CredentialManager:
    """凭证管理器 - 支持多凭证池和主动健康检查"""
    
    def __init__(self, filepath=CREDENTIALS_FILE, pool_size=5):
        self.filepath = filepath
        self.pool_size = pool_size
        
        # 凭证池
        self.slots: List[CredentialSlot] = []
        for i in range(pool_size):
            self.slots.append(CredentialSlot(
                slot_id=i,
                harvest=None,
                timestamp=0,
                version=0,
                status='empty',
                last_used=0,
                use_count=0
            ))
        
        # 池管理
        self.current_slot = 0  # 下一个要替换的槽位
        self.active_slot = -1  # 当前使用的槽位
        self.pool_version = 0  # 池的全局版本号
        
        # 并发控制
        self.refresh_event = asyncio.Event()
        self.refresh_complete_event = asyncio.Event()
        self.refresh_lock = asyncio.Lock()
        self.refresh_event.set()
        self.refresh_complete_event.set()
        self._is_refreshing = False
        
        # 请求队列
        self.pending_request_queue: List[tuple] = []
        self.queue_lock = asyncio.Lock()
        
        # 加载已保存的凭证
        self.load_from_disk()
    
    def load_from_disk(self):
        """从磁盘加载凭证池"""
        try:
            with open(self.filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)
                
                # 兼容旧格式（单凭证）
                if 'harvest' in data and 'pool' not in data:
                    print("📂 检测到旧格式凭证，自动迁移...")
                    # 将旧凭证放入第一个槽位
                    self.slots[0] = CredentialSlot(
                        slot_id=0,
                        harvest=data.get('harvest'),
                        timestamp=data.get('timestamp', 0),
                        version=1,
                        status='active',
                        last_used=0,
                        use_count=0
                    )
                    self.active_slot = 0
                    self.current_slot = 1
                    self.pool_version = 1
                    print(f"✅ 已迁移到凭证池 (槽位 0)")
                    self.save_to_disk()  # 保存新格式
                
                # 新格式（凭证池）
                elif 'pool' in data:
                    pool_data = data['pool']
                    for slot_data in pool_data:
                        slot_id = slot_data['slot_id']
                        if 0 <= slot_id < self.pool_size:
                            self.slots[slot_id] = CredentialSlot.from_dict(slot_data)
                    
                    self.current_slot = data.get('current_slot', 0)
                    self.active_slot = data.get('active_slot', -1)
                    self.pool_version = data.get('pool_version', 0)
                    
                    active_count = sum(1 for s in self.slots if s.status == 'active')
                    print(f"📂 已加载凭证池: {active_count}/{self.pool_size} 个活跃凭证")
                
        except FileNotFoundError:
            print("📂 未找到已保存的凭证池")
        except Exception as e:
            print(f"⚠️ 加载凭证池失败: {e}")
    
    def save_to_disk(self):
        """保存凭证池到磁盘"""
        try:
            data = {
                'pool': [slot.to_dict() for slot in self.slots],
                'current_slot': self.current_slot,
                'active_slot': self.active_slot,
                'pool_version': self.pool_version,
                'timestamp': time.time()
            }
            
            with open(self.filepath, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)
            
            print(f"💾 凭证池已保存")
        except Exception as e:
            print(f"⚠️ 保存凭证池失败: {e}")
    
    def update(self, data: Dict[str, Any]):
        """更新凭证池（循环替换）"""
        slot_id = self.current_slot
        old_slot = self.slots[slot_id]
        
        # 更新槽位
        self.pool_version += 1
        self.slots[slot_id] = CredentialSlot(
            slot_id=slot_id,
            harvest=data,
            timestamp=time.time(),
            version=self.pool_version,
            status='active',
            last_used=0,
            use_count=0
        )
        
        # 更新活跃槽位为最新的
        self.active_slot = slot_id
        
        # 移动到下一个槽位
        self.current_slot = (self.current_slot + 1) % self.pool_size
        
        if old_slot.status != 'empty':
            print(f"🔄 替换槽位 {slot_id} (旧版本 v{old_slot.version})")
        else:
            print(f"🎯 捕获凭证到槽位 {slot_id}")
        
        print(f"✅ 凭证已更新 v{self.pool_version} @ {time.strftime('%H:%M:%S')}")
        print(f"   下一个替换槽位: {self.current_slot}")
        
        self.save_to_disk()
        self.refresh_event.set()
        
        # 通知所有等待队列中的请求
        asyncio.create_task(self._notify_pending_requests())
    
    def update_token(self, token: str):
        """更新活跃凭证的 token"""
        if self.active_slot >= 0:
            slot = self.slots[self.active_slot]
            if slot.harvest and 'headers' in slot.harvest:
                formatted_token = json.dumps([token])
                slot.harvest['headers']['X-Goog-First-Party-Reauth'] = formatted_token
                
                slot.timestamp = time.time()
                self.pool_version += 1
                slot.version = self.pool_version
                
                print(f"🔄 Token 已刷新 (槽位 {self.active_slot}, v{self.pool_version}) @ {time.strftime('%H:%M:%S')}")
                self.save_to_disk()
                self.refresh_event.set()
                
                # 通知等待队列
                asyncio.create_task(self._notify_pending_requests())
    
    def get_credentials(self) -> Optional[Dict[str, Any]]:
        """获取最佳可用凭证（优先最新且健康的）"""
        best_slot = self._get_best_slot()
        if best_slot:
            best_slot.last_used = time.time()
            best_slot.use_count += 1
            return best_slot.harvest
        return None
    
    def _get_best_slot(self) -> Optional[CredentialSlot]:
        """获取最佳凭证槽位"""
        # 1. 优先使用健康的凭证
        healthy_slots = [s for s in self.slots if s.is_healthy()]
        if healthy_slots:
            # 返回最新的健康凭证
            return max(healthy_slots, key=lambda s: s.timestamp)
        
        # 2. 如果没有健康凭证，尝试使用活跃但可能过期的凭证
        active_slots = [s for s in self.slots if s.status == 'active' and s.harvest]
        if active_slots:
            print("⚠️ 所有凭证都已过期，使用最新的凭证")
            return max(active_slots, key=lambda s: s.timestamp)
        
        # 3. 完全没有可用凭证
        return None
    
    def check_credential_health(self, max_age: int = 180) -> tuple[bool, str, Optional[CredentialSlot]]:
        """
        主动健康检查
        
        Returns:
            (is_healthy, reason, best_slot)
        """
        best_slot = self._get_best_slot()
        
        if not best_slot:
            return False, "no_credential", None
        
        if not best_slot.harvest:
            return False, "empty_harvest", None
        
        age = best_slot.age_seconds()
        if age > max_age:
            return False, f"expired_{int(age)}s", best_slot
        
        return True, "healthy", best_slot
    
    def mark_slot_expired(self, slot_id: int):
        """标记槽位为过期"""
        if 0 <= slot_id < self.pool_size:
            self.slots[slot_id].status = 'expired'
            print(f"⚠️ 槽位 {slot_id} 已标记为过期")
    
    def mark_slot_invalid(self, slot_id: int):
        """标记槽位为无效"""
        if 0 <= slot_id < self.pool_size:
            self.slots[slot_id].status = 'invalid'
            print(f"⚠️ 槽位 {slot_id} 已标记为无效")
    
    def get_pool_status(self) -> Dict[str, Any]:
        """获取凭证池状态（用于监控）"""
        return {
            'pool_size': self.pool_size,
            'current_slot': self.current_slot,
            'active_slot': self.active_slot,
            'pool_version': self.pool_version,
            'slots': [
                {
                    'slot_id': slot.slot_id,
                    'status': slot.status,
                    'timestamp': slot.timestamp,
                    'age_seconds': int(slot.age_seconds()) if slot.harvest else 0,
                    'version': slot.version,
                    'use_count': slot.use_count,
                    'last_used': slot.last_used,
                    'is_healthy': slot.is_healthy()
                }
                for slot in self.slots
            ],
            'queue_length': len(self.pending_request_queue),
            'is_refreshing': self._is_refreshing
        }
    
    async def wait_for_refresh(self, timeout=30):
        """等待凭证刷新完成（保留兼容性）"""
        request_id = id(asyncio.current_task())
        print(f"   ⏳ [请求 {request_id}] 等待凭证刷新...")
        
        start_time = time.time()
        old_version = self.pool_version
        
        async with self.refresh_lock:
            if not self._is_refreshing:
                self._is_refreshing = True
                self.refresh_event.clear()
                self.refresh_complete_event.clear()
        
        try:
            while time.time() - start_time < timeout:
                if self.pool_version > old_version:
                    elapsed = time.time() - start_time
                    print(f"   ✅ [请求 {request_id}] 检测到新凭证 (等待 {elapsed:.1f}秒)")
                    return True
                
                try:
                    await asyncio.wait_for(self.refresh_event.wait(), timeout=1.0)
                    if self.pool_version > old_version:
                        elapsed = time.time() - start_time
                        print(f"   ✅ [请求 {request_id}] 凭证已更新 (等待 {elapsed:.1f}秒)")
                        return True
                except asyncio.TimeoutError:
                    pass
            
            elapsed = time.time() - start_time
            print(f"   ⚠️ [请求 {request_id}] 凭证刷新超时 ({elapsed:.1f}秒)")
            return False
            
        finally:
            self._is_refreshing = False
    
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
    
    def force_reset(self) -> None:
        """强制重置刷新状态"""
        print("🔄 强制重置凭证刷新状态...")
        self._is_refreshing = False
        self.refresh_event.set()
        self.refresh_complete_event.set()
        print("   ✅ 刷新状态已重置")
    
    def mark_refresh_failed(self) -> None:
        """标记刷新失败，解除等待"""
        print("   ⚠️ 标记凭证刷新失败")
        self.refresh_event.set()
        self.refresh_complete_event.set()
        self._is_refreshing = False
    
    def is_expired(self, max_age: int = 180) -> bool:
        """检查是否有可用的健康凭证"""
        is_healthy, _, _ = self.check_credential_health(max_age)
        return not is_healthy
    
    def should_preemptive_refresh(self, threshold: int = 120) -> bool:
        """检查是否应该预刷新凭证"""
        best_slot = self._get_best_slot()
        if not best_slot or not best_slot.harvest:
            return True
        
        age = best_slot.age_seconds()
        max_age = 180  # 3分钟有效期
        remaining = max_age - age
        
        return remaining < threshold
    
    async def _notify_pending_requests(self):
        """通知所有等待队列中的请求"""
        async with self.queue_lock:
            count = len(self.pending_request_queue)
            if count > 0:
                print(f"📢 通知 {count} 个等待中的请求使用新凭证")
                for request_id, event in self.pending_request_queue:
                    event.set()
                self.pending_request_queue.clear()
    
    async def wait_for_credential_with_queue(self, request_id: str, timeout: int = 30) -> bool:
        """
        使用队列机制等待凭证更新
        
        Args:
            request_id: 请求标识符
            timeout: 超时时间（秒）
            
        Returns:
            是否成功获取新凭证
        """
        event = asyncio.Event()
        
        # 加入等待队列
        async with self.queue_lock:
            self.pending_request_queue.append((request_id, event))
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
                self.pending_request_queue = [
                    (rid, evt) for rid, evt in self.pending_request_queue 
                    if rid != request_id
                ]
    
    # 兼容性属性（保持向后兼容）
    @property
    def latest_harvest(self) -> Optional[Dict[str, Any]]:
        """兼容旧代码：返回最佳凭证"""
        return self.get_credentials()
    
    @property
    def last_updated(self) -> float:
        """兼容旧代码：返回最新凭证的时间戳"""
        best_slot = self._get_best_slot()
        return best_slot.timestamp if best_slot else 0
    
    @property
    def credential_version(self) -> int:
        """兼容旧代码：返回池版本号"""
        return self.pool_version