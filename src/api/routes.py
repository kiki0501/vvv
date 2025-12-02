"""FastAPI路由模块"""

import asyncio
import json
import os
import time
import uuid
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import StreamingResponse, Response, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from typing import Dict, Any, List

from src.core import MODELS_CONFIG_FILE, TokenStatsManager, load_config
from src.api.vertex_client import VertexAIClient


class APIKeyAuthMiddleware(BaseHTTPMiddleware):
    """API 密钥验证中间件"""
    
    def __init__(self, app, api_keys: List[str]):
        super().__init__(app)
        self.api_keys = api_keys
        self.enabled = len(api_keys) > 0
    
    async def dispatch(self, request: Request, call_next):
        # 如果未配置密钥，则不验证
        if not self.enabled:
            return await call_next(request)
        
        # 跳过健康检查端点和模型列表
        if request.url.path in ["/health", "/v1/models"]:
            return await call_next(request)
        
        # 统计页面和API也需要验证（优先Cookie，其次Header）
        if request.url.path in ["/stats", "/api/stats", "/"]:
            # 优先从Cookie获取API key（避免URL泄露）
            api_key = request.cookies.get("stats_api_key", "")
            
            # 如果Cookie没有，尝试从Authorization头获取
            if not api_key:
                auth_header = request.headers.get("Authorization", "")
                if auth_header.startswith("Bearer "):
                    api_key = auth_header[7:]
                else:
                    api_key = auth_header
            
            # 无头模式特殊处理：如果有temp参数，尝试从localStorage恢复
            if not api_key and request.url.path in ["/stats", "/"]:
                temp_token = request.query_params.get("temp", "")
                if temp_token:
                    # 返回一个特殊的页面，尝试从localStorage恢复API key
                    return Response(
                        content=self._get_recovery_page(),
                        status_code=200,
                        media_type="text/html"
                    )
            
            # 验证密钥
            if api_key not in self.api_keys:
                # 统计页面返回HTML登录页面
                if request.url.path in ["/stats", "/"]:
                    return Response(
                        content=self._get_login_page(),
                        status_code=401,
                        media_type="text/html"
                    )
                # API返回JSON错误
                else:
                    return Response(
                        content=json.dumps({
                            "error": {
                                "message": "Invalid API key",
                                "type": "invalid_request_error",
                                "code": "invalid_api_key"
                            }
                        }),
                        status_code=401,
                        media_type="application/json"
                    )
            
            return await call_next(request)
        
        # 其他端点从 Authorization 头获取密钥
        auth_header = request.headers.get("Authorization", "")
        
        # 支持 "Bearer sk-xxx" 和 "sk-xxx" 两种格式
        if auth_header.startswith("Bearer "):
            api_key = auth_header[7:]
        else:
            api_key = auth_header
        
        # 验证密钥
        if api_key not in self.api_keys:
            return Response(
                content=json.dumps({
                    "error": {
                        "message": "Invalid API key",
                        "type": "invalid_request_error",
                        "code": "invalid_api_key"
                    }
                }),
                status_code=401,
                media_type="application/json"
            )
        
        return await call_next(request)
    
    def _get_login_page(self):
        """返回登录页面HTML"""
        return """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>统计页面 - 身份验证</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Roboto', sans-serif;
            background-color: #f3f4f6;
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 20px;
        }
        .login-container {
            background: white;
            border-radius: 16px;
            box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -1px rgba(0, 0, 0, 0.06);
            padding: 40px;
            max-width: 400px;
            width: 100%;
            border: 1px solid #e5e7eb;
        }
        h1 {
            font-size: 24px;
            font-weight: 700;
            color: #1f2937;
            margin-bottom: 8px;
            text-align: center;
        }
        .subtitle {
            color: #6b7280;
            font-size: 14px;
            text-align: center;
            margin-bottom: 32px;
        }
        .input-group {
            margin-bottom: 24px;
        }
        label {
            display: block;
            font-size: 14px;
            font-weight: 600;
            color: #374151;
            margin-bottom: 8px;
        }
        input[type="password"] {
            width: 100%;
            padding: 12px 16px;
            font-size: 14px;
            border: 2px solid #e5e7eb;
            border-radius: 8px;
            transition: all 0.2s;
            outline: none;
        }
        input[type="password"]:focus {
            border-color: #3b82f6;
            box-shadow: 0 0 0 3px rgba(59, 130, 246, 0.1);
        }
        button {
            width: 100%;
            padding: 12px;
            background-color: #3b82f6;
            color: white;
            border: none;
            border-radius: 8px;
            font-size: 14px;
            font-weight: 600;
            cursor: pointer;
            transition: transform 0.2s, box-shadow 0.2s, background-color 0.2s;
        }
        button:hover {
            background-color: #2563eb;
            transform: translateY(-2px);
            box-shadow: 0 4px 6px rgba(59, 130, 246, 0.3);
        }
        button:active {
            transform: translateY(0);
        }
        .error {
            background: #fef2f2;
            color: #991b1b;
            padding: 12px;
            border-radius: 8px;
            font-size: 13px;
            margin-top: 16px;
            display: none;
        }
        .error.show {
            display: block;
        }
        .icon {
            text-align: center;
            font-size: 48px;
            margin-bottom: 16px;
        }
    </style>
</head>
<body>
    <div class="login-container">
        <div class="icon">🔐</div>
        <h1>统计仪表板</h1>
        <p class="subtitle">请输入 API Key 以访问</p>
        
        <form id="loginForm">
            <div class="input-group">
                <label for="apiKey">API Key</label>
                <input type="password" id="apiKey" placeholder="输入您的 API Key" autocomplete="off" required>
            </div>
            <button type="submit">访问仪表板</button>
            <div class="error" id="errorMsg">API Key 无效，请重试</div>
        </form>
    </div>

    <script>
        const form = document.getElementById('loginForm');
        const apiKeyInput = document.getElementById('apiKey');
        const errorMsg = document.getElementById('errorMsg');

        form.addEventListener('submit', async (e) => {
            e.preventDefault();
            errorMsg.classList.remove('show');
            
            const apiKey = apiKeyInput.value.trim();
            if (!apiKey) return;

            // 验证API Key（通过Header，避免URL泄露）
            try {
                const response = await fetch('/api/stats', {
                    headers: {
                        'Authorization': 'Bearer ' + apiKey
                    }
                });
                if (response.ok) {
                    // 设置Cookie（HttpOnly通过服务器设置更安全，但这里客户端设置也可以）
                    document.cookie = 'stats_api_key=' + apiKey + '; path=/; max-age=2592000; SameSite=Strict';
                    // 保存到localStorage作为备份
                    localStorage.setItem('stats_api_key', apiKey);
                    // 跳转到统计页面（带临时token用于首次验证）
                    const tempToken = Date.now() + '_' + Math.random().toString(36).substr(2, 9);
                    window.location.href = '/stats?temp=' + encodeURIComponent(tempToken);
                } else {
                    errorMsg.classList.add('show');
                    apiKeyInput.value = '';
                    apiKeyInput.focus();
                }
            } catch (error) {
                errorMsg.textContent = '网络错误，请重试';
                errorMsg.classList.add('show');
            }
        });

        // 如果Cookie中有API Key，自动尝试登录
        const cookies = document.cookie.split(';').reduce((acc, cookie) => {
            const [key, value] = cookie.trim().split('=');
            acc[key] = value;
            return acc;
        }, {});
        
        if (cookies.stats_api_key) {
            // Cookie会自动发送，直接跳转（带临时token）
            const tempToken = Date.now() + '_' + Math.random().toString(36).substr(2, 9);
            window.location.href = '/stats?temp=' + encodeURIComponent(tempToken);
        } else if (localStorage.getItem('stats_api_key')) {
            // 尝试用localStorage的key重新设置Cookie
            const savedKey = localStorage.getItem('stats_api_key');
            fetch('/api/stats', {
                headers: {
                    'Authorization': 'Bearer ' + savedKey
                }
            }).then(response => {
                if (response.ok) {
                    document.cookie = 'stats_api_key=' + savedKey + '; path=/; max-age=2592000; SameSite=Strict';
                    const tempToken = Date.now() + '_' + Math.random().toString(36).substr(2, 9);
                    window.location.href = '/stats?temp=' + encodeURIComponent(tempToken);
                }
            });
        }
    </script>
</body>
</html>
"""


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
    
    # 从环境变量读取 API 密钥（逗号分隔）
    api_keys_env = os.getenv("API_KEYS", "")
    if api_keys_env:
        api_keys = [key.strip() for key in api_keys_env.split(",") if key.strip()]
        print(f"🔐 API 密钥验证已启用 ({len(api_keys)} 个密钥)")
    else:
        api_keys = []
        print("⚠️ API 密钥验证未启用（未设置 API_KEYS 环境变量）")
    
    app.add_middleware(APIKeyAuthMiddleware, api_keys=api_keys)
    
    # 添加连接兼容性中间件
    app.add_middleware(ConnectionCompatibilityMiddleware)
    
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # 允许所有来源（生产环境建议限制具体域名）
        allow_credentials=True,  # 允许发送Cookie
        allow_methods=["GET", "POST", "OPTIONS"],  # 明确指定允许的方法
        allow_headers=["*"],  # 允许所有请求头
        expose_headers=["*"],  # 暴露所有响应头给客户端
    )
    
    @app.get("/")
    async def root():
        """根路径重定向到统计页面"""
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url="/stats")
    
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
    
    @app.get("/api/stats")
    async def get_stats():
        """获取每日统计数据"""
        try:
            daily_stats = stats_manager.get_daily_stats()
            return {
                "success": True,
                "data": daily_stats
            }
        except Exception as e:
            print(f"⚠️ 获取统计数据失败: {e}")
            raise HTTPException(status_code=500, detail={"error": str(e)})
    
    @app.get("/stats")
    async def stats_page():
        """统计页面"""
        # 获取项目根目录
        current_file = os.path.abspath(__file__)
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(current_file)))
        stats_html_path = os.path.join(project_root, "static", "stats.html")
        
        print(f"🔍 查找统计页面: {stats_html_path}")
        print(f"   文件存在: {os.path.exists(stats_html_path)}")
        
        if os.path.exists(stats_html_path):
            return FileResponse(stats_html_path, media_type="text/html")
        else:
            return Response(
                content=f"统计页面未找到。查找路径: {stats_html_path}",
                status_code=404,
                media_type="text/plain"
            )
    
    return app