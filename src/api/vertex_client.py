"""Vertex AI客户端"""

import asyncio
import json
import time
import uuid
import re
import httpx
from typing import Dict, Any, Optional, List, AsyncGenerator

from src.core import TokenStatsManager, CredentialManager, MODELS_CONFIG_FILE
from src.stream import get_stream_processor, AuthError as StreamAuthError
from src.utils import autocorrect_diff
from src.utils.image import extract_images_from_assistant_message

# 从拆分的模块导入
from .chunk_aggregator import ChunkAggregator
from .message_builder import MessageBuilder
from .model_config import ModelConfigBuilder


class AuthError(Exception):
    """认证错误"""
    pass


class VertexAIClient:
    """Vertex AI API客户端"""
    
    def __init__(self, cred_manager: CredentialManager, stats_manager: TokenStatsManager,
                 request_token_refresh_callback=None):
        self.cred_manager = cred_manager
        self.stats_manager = stats_manager
        self.request_token_refresh = request_token_refresh_callback
        
        # 优化连接池配置，提升兼容性
        limits = httpx.Limits(
            max_keepalive_connections=20,
            max_connections=100,
            keepalive_expiry=30.0  # 显式设置 keepalive 过期时间
        )
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=30.0, read=120.0, write=30.0, pool=10.0),
            limits=limits,
            http1=True,   # 启用 HTTP/1.1 支持
            http2=True,   # 同时启用 HTTP/2 支持
        )
    
    def _create_isolated_client(self) -> httpx.AsyncClient:
        """
        为流式请求创建隔离的httpx客户端
        
        增强兼容性配置：
        - 同时支持 HTTP/1.1 和 HTTP/2
        - 更宽松的超时设置
        - 优化连接池管理
        """
        limits = httpx.Limits(
            max_keepalive_connections=5,  # 增加 keepalive 连接数
            max_connections=10,            # 增加最大连接数
            keepalive_expiry=60.0          # 延长 keepalive 过期时间
        )
        
        return httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=30.0,   # 增加连接超时
                read=180.0,     # 增加读取超时（长响应）
                write=30.0,     # 增加写入超时
                pool=30.0       # 增加池等待超时
            ),
            limits=limits,
            follow_redirects=True,  # 启用重定向跟随
            http1=True,             # 启用 HTTP/1.1（某些服务器不支持 HTTP/2）
            http2=True,             # 同时启用 HTTP/2
            verify=True
        )

    async def complete_chat(self, messages: List[Dict[str, str]], model: str, **kwargs) -> Dict[str, Any]:
        """聚合流式响应为非流式ChatCompletion对象"""
        
        full_content = ""
        reasoning_content = ""
        finish_reason = "stop"
        
        _raw_image_response = kwargs.pop('_raw_image_response', False)

        async for chunk_data_sse in self.stream_chat(messages, model, **kwargs):
            if chunk_data_sse.startswith("data: "):
                json_str = chunk_data_sse[6:].strip()
                if json_str == "[DONE]":
                    continue
                
                try:
                    chunk = json.loads(json_str)
                    choices = chunk.get('choices', [])
                    if choices:
                        delta = choices[0].get('delta', {})
                        
                        if 'content' in delta:
                            full_content += delta['content']
                        if 'reasoning_content' in delta:
                            reasoning_content += delta['reasoning_content']
                        if choices[0].get('finish_reason'):
                            finish_reason = choices[0]['finish_reason']
                            
                except json.JSONDecodeError as e:
                    print(f"⚠️ JSON 解析错误: {e}")
                    
        full_content = autocorrect_diff(full_content)

        if full_content.startswith("![Generated Image](data:"):
            print("ℹ️ 检测到图像响应 (非流式)")
            data_url = full_content[21:-1]
            
            if _raw_image_response:
                try:
                    header, encoded = data_url.split(',', 1)
                    return {
                        "created": int(time.time()),
                        "data": [{"b64_json": encoded}]
                    }
                except Exception as e:
                    print(f"❌ 解析图像 URL 失败: {e}")
                    return {"created": int(time.time()), "data": []}
            else:
                return {"resultUrl": data_url}
            
        if '<tool_calls>' in full_content and '</tool_calls>' in full_content:
            print("ℹ️ 检测到工具调用块")
            final_content = full_content
            response = {
                "id": f"chatcmpl-proxy-nonstream-{uuid.uuid4()}",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": model,
                "usage": self.stats_manager.get_current_usage(),
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": final_content
                        },
                        "finish_reason": "stop" # Finish reason is 'stop' as it's a text response
                    }
                ]
            }
            return response
        
        final_content = full_content
        if reasoning_content:
            cleaned_reasoning = re.sub(r'\n\s*\n', '\n', reasoning_content).strip()
            final_content = f"**Reasoning:**\n{cleaned_reasoning}\n\n**Response:**\n{full_content}"
        
        if not final_content:
            final_content = " "
            
        response = {
            "id": f"chatcmpl-proxy-nonstream-{uuid.uuid4()}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "usage": self.stats_manager.get_current_usage(),
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": final_content
                    },
                    "finish_reason": finish_reason
                }
            ]
        }
        return response

    async def stream_chat(self, messages: List[Dict[str, str]], model: str, **kwargs):
        """流式聊天 - 优化版（支持请求队列和预刷新）"""
        request_id = str(uuid.uuid4())[:8]  # 生成请求ID用于追踪
        if not self.cred_manager.latest_harvest or (time.time() - self.cred_manager.last_updated > 3000):
            async with self.cred_manager.refresh_lock:
                should_refresh = False
                if not self.cred_manager.latest_harvest:
                    should_refresh = True
                elif time.time() - self.cred_manager.last_updated > 3000:
                    print("⚠️ 凭证已过期 (>50分钟)，触发刷新...")
                    should_refresh = True
                
                if should_refresh:
                    if self.request_token_refresh:
                        await self.request_token_refresh()
                    
                    print(f"[{request_id}] ⏳ 等待新凭证...")
                    # 使用新的队列机制等待
                    refreshed = await self.cred_manager.wait_for_credential_with_queue(request_id, timeout=60)
                    
                    if refreshed:
                        await asyncio.sleep(0.5)  # 短暂延迟确保凭证就绪
                    
                    if not refreshed and not self.cred_manager.latest_harvest:
                        error_msg = "⚠️ **Proxy Error**: Could not refresh credentials.\n\nPlease ensure **Google Vertex AI Studio** is open in your browser and the Harvester script is active."
                        chunk = {
                            "id": "error-no-creds",
                            "object": "chat.completion.chunk",
                            "created": int(time.time()),
                            "model": "vertex-ai-proxy",
                            "choices": [{"index": 0, "delta": {"content": error_msg}, "finish_reason": "stop"}]
                        }
                        yield f"data: {json.dumps(chunk)}\n\n"
                        yield "data: [DONE]\n\n"
                        return

        # 预刷新检测：如果凭证即将过期，提前触发刷新
        if self.cred_manager.should_preemptive_refresh(threshold=120):
            print(f"[{request_id}] 🔄 凭证即将过期，触发预刷新...")
            if self.request_token_refresh:
                # 异步触发刷新，不阻塞当前请求
                asyncio.create_task(self.request_token_refresh())

        max_retries = 3  # 增加重试次数
        content_yielded = False
        isolated_client = self._create_isolated_client()
        
        try:
            for attempt in range(max_retries + 1):
                stream_processor = get_stream_processor()
                stream_processor.enable_debug(True)
                
                # 记录当前凭证版本
                current_cred_version = self.cred_manager.credential_version
                
                creds = self.cred_manager.get_credentials()
                if not creds:
                    if attempt > 0:
                        break
                    return

                raw_body = creds['body']
                if isinstance(raw_body, dict):
                    original_body = raw_body
                else:
                    original_body = json.loads(raw_body)
            
                system_instruction = ""
                chat_history = []
                all_assistant_images_with_turn = []
                
                last_user_index = -1
                assistant_turn_number = 0
                for i, msg in enumerate(messages):
                    if msg['role'] == 'user':
                        last_user_index = i
                    elif msg['role'] == 'assistant':
                        assistant_turn_number += 1
                        assistant_content = msg['content'] if isinstance(msg['content'], str) else ""
                        if assistant_content and 'data:image/' in assistant_content and ';base64,' in assistant_content:
                            _, image_parts = extract_images_from_assistant_message(assistant_content)
                            if image_parts:
                                for img_part in image_parts:
                                    all_assistant_images_with_turn.append((assistant_turn_number, img_part))
                
                if all_assistant_images_with_turn:
                    print(f"ℹ️ 共收集 {len(all_assistant_images_with_turn)} 张历史图片")
                
                for i, msg in enumerate(messages):
                    if msg['role'] == 'system':
                        # 处理 system 消息的 content 可能是字符串或列表
                        if isinstance(msg['content'], str):
                            system_instruction += msg['content'] + "\n"
                        elif isinstance(msg['content'], list):
                            # 如果是列表,提取所有文本部分
                            for part in msg['content']:
                                if isinstance(part, dict) and part.get('type') == 'text':
                                    system_instruction += part.get('text', '') + "\n"
                                elif isinstance(part, str):
                                    system_instruction += part + "\n"
                    elif msg['role'] == 'user':
                        parts = []
                        
                        if i == last_user_index and all_assistant_images_with_turn:
                            parts.append({"text": f"[以下是之前生成的 {len(all_assistant_images_with_turn)} 张图片：]"})
                            current_turn = 0
                            for turn_num, img_part in all_assistant_images_with_turn:
                                if turn_num != current_turn:
                                    current_turn = turn_num
                                    parts.append({"text": f"[第 {turn_num} 轮生成的图片:]"})
                                parts.append(img_part)
                            
                            parts.append({"text": "[以上是历史图片，用户新请求如下:]"})
                            print(f"ℹ️ 注入 {len(all_assistant_images_with_turn)} 张历史图片")
                        
                        if isinstance(msg['content'], str):
                            parts.append({"text": msg['content']})
                        elif isinstance(msg['content'], list):
                            for part in msg['content']:
                                if part['type'] == 'text':
                                    parts.append({"text": part['text']})
                                elif part['type'] == 'image_url':
                                    image_url = part['image_url']['url']
                                    if image_url.startswith('data:'):
                                        header, encoded = image_url.split(',', 1)
                                        mime_type = header.split(':')[1].split(';')[0]
                                        parts.append({
                                            "inlineData": {
                                                "mimeType": mime_type,
                                                "data": encoded
                                            }
                                        })
                        chat_history.append({"role": "user", "parts": parts})
                    elif msg['role'] == 'assistant':
                        assistant_content = msg['content'] if isinstance(msg['content'], str) else ""
                        
                        if assistant_content and 'data:image/' in assistant_content and ';base64,' in assistant_content:
                            cleaned_text, _ = extract_images_from_assistant_message(assistant_content)
                            
                            if cleaned_text.strip():
                                chat_history.append({"role": "model", "parts": [{"text": cleaned_text}]})
                            else:
                                # 如果没有文本，添加一个简短说明
                                chat_history.append({"role": "model", "parts": [{"text": "[已生成图片]"}]})
                        else:
                            # 普通文本消息，直接添加
                            if assistant_content:
                                chat_history.append({"role": "model", "parts": [{"text": assistant_content}]})

                # 2. Construct New Body
                # We clone the harvested body structure to keep all the magic context/metadata
                new_variables = original_body.get('variables', {}).copy()
                
                # Update contents (Chat History)
                new_variables['contents'] = chat_history
                
                # Inject Tools into System Instruction (Custom Format)
                if 'tools' in kwargs and kwargs['tools']:
                    print(f"ℹ️ 注入 {len(kwargs['tools'])} 个工具到系统提示")
                    tools_xml = "\n\n<available_tools>\n"
                    for tool in kwargs['tools']:
                        function = tool.get('function', {})
                        tools_xml += f"  <tool>\n"
                        tools_xml += f"    <name>{function.get('name', '')}</name>\n"
                        tools_xml += f"    <description>{function.get('description', '')}</description>\n"
                        # Ensure parameters are serialized to a string
                        params = function.get('parameters', {})
                        tools_xml += f"    <parameters>{json.dumps(params)}</parameters>\n"
                        tools_xml += f"  </tool>\n"
                    tools_xml += "</available_tools>\n"
                    
                    # Add instruction for the model to use the specific XML format expected by the parser
                    tools_xml += "\nIMPORTANT: To use a tool, you MUST output a <tool_calls> block. "
                    system_instruction += tools_xml

                # Update System Instruction
                if system_instruction:
                    new_variables['systemInstruction'] = {"parts": [{"text": system_instruction.strip()}]}

                # Disable Safety Filters
                new_variables['safetySettings'] = [
                    {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
                    {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
                    {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
                    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
                    {"category": "HARM_CATEGORY_CIVIC_INTEGRITY", "threshold": "BLOCK_NONE"}
                ]

                # CLEANUP: Remove tools and toolConfig to prevent context interference
                # Harvester might capture a session with tools enabled (e.g. Google Search),
                # which can confuse the model if we don't intend to use them.
                # new_variables.pop('tools', None)
                # new_variables.pop('toolConfig', None)
                    
                # Update Model
                # Load model mapping from models.json
                model_map = {}
                try:
                    with open(MODELS_CONFIG_FILE, 'r', encoding='utf-8') as f:
                        config = json.load(f)
                        model_map = config.get('alias_map', {})
                except Exception as e:
                    print(f"⚠️ 加载 models.json 失败: {e}")

                target_model = model_map.get(model, model)
                
                # Handle suffixes for thinking and resolution
                thinking_mode = None
                resolution_mode = None
                
                if target_model.endswith("-low"):
                    target_model = target_model[:-4]
                    thinking_mode = "low"
                elif target_model.endswith("-high"):
                    target_model = target_model[:-5]
                    thinking_mode = "high"
                    
                if target_model.endswith("-1k"):
                    resolution_mode = "1k"
                    target_model = target_model[:-3]
                elif target_model.endswith("-2k"):
                    resolution_mode = "2k"
                    target_model = target_model[:-3]
                elif target_model.endswith("-4k"):
                    resolution_mode = "4k"
                    target_model = target_model[:-3]

                # The target_model variable already holds the base model name (stripped of resolution suffix)
                # if a resolution suffix was present. We use it directly as the backend model ID.
                backend_model_for_api = target_model
                
                # 简化模型切换日志
                new_variables['model'] = backend_model_for_api
                
                # Apply generation parameters from client
                if 'generationConfig' not in new_variables:
                    new_variables['generationConfig'] = {}
                
                gen_config = new_variables['generationConfig']

                # Handle Thinking Config
                # Case 1: Explicit suffixes (-low, -high)
                if thinking_mode:
                    gen_config['thinkingConfig'] = {"includeThoughts": True}
                    if thinking_mode == 'low':
                         budget = 8192
                    elif thinking_mode == 'high':
                         budget = 32768
                    
                    gen_config['thinkingConfig']['budget_token_count'] = budget
                    gen_config['thinkingConfig']['thinkingBudget'] = budget
                    print(f"ℹ️ 思考模式: {thinking_mode}, 预算: {budget}")

                # Case 2: No suffix, but client provided max_tokens (treat as thinking budget for 3-pro)
                # Only applies if we haven't already set a thinking mode via suffix
                elif 'gemini-3-pro' in target_model and 'max_tokens' in kwargs and kwargs['max_tokens'] is not None:
                    budget = int(kwargs['max_tokens'])
                    # Only enable thinking if budget is reasonable for thinking (e.g. > 1024)
                    # or if user explicitly wants it. Let's assume max_tokens on 3-pro implies thinking budget.
                    gen_config['thinkingConfig'] = {
                        "includeThoughts": True,
                        "budget_token_count": budget,
                        "thinkingBudget": budget
                    }
                    print(f"ℹ️ 思考模式 (自定义): 预算={budget}")
                
                # Handle Resolution (Image Generation)
                # New logic: Check for "image" in model name, then check for resolution suffix.
                if "image" in target_model:
                    # This is an image model. Ensure response modalities are set.
                    if 'responseModalities' not in gen_config:
                        gen_config['responseModalities'] = ["TEXT", "IMAGE"]
                    if 'imageConfig' not in gen_config:
                        gen_config['imageConfig'] = {}
                    
                    # Set other standard image generation parameters from logs
                    gen_config['imageConfig']['personGeneration'] = "ALLOW_ALL"
                    if 'imageOutputOptions' not in gen_config['imageConfig']:
                        gen_config['imageConfig']['imageOutputOptions'] = {"mimeType": "image/png"}

                    # Only add imageSize if a resolution suffix is present
                    if resolution_mode:
                        size_str_map = {
                            "1k": "1K",
                            "2k": "2K",
                            "4k": "4K"
                        }
                        if resolution_mode in size_str_map:
                            gen_config['imageConfig']['imageSize'] = size_str_map[resolution_mode]
                            print(f"ℹ️ 图像生成: 尺寸={gen_config['imageConfig']['imageSize']}")
                    else:
                        # If no suffix, remove any existing imageSize to let Google decide
                        gen_config['imageConfig'].pop('imageSize', None)
                        print(f"ℹ️ 图像生成: 默认尺寸")
                
                # CLEANUP: Remove model-specific configurations that might cause conflicts
                # If we switch models, old generation configs (like thinking) might be invalid.
                
                # Remove 'thinkingConfig' if present, unless the model is explicitly a thinking model
                if not thinking_mode:
                    gen_config.pop('thinkingConfig', None)
                    # Also check for snake_case just in case
                    gen_config.pop('thinking_config', None)

                # Remove 'imageConfig' if NOT an image model (to be safe)
                # 修复: 使用 target_model 判断是否为图像模型，而不是 resolution_mode
                # 因为 resolution_mode 只在有 -1k/-2k/-4k 后缀时才设置
                if "image" not in target_model:
                    gen_config.pop('imageConfig', None)
                    gen_config.pop('sampleImageSize', None)
                    gen_config.pop('width', None)
                    gen_config.pop('height', None)
                    # 清理 responseModalities - 非图像模型不应该有多模态输出配置
                    # 否则会导致 "Multi-modal output is not supported" 错误
                    gen_config.pop('responseModalities', None)
                
                # Note: The exact field name might be 'thinkingConfig' or inside 'generationConfig'
                # Based on common Vertex AI payloads, let's check 'generationConfig'
                
                # Fix maxOutputTokens
                # Allow client to override max_tokens, otherwise default to harvested value or 65535
                # client_max_tokens = original_body.get('variables', {}).get('generationConfig', {}).get('maxOutputTokens')
                
                # Check if client provided max_tokens in the request body (OpenAI format)
                # Note: 'original_body' here is the harvested body. We need to check the incoming 'messages' or 'body' from the request.
                # But wait, 'stream_chat' doesn't receive the full request body, only 'messages' and 'model'.
                # Let's assume we want to restore the high limit.
                
                if isinstance(gen_config, dict):
                    # Restore high limit or use a safe default
                    # If the harvested token had a value, we keep it (unless we want to force it)
                    # User requested to put it back to 65535
                    if 'maxOutputTokens' in gen_config:
                        # Ensure it's at least 8192 if it was lowered, or just set to 65535 if missing/low
                        if gen_config['maxOutputTokens'] < 8192:
                                gen_config['maxOutputTokens'] = 65535
                    else:
                        gen_config['maxOutputTokens'] = 65535
                
                if 'temperature' in kwargs and kwargs['temperature'] is not None:
                    gen_config['temperature'] = float(kwargs['temperature'])
                    
                if 'top_p' in kwargs and kwargs['top_p'] is not None:
                    gen_config['topP'] = float(kwargs['top_p'])
                    
                if 'top_k' in kwargs and kwargs['top_k'] is not None:
                    gen_config['topK'] = int(kwargs['top_k'])
                    
                if 'max_tokens' in kwargs and kwargs['max_tokens'] is not None:
                    gen_config['maxOutputTokens'] = int(kwargs['max_tokens'])
                    
                if 'stop' in kwargs and kwargs['stop'] is not None:
                    gen_config['stopSequences'] = kwargs['stop'] if isinstance(kwargs['stop'], list) else [kwargs['stop']]

                # Reassemble body
                new_body = {
                    "querySignature": original_body.get('querySignature'), # Might need this?
                    "operationName": original_body.get('operationName'),
                    "variables": new_variables
                }
                
                # 3. Prepare Headers
                headers = creds['headers'].copy() # Copy to avoid mutating the cached credentials
                
                # Ensure critical headers are present and correct
                # Note: 'Cookie', 'User-Agent', 'Origin', 'Referer' should now be in creds['headers'] from the harvester
                
                headers['content-type'] = 'application/json'
                
                # Remove headers that httpx/network layer should handle or that might cause conflicts
                headers.pop('content-length', None)
                headers.pop('Content-Length', None)
                headers.pop('host', None)
                headers.pop('Host', None)
                headers.pop('connection', None)
                headers.pop('Connection', None)
                headers.pop('accept-encoding', None) # Let httpx handle decompression

                url = creds['url']
                
                # 简化日志 - 仅在首次请求时打印模型名
                if attempt == 0:
                    print(f"→ {backend_model_for_api}")
                else:
                    print(f"↻ 重试({attempt+1})")
                try:
                    # 使用独立客户端进行流式请求,确保请求间完全隔离
                    async with isolated_client.stream('POST', url, headers=headers, json=new_body) as response:
                        print(f"📡 Response Status: {response.status_code}")
                    
                        if response.status_code != 200:
                            error_text = await response.aread()
                            print(f"✗ API 错误: {response.status_code}")
                            
                            # Check for potential token expiration
                            if response.status_code in [400, 401, 403] and attempt < max_retries:
                                print(f"[{request_id}] ⚠️ 认证错误 ({response.status_code})，触发刷新...")
                                
                                # Trigger UI Refresh
                                if self.request_token_refresh:
                                    await self.request_token_refresh()
                                
                                # 使用队列机制等待新凭证（更快响应）
                                refresh_start = time.time()
                                refreshed = await self.cred_manager.wait_for_credential_with_queue(request_id, timeout=30)
                                refresh_elapsed = time.time() - refresh_start
                                
                                if refreshed:
                                    # 验证凭证版本是否更新
                                    new_version = self.cred_manager.credential_version
                                    if new_version > current_cred_version:
                                        print(f"[{request_id}] ✅ 凭证已更新 v{current_cred_version} → v{new_version} ({refresh_elapsed:.1f}秒)")
                                        
                                        await asyncio.sleep(0.3)  # 短暂延迟
                                        # Update headers/url with new credentials
                                        new_creds = self.cred_manager.get_credentials()
                                        headers = new_creds['headers'].copy()
                                        headers['content-type'] = 'application/json'
                                        headers.pop('content-length', None)
                                        headers.pop('host', None)
                                        url = new_creds['url']
                                        print(f"[{request_id}] 🔄 使用新凭证重试...")
                                        continue # Retry loop
                                    else:
                                        print(f"[{request_id}] ⚠️ 凭证版本未变化")
                                else:
                                    print(f"[{request_id}] ⚠️ 凭证刷新超时 ({refresh_elapsed:.1f}秒)")
                            
                            # If we get here, it's a fatal error or retry failed
                            error_payload = {"error": {"message": f"Upstream Error: {response.status_code} - {error_text.decode()}", "type": "upstream_error"}}
                            yield f"data: {json.dumps(error_payload)}\n\n"
                            return

                        # Layer 1: 使用ChunkAggregator稳定输入流
                        # v5.0: 增加min_chunk_size以确保JSON边界稳定性
                        aggregator = ChunkAggregator(min_chunk_size=256, max_buffer_time=0.1)
                        stabilized_stream = aggregator.aggregate(response.aiter_text())
                        
                        # 使用StreamProcessor处理响应流
                        chunk_count = 0
                        total_completion_chars = 0
                        stream_error = None  # v8.1: 追踪流处理中的错误
                        
                        try:
                            async for sse_event in stream_processor.process_stream(stabilized_stream, model=model):
                                chunk_count += 1
                                # 统计completion字符数用于token估算
                                if 'data: ' in sse_event and '"content"' in sse_event:
                                    try:
                                        json_part = sse_event.split('data: ', 1)[1].split('\n')[0]
                                        if json_part != '[DONE]':
                                            chunk_obj = json.loads(json_part)
                                            delta_content = chunk_obj.get('choices', [{}])[0].get('delta', {}).get('content', '')
                                            if delta_content:
                                                total_completion_chars += len(delta_content)
                                    except:
                                        pass
                                yield sse_event
                                # v8.3: 使用 stream_processor 追踪实际内容是否已发送
                                # role chunk 和 heartbeat chunk 不算实际内容，仍可重试
                                content_yielded = stream_processor.has_actual_content_sent()
                                await asyncio.sleep(0)
                        except (AuthError, StreamAuthError) as e:
                            # v8.1: 捕获流处理中的认证错误
                            stream_error = e
                            print(f"⚠️ 流中检测到认证错误")
                        
                        # v8.1: 如果流处理中发生认证错误，触发重试
                        if stream_error:
                            if content_yielded:
                                # 已发送内容，无法重试
                                print("⚠️ 已发送内容，无法重试")
                                error_payload = {"error": {"message": f"Authentication failed mid-stream: {str(stream_error)}", "type": "authentication_error"}}
                                yield f"data: {json.dumps(error_payload)}\n\n"
                                yield "data: [DONE]\n\n"
                                return
                            
                            if attempt < max_retries:
                                print(f"[{request_id}] 🔄 流中认证错误，触发刷新 (尝试 {attempt+1}/{max_retries+1})")
                                
                                # 触发刷新
                                if self.request_token_refresh:
                                    await self.request_token_refresh()
                                
                                # 使用队列机制等待新凭证
                                refresh_start = time.time()
                                refreshed = await self.cred_manager.wait_for_credential_with_queue(request_id, timeout=30)
                                refresh_elapsed = time.time() - refresh_start
                                
                                if refreshed:
                                    # 验证凭证版本是否更新
                                    new_version = self.cred_manager.credential_version
                                    if new_version > current_cred_version:
                                        print(f"[{request_id}] ✅ 凭证已更新 v{current_cred_version} → v{new_version} ({refresh_elapsed:.1f}秒)")
                                        await asyncio.sleep(0.3)
                                        print(f"[{request_id}] 🔄 使用新凭证重试...")
                                        continue  # 重试循环
                                    else:
                                        print(f"[{request_id}] ⚠️ 凭证版本未变化")
                                else:
                                    print(f"[{request_id}] ⚠️ 凭证刷新超时 ({refresh_elapsed:.1f}秒)")
                            
                            # 重试用尽或刷新失败 - 静默失败，让系统自动处理
                            print(f"⚠️ 凭证刷新失败，已达最大重试次数")
                            # 不向客户端返回错误信息，让请求静默失败
                            return
                        
                        # 估算并更新token统计
                        # 图像模型使用固定token计数，LLM使用字符估算
                        is_image_model = "image" in backend_model_for_api.lower()
                        
                        if is_image_model:
                            # 图像模型: 使用固定的估算值
                            # 输入约500 token，输出图像约1000 token
                            prompt_tokens = 500
                            completion_tokens = 1000
                        else:
                            # LLM: 根据实际内容估算
                            prompt_tokens = self.stats_manager.estimate_messages_tokens(messages)
                            completion_tokens = max(1, int(total_completion_chars / 3.5)) if total_completion_chars > 0 else 1
                        
                        await self.stats_manager.update(prompt_tokens, completion_tokens, model=model)
                        self.stats_manager.set_current_request_tokens(prompt_tokens, completion_tokens)
                        
                        # 发送包含usage的最终chunk给客户端
                        usage_chunk = {
                            "id": f"chatcmpl-proxy-usage-{uuid.uuid4()}",
                            "object": "chat.completion.chunk",
                            "created": int(time.time()),
                            "model": model,
                            "choices": [{"index": 0, "delta": {}, "finish_reason": None}],
                            "usage": {
                                "prompt_tokens": prompt_tokens,
                                "completion_tokens": completion_tokens,
                                "total_tokens": prompt_tokens + completion_tokens
                            }
                        }
                        yield f"data: {json.dumps(usage_chunk)}\n\n"
                        
                        # v8.1: 只有成功完成才发送[DONE]
                        yield "data: [DONE]\n\n"
                        
                        # 简化完成日志
                        if is_image_model:
                            print(f"✅ 图像生成完成")
                        else:
                            print(f"✅ {chunk_count} 块 | {prompt_tokens}+{completion_tokens}={prompt_tokens+completion_tokens} token")
                        
                        # 如果成功处理完流，跳出重试循环
                        break

                except (AuthError, StreamAuthError) as e:
                    print(f"⚠️ 认证错误")
                    
                    # 如果已经发送了内容，不能重试
                    if content_yielded:
                        print("⚠️ 已发送内容，无法重试")
                        error_payload = {"error": {"message": f"Authentication failed mid-stream: {str(e)}", "type": "authentication_error"}}
                        yield f"data: {json.dumps(error_payload)}\n\n"
                        return

                    if attempt < max_retries:
                        print("🔄 触发刷新并重试...")
                        if self.request_token_refresh:
                            await self.request_token_refresh()
                        # Step 1: Wait for the new credentials to be harvested
                        refreshed = await self.cred_manager.wait_for_refresh(timeout=60)
                        if refreshed:
                            ui_ready = await self.cred_manager.wait_for_refresh_complete(timeout=60)
                            if ui_ready:
                                print("✅ 凭证和 UI 已就绪")
                                await asyncio.sleep(1) # Add 1 second delay
                                # Update headers/url with new credentials
                                new_creds = self.cred_manager.get_credentials()
                                headers = new_creds['headers'].copy()
                                headers['content-type'] = 'application/json'
                                headers.pop('content-length', None)
                                headers.pop('host', None)
                                url = new_creds['url']
                                continue # Retry the request
                            else:
                                print("✗ UI 未就绪")
                        else:
                            print("✗ 刷新超时")

                    error_payload = {"error": {"message": str(e), "type": "authentication_error"}}
                    yield f"data: {json.dumps(error_payload)}\n\n"
                    return

                except Exception as e:
                    print(f"✗ 请求失败: {str(e)[:50]}")
                    
                    if content_yielded:
                        print("⚠️ 已发送内容，无法重试")
                        error_payload = {"error": {"message": f"Stream interrupted: {str(e)}", "type": "request_error"}}
                        yield f"data: {json.dumps(error_payload)}\n\n"
                        return

                    if attempt < max_retries:
                        continue
                    error_payload = {"error": {"message": str(e), "type": "request_error"}}
                    yield f"data: {json.dumps(error_payload)}\n\n"
                    return # Stop generator on fatal error
        
        finally:
            # 确保独立客户端被正确关闭,释放资源
            await isolated_client.aclose()