"""
定时刷新调度模块

管理凭证的自动刷新，确保 reCAPTCHA token 不过期。
"""

import asyncio
import time
from typing import Optional, Callable, Awaitable


class RefreshScheduler:
    """凭证刷新调度器"""
    
    def __init__(
        self,
        refresh_interval: int = 180,
        on_refresh: Optional[Callable[[], Awaitable[bool]]] = None
    ):
        """
        Args:
            refresh_interval: 刷新间隔 (秒)，默认 3 分钟
            on_refresh: 刷新回调函数 (异步)
        """
        self.refresh_interval = refresh_interval
        self.on_refresh = on_refresh
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._last_refresh: float = 0
        self._refresh_count: int = 0
    
    async def start(self) -> None:
        """启动调度器"""
        if self._running:
            print("⚠️ 调度器已在运行")
            return
        
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        print(f"⏰ 刷新调度器已启动 (间隔: {self.refresh_interval}秒)")
    
    async def stop(self) -> None:
        """停止调度器"""
        self._running = False
        
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        
        print("⏰ 刷新调度器已停止")
    
    async def _run_loop(self) -> None:
        """主调度循环"""
        while self._running:
            try:
                # 等待刷新间隔
                await asyncio.sleep(self.refresh_interval)
                
                if not self._running:
                    break
                
                # 执行刷新
                await self._do_refresh()
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"⚠️ 调度器循环出错: {e}")
                await asyncio.sleep(10)  # 出错后等待一会再重试
    
    async def _do_refresh(self) -> bool:
        """执行刷新"""
        if not self.on_refresh:
            return False
        
        self._refresh_count += 1
        print(f"🔄 开始自动刷新 #{self._refresh_count}")
        
        try:
            success = await self.on_refresh()
            self._last_refresh = time.time()
            
            if success:
                print(f"✅ 自动刷新成功 @ {time.strftime('%H:%M:%S')}")
            else:
                print(f"⚠️ 自动刷新失败")
            
            return success
            
        except Exception as e:
            print(f"❌ 自动刷新出错: {e}")
            return False
    
    async def trigger_refresh(self) -> bool:
        """手动触发刷新"""
        print("🔄 手动触发刷新...")
        return await self._do_refresh()
    
    @property
    def is_running(self) -> bool:
        return self._running
    
    @property
    def last_refresh(self) -> float:
        return self._last_refresh
    
    @property
    def refresh_count(self) -> int:
        return self._refresh_count
    
    @property
    def time_until_next_refresh(self) -> int:
        """距离下次刷新的秒数"""
        if not self._running or self._last_refresh == 0:
            return 0
        elapsed = time.time() - self._last_refresh
        remaining = self.refresh_interval - elapsed
        return max(0, int(remaining))