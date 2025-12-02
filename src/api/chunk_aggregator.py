"""块聚合器，对流式响应进行缓冲和聚合"""

import time
from typing import Dict, Any, AsyncGenerator


class ChunkAggregator:
    """
    输入缓冲稳定化，保证最小块大小和最大等待时间
    
    优化：确保在 JSON 边界处切分，避免破坏 base64 图像数据
    """
    
    def __init__(self, min_chunk_size: int = 256, max_buffer_time: float = 0.1):
        self.min_chunk_size = min_chunk_size
        self.max_buffer_time = max_buffer_time
        self.buffer = ""
        self.last_yield_time = time.time()
        self.total_input = 0
        self.total_output = 0
        self.chunks_aggregated = 0
    
    def _find_safe_split_point(self, data: str) -> int:
        """
        找到安全的切分点
        
        优先在换行符处切分（NDJSON 格式），避免在 JSON 对象中间切分
        """
        if not data:
            return 0
        
        # 从后往前找最后一个换行符
        last_newline = data.rfind('\n')
        if last_newline > 0:
            return last_newline + 1
        
        # 没有换行符，返回全部长度（保持完整）
        return len(data)
    
    async def aggregate(self, iterator: AsyncGenerator[str, None]) -> AsyncGenerator[str, None]:
        """聚合输入流，在 JSON 边界处安全切分"""
        async for chunk in iterator:
            self.total_input += len(chunk)
            self.buffer += chunk
            
            current_time = time.time()
            time_elapsed = current_time - self.last_yield_time
            
            should_yield = (
                len(self.buffer) >= self.min_chunk_size or  # 达到最小大小
                time_elapsed >= self.max_buffer_time        # 超过最大等待时间
            )
            
            if should_yield and self.buffer:
                # 找到安全切分点
                split_point = self._find_safe_split_point(self.buffer)
                
                if split_point > 0:
                    output = self.buffer[:split_point]
                    self.buffer = self.buffer[split_point:]
                    self.last_yield_time = current_time
                    self.total_output += len(output)
                    self.chunks_aggregated += 1
                    yield output
        
        # 最后刷新剩余数据
        if self.buffer:
            self.total_output += len(self.buffer)
            yield self.buffer
            self.buffer = ""
    
    def get_stats(self) -> Dict[str, Any]:
        return {
            "total_input": self.total_input,
            "total_output": self.total_output,
            "chunks_aggregated": self.chunks_aggregated,
            "buffer_remaining": len(self.buffer)
        }