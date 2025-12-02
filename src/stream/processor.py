"""
流式响应处理器

处理 Vertex AI 流式响应并转换为 OpenAI SSE 格式。
支持内容去重、Diff块原子传输、心跳保活。
"""

import json
import time
import uuid
from typing import Dict, Any, Generator, Optional, List, AsyncGenerator
from threading import Lock

from .trackers import DiffState, PathIndexTracker, StreamBuffer
from .parsers import IncrementalJSONParser
from .diff_handler import DiffBlockHandler
from .sse_formatter import SSEFormatter


class AuthError(Exception):
    """认证错误"""
    pass


class StreamProcessor:
    """流式响应处理器，支持三层消抖和内容去重"""
    
    TAIL_BUFFER_SIZE = 512  # 尾部缓冲区大小，用于微重复裁剪
    
    def __init__(self, enable_heartbeat: bool = True, heartbeat_interval: float = 15.0):
        """
        初始化流处理器
        
        Args:
            enable_heartbeat: 是否启用心跳机制
            heartbeat_interval: 心跳间隔（秒）
        """
        self.enable_heartbeat = enable_heartbeat
        self.heartbeat_interval = heartbeat_interval
        self.debug_mode = False
        self._conversation_id = str(uuid.uuid4())
        self._lock = Lock()
        
        self.json_parser = IncrementalJSONParser()
        self.diff_handler = DiffBlockHandler()
        self.buffer = StreamBuffer()
        self.path_tracker = PathIndexTracker()
        self.sse_formatter = SSEFormatter(self._conversation_id)
        
        self._tail_buffer = ""
        self._tail_buffer_lock = Lock()
        self._role_sent = False
        self._actual_content_sent = False  # 用于判断是否可安全重试
        
        self._stats = {
            "chunks_processed": 0,
            "chunks_yielded": 0,
            "duplicates_filtered": 0,
            "diff_blocks_processed": 0,
            "prefix_trimmed_bytes": 0,
            "errors": 0
        }
        
        self._last_chunk_time = time.time()
        self._chunk_times = []
        self._chunk_sizes = []
        
    def enable_debug(self, enabled: bool = True):
        """启用调试模式"""
        self.debug_mode = enabled
    
    def _log_debug(self, message: str):
        """调试日志"""
        if self.debug_mode:
            print(f"[流处理] {message}")
    
    def _generate_chunk_id(self, sequence: int) -> str:
        """生成唯一的chunk ID"""
        return f"{self._conversation_id[:8]}-seq{sequence:06d}"
    
    def get_stats(self) -> Dict[str, Any]:
        """获取处理统计信息"""
        return {
            **self._stats,
            "buffer_stats": self.buffer.get_stats(),
            "tracker_stats": self.path_tracker.get_stats(),
            "parser_stats": self.json_parser.get_stats()
        }
    
    def has_actual_content_sent(self) -> bool:
        """检查是否已发送实际文本内容（用于重试判断）"""
        return self._actual_content_sent

    def _trim_duplicate_prefix(self, content: str) -> str:
        """裁剪与尾部缓冲区重叠的前缀"""
        with self._tail_buffer_lock:
            if not self._tail_buffer or not content:
                return content
            
            # 查找content前缀与tail_buffer后缀的最大重叠
            max_overlap = min(len(self._tail_buffer), len(content))
            overlap_len = 0
            
            for i in range(1, max_overlap + 1):
                # 检查 tail_buffer 的后 i 个字符是否等于 content 的前 i 个字符
                if self._tail_buffer[-i:] == content[:i]:
                    overlap_len = i
            
            if overlap_len > 0:
                self._stats["prefix_trimmed_bytes"] += overlap_len
                trimmed = content[overlap_len:]
                if self.debug_mode:
                    print(f"🔧 裁剪重复前缀: {overlap_len}字符")
                return trimmed
            
            return content
    
    def _update_tail_buffer(self, content: str):
        """更新尾部缓冲区"""
        with self._tail_buffer_lock:
            self._tail_buffer += content
            # 保持缓冲区不超过最大大小
            if len(self._tail_buffer) > self.TAIL_BUFFER_SIZE:
                self._tail_buffer = self._tail_buffer[-self.TAIL_BUFFER_SIZE:]

    def _yield_content(
        self,
        content: str,
        model: str,
        is_diff_block: bool = False,
        is_reasoning: bool = False
    ) -> Generator[str, None, None]:
        """输出内容块（带重复前缀裁剪）"""
        if not content:
            return
        
        if not self._role_sent:
            self._role_sent = True
            yield self.sse_formatter.create_initial_role_chunk(model)
            self.buffer.mark_yield()
        
        trimmed_content = self._trim_duplicate_prefix(content)
        if not trimmed_content:
            self._stats["duplicates_filtered"] += 1
            return
        
        sequence = self.buffer.increment_sequence()
        chunk_id = self._generate_chunk_id(sequence)
        
        if is_reasoning:
            openai_chunk = self.sse_formatter.create_openai_chunk(reasoning_content=trimmed_content, model=model)
        else:
            openai_chunk = self.sse_formatter.create_openai_chunk(content=trimmed_content, model=model)
        
        sse_event = self.sse_formatter.format_sse_event(data=openai_chunk)
        
        self._update_tail_buffer(trimmed_content)
        
        self.buffer.mark_yield()
        self.buffer.mark_content_sent(trimmed_content)
        self._stats["chunks_yielded"] += 1
        self._actual_content_sent = True
        
        if is_diff_block:
            self._stats["diff_blocks_processed"] += 1
        
        yield sse_event

    def _fix_base64_padding(self, b64_data: str) -> str:
        """
        修复 base64 填充
        
        base64 编码的数据长度必须是 4 的倍数，不足时需要用 '=' 填充。
        如果数据被截断或缺少填充，会导致解码错误和图像损坏。
        """
        if not b64_data:
            return b64_data
        
        # 移除可能存在的换行符和空格
        b64_data = b64_data.replace('\n', '').replace('\r', '').replace(' ', '')
        
        # 计算需要的填充
        missing_padding = len(b64_data) % 4
        if missing_padding:
            b64_data += '=' * (4 - missing_padding)
        
        return b64_data

    def _yield_content_raw(
        self,
        content: str,
        model: str
    ) -> Generator[str, None, None]:
        """
        输出原始内容块（不做重复前缀裁剪）
        
        用于图像等二进制数据，避免 base64 被错误裁剪导致图像损坏
        """
        if not content:
            return
        
        if not self._role_sent:
            self._role_sent = True
            yield self.sse_formatter.create_initial_role_chunk(model)
            self.buffer.mark_yield()
        
        sequence = self.buffer.increment_sequence()
        
        openai_chunk = self.sse_formatter.create_openai_chunk(content=content, model=model)
        sse_event = self.sse_formatter.format_sse_event(data=openai_chunk)
        
        # 图像数据不更新 tail_buffer，避免影响后续文本的去重
        # self._update_tail_buffer(content)  # 跳过
        
        self.buffer.mark_yield()
        self.buffer.mark_content_sent(content)
        self._stats["chunks_yielded"] += 1
        self._actual_content_sent = True
        
        yield sse_event

    def _extract_path_index(self, result: Dict[str, Any]) -> int:
        """从result中提取path索引，格式: {"path": [..., 索引]}"""
        path = result.get('path', [])
        if len(path) >= 3:
            try:
                return int(path[2])
            except (ValueError, TypeError):
                return -1
        return -1

    def process_vertex_response(
        self,
        data: Dict[str, Any],
        model: str = "vertex-ai-proxy"
    ) -> Generator[str, None, None]:
        """处理Vertex AI响应并转换为OpenAI SSE格式"""
        self._stats["chunks_processed"] += 1
        
        if not data:
            return
        
        # 检查错误
        if 'error' in data:
            self._log_debug(f"Vertex AI错误: {data['error']}")
            self._stats["errors"] += 1
            return
        
        results = data.get('results', [])
        if not results:
            return
        
        indexed_results = []
        for result in results:
            if not result:
                continue
            path_index = self._extract_path_index(result)
            indexed_results.append((path_index, result))
        
        # 按path索引排序（-1会排在最前面，这些是没有path的result）
        indexed_results.sort(key=lambda x: x[0] if x[0] >= 0 else float('inf'))
        
        for path_index, result in indexed_results:
            # 检查错误
            if 'errors' in result:
                for err in result['errors']:
                    msg = err.get('message', 'Unknown Error')
                    self._log_debug(f"API错误: {msg}")
                    self._stats["errors"] += 1
                    
                    # 抛出认证错误以便上层重试
                    if "Recaptcha" in msg or "token" in msg.lower() or "Authentication" in msg:
                        raise AuthError(f"Authentication failed: {msg}")
                continue
            
            result_data = result.get('data')
            if not result_data:
                continue
            
            candidates = result_data.get('candidates')
            if not candidates:
                continue
            
            for candidate in candidates:
                content_obj = candidate.get('content') or {}
                parts = content_obj.get('parts') or []
                
                # 严格按顺序处理 parts
                for part in parts:
                    # 1. 处理文本内容 (包括 thought)
                    text = part.get('text', '')
                    if text:
                        is_thought = part.get('thought', False)
                        
                        if path_index >= 0:
                            tracker_result = self.path_tracker.process_result(path_index, text, is_thought)
                            
                            if tracker_result:
                                _, delta_content, is_reasoning = tracker_result
                                if delta_content:
                                    yield from self._yield_content(delta_content, model, is_reasoning=is_reasoning)
                            else:
                                self._stats["duplicates_filtered"] += 1
                        else:
                            if is_thought:
                                yield from self._yield_content(text, model, is_reasoning=True)
                            else:
                                yield from self._yield_content(text, model, is_diff_block=False)
                    
                    inline_data = part.get('inlineData')
                    uri = part.get('uri')
                    
                    if inline_data:
                        mime_type = inline_data.get('mimeType')
                        b64_data = inline_data.get('data')
                        if mime_type and b64_data:
                            # 验证和修复 base64 数据
                            b64_data = self._fix_base64_padding(b64_data)
                            image_md = f"![Generated Image](data:{mime_type};base64,{b64_data})"
                            # 图像数据不做重复前缀裁剪，直接输出
                            yield from self._yield_content_raw(image_md, model)
                    elif uri:
                        image_md = f"![Generated Image]({uri})"
                        yield from self._yield_content_raw(image_md, model)
                
                finish_reason = candidate.get('finishReason')
                if finish_reason in ['STOP', 'MAX_TOKENS']:
                    if not self._role_sent:
                        self._role_sent = True
                        yield self.sse_formatter.create_initial_role_chunk(model)
                        self.buffer.mark_yield()
                    
                    sequence = self.buffer.increment_sequence()
                    chunk_id = self._generate_chunk_id(sequence)
                    
                    mapped_reason = self.sse_formatter.map_finish_reason(finish_reason)
                    
                    finish_chunk = self.sse_formatter.create_openai_chunk(
                        finish_reason=mapped_reason,
                        model=model
                    )
                    
                    sse_event = self.sse_formatter.format_sse_event(data=finish_chunk)
                    
                    yield sse_event
                    self.buffer.mark_yield()
    
    async def process_stream(
        self,
        response_iterator,
        model: str = "vertex-ai-proxy"
    ) -> AsyncGenerator[str, None]:
        """
        处理完整的流式响应
        
        Args:
            response_iterator: 响应数据的异步迭代器
            model: 模型名称
        
        Yields:
            SSE格式的字符串
        """
        content_yielded = False
        is_error = False
        
        try:
            chunk_received = 0
            async for chunk in response_iterator:
                chunk_received += 1
                current_time = time.time()
                
                self._chunk_times.append(current_time)
                self._chunk_sizes.append(len(chunk))
                if len(self._chunk_times) > 20:
                    self._chunk_times.pop(0)
                    self._chunk_sizes.pop(0)
                
                self._last_chunk_time = current_time
                
                if chunk_received <= 3:
                    self._log_debug(f"收到 chunk #{chunk_received}: {len(chunk)} 字符")
                
                json_objects = self.json_parser.feed(chunk)
                
                if chunk_received <= 3:
                    self._log_debug(f"  解析出 {len(json_objects)} 个 JSON 对象")
                
                if len(chunk) > 500 and len(chunk) < 10000 and len(json_objects) == 0 and chunk_received > 1:
                    self._log_debug(f"⚠️ 大 chunk 但无 JSON: {len(chunk)} 字符")
                
                for obj in json_objects:
                    for sse_event in self.process_vertex_response(obj, model):
                        yield sse_event
                        content_yielded = True
                
                if self.enable_heartbeat and self.buffer.should_send_heartbeat(self.heartbeat_interval):
                    sequence = self.buffer.increment_sequence()
                    heartbeat = self.sse_formatter.create_heartbeat_event(sequence)
                    yield heartbeat
                    self.buffer.mark_yield()
                    
        except Exception as e:
            self._log_debug(f"流处理异常: {str(e)[:100]}")
            is_error = True
            raise e
            
        finally:
            if is_error:
                self._log_debug(f"流处理完成 (有错误)")
            else:
                remaining_json_objs = self.json_parser.flush()
                for obj in remaining_json_objs:
                    for sse_event in self.process_vertex_response(obj, model):
                        yield sse_event
                        content_yielded = True
                
                diff_flush_result = self.diff_handler.flush()
                if diff_flush_result:
                    flush_content, is_diff = diff_flush_result
                    if flush_content:
                        self._log_debug(f"DiffHandler flush: {len(flush_content)} 字符")
                        for sse_event in self._yield_content(flush_content, model, is_diff_block=is_diff):
                            yield sse_event
                            content_yielded = True
                
                pending_contents = self.path_tracker.get_pending_content()
                for path_idx, pending_content, is_thought in pending_contents:
                    if pending_content:
                        self._log_debug(f"PathTracker flush: {len(pending_content)} 字符")
                        for sse_event in self._yield_content(pending_content, model, is_reasoning=is_thought):
                            yield sse_event
                            content_yielded = True
                
                if not content_yielded:
                    self._log_debug("⚠️ 无内容输出，发送空消息")
                    if not self._role_sent:
                        self._role_sent = True
                        yield self.sse_formatter.create_initial_role_chunk(model)
                        self.buffer.mark_yield()
                    empty_chunk = self.sse_formatter.create_openai_chunk(content="", model=model)
                    yield self.sse_formatter.format_sse_event(data=empty_chunk)
                    finish_chunk = self.sse_formatter.create_openai_chunk(finish_reason="stop", model=model)
                    yield self.sse_formatter.format_sse_event(data=finish_chunk)
                    self.buffer.mark_yield()
                
                yield "data: [DONE]\n\n"


def get_stream_processor(enable_heartbeat: bool = True, heartbeat_interval: float = 15.0) -> StreamProcessor:
    """创建流处理器实例"""
    return StreamProcessor(enable_heartbeat=enable_heartbeat, heartbeat_interval=heartbeat_interval)