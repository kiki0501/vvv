"""FastAPI路由模块"""

import asyncio
import json
import time
import uuid
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import StreamingResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from typing import Dict, Any

from src.core import MODELS_CONFIG_FILE, TokenStatsManager
from src.api.vertex_client import VertexAIClient


class ConnectionCompatibilityMiddleware(BaseHTTPMiddleware):
    """
    连接兼容性中间件
    
    解决 httpx 等现代 HTTP 客户端的连接问题：
    - 确保正确的 Connection 头处理
    - 支持 HTTP/1.0 和 HTTP/1.1 客户端
    """
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        
        # 确保响应包含适当的连接头
        # 某些客户端（如 httpx）需要明确的 keep-alive 支持
        if "connection" not in response.headers:
            response.headers["Connection"] = "keep-alive"
        
        return response


def create_app(vertex_client: VertexAIClient, stats_manager: TokenStatsManager) -> FastAPI:
    """创建FastAPI应用"""
    app = FastAPI()
    
    # 添加连接兼容性中间件
    app.add_middleware(ConnectionCompatibilityMiddleware)
    
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # Allows all origins
        allow_credentials=True,
        allow_methods=["*"],  # Allows all methods
        allow_headers=["*"],  # Allows all headers
        expose_headers=["*"],  # 暴露所有响应头给客户端
    )
    
    @app.get("/v1/models")
    async def list_models():
        """返回可用模型列表"""
        current_time = int(time.time())
        models = []
        try:
            with open(MODELS_CONFIG_FILE, 'r', encoding='utf-8') as f:
                config = json.load(f)
                models = config.get('models', [])
        except Exception as e:
            print(f"⚠️ 加载 models.json 失败: {e}")
            models = ["gemini-1.5-pro", "gemini-1.5-flash"]

        data = {
            "object": "list",
            "data": [
                {"id": m, "object": "model", "created": current_time, "owned_by": "google"}
                for m in models
            ]
        }
        return data

    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request):
        """处理聊天补全请求"""
        try:
            body = await request.json()
            messages = body.get('messages', [])
            model = body.get('model', 'gemini-1.5-pro')
            stream = body.get('stream', False)
            
            temperature = body.get('temperature')
            top_p = body.get('top_p')
            top_k = body.get('top_k')
            max_tokens = body.get('max_tokens')
            stop = body.get('stop')
            tools = body.get('tools')
            
            if not messages:
                if stream:
                    async def empty_stream_generator():
                        empty_chunk = {
                            "id": f"chatcmpl-proxy-empty-{uuid.uuid4()}",
                            "object": "chat.completion.chunk",
                            "created": int(time.time()),
                            "model": model,
                            "choices": [{"index": 0, "delta": {"role": "assistant", "content": ""}, "finish_reason": "stop"}]
                        }
                        yield f"data: {json.dumps(empty_chunk)}\n\n"
                        yield "data: [DONE]\n\n"
                    return StreamingResponse(empty_stream_generator(), media_type="text/event-stream")
                else:
                    return {
                        "id": f"chatcmpl-proxy-empty-{uuid.uuid4()}",
                        "object": "chat.completion",
                        "created": int(time.time()),
                        "model": model,
                        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                        "choices": [{
                            "index": 0,
                            "message": {"role": "assistant", "content": ""},
                            "finish_reason": "stop"
                        }]
                    }

            if stream:
                async def stream_with_disconnect_check():
                    """包装流式响应，添加客户端断开检测"""
                    try:
                        async for chunk in vertex_client.stream_chat(
                            messages,
                            model,
                            temperature=temperature,
                            top_p=top_p,
                            top_k=top_k,
                            max_tokens=max_tokens,
                            stop=stop,
                            tools=tools
                        ):
                            if await request.is_disconnected():
                                print("⚠️ 客户端断开，终止响应")
                                break
                            yield chunk
                    except asyncio.CancelledError:
                        print("⚠️ 响应已取消")
                        raise
                
                # 增强的 SSE 响应头，提升 httpx 等客户端兼容性
                sse_headers = {
                    "Cache-Control": "no-cache, no-store, must-revalidate, max-age=0",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no",  # 禁用 nginx 缓冲
                    "Transfer-Encoding": "chunked",
                }
                
                return StreamingResponse(
                    stream_with_disconnect_check(),
                    media_type="text/event-stream",
                    headers=sse_headers
                )
            else:
                response_data = await vertex_client.complete_chat(
                    messages,
                    model,
                    temperature=temperature,
                    top_p=top_p,
                    top_k=top_k,
                    max_tokens=max_tokens,
                    stop=stop,
                    tools=tools
                )
                return response_data

        except HTTPException:
            raise
        except Exception as e:
            print(f"⚠️ 端点异常: {e}")
            raise HTTPException(status_code=500, detail={"error": str(e)})
    
    return app