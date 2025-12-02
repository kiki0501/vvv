"""
WebSocket 处理模块

提供与浏览器 Harvester 脚本的 WebSocket 通信功能：
- 接收凭证更新
- 处理 Token 刷新请求
- 管理客户端连接
"""

import json
import time
import websockets
from typing import Set, Optional, Any

# 模块级变量
_cred_manager = None

# 存储连接的 harvester 客户端
harvester_clients: Set = set()


def init_websocket_handler(cred_manager) -> None:
    """
    初始化 WebSocket 处理器
    
    Args:
        cred_manager: CredentialManager 实例，用于凭证管理
    """
    global _cred_manager
    _cred_manager = cred_manager
    print("✅ WebSocket 处理器已初始化")


async def websocket_handler(websocket) -> None:
    """
    处理 WebSocket 连接
    
    处理来自 Harvester 脚本的消息：
    - credentials_harvested: 新凭证收集完成
    - token_refreshed: Token 已刷新
    - refresh_complete: 前端刷新完成
    - identify: 客户端身份标识
    
    Args:
        websocket: WebSocket 连接对象
    """
    global _cred_manager
    
    print("🔌 WebSocket 客户端已连接")
    harvester_clients.add(websocket)
    
    try:
        async for message in websocket:
            try:
                data = json.loads(message)
                msg_type = data.get("type")
                
                if msg_type == "credentials_harvested":
                    if _cred_manager:
                        _cred_manager.update(data.get("data"))
                    else:
                        print("⚠️ CredentialManager 未初始化")
                        
                elif msg_type == "token_refreshed":
                    if _cred_manager:
                        _cred_manager.update_token(data.get("token"))
                    else:
                        print("⚠️ CredentialManager 未初始化")
                        
                elif msg_type == "refresh_complete":
                    print("✅ 前端确认刷新完成")
                    if _cred_manager:
                        _cred_manager.refresh_complete_event.set()
                        
                elif msg_type == "identify":
                    client_id = data.get('client')
                    print(f"👋 客户端已识别: {client_id}")
                    # 发送握手确认
                    await websocket.send(json.dumps({
                        "type": "hello",
                        "message": "Connection established",
                        "server_time": time.time()
                    }))
                    
            except Exception as e:
                print(f"⚠️ WS 错误: {e}")
                
    except websockets.ConnectionClosed:
        print("🔌 WebSocket 客户端已断开")
        harvester_clients.discard(websocket)
        
    except Exception as e:
        print(f"⚠️ WS 处理器错误: {e}")
        harvester_clients.discard(websocket)


async def request_token_refresh() -> None:
    """
    向前端请求刷新 Token
    
    向所有连接的 Harvester 客户端广播刷新请求
    """
    print("🔄 正在请求前端刷新 Token...")
    
    if not harvester_clients:
        print("⚠️ 没有已连接的 Harvester 客户端!")
        return
    
    message = json.dumps({"type": "refresh_token"})
    
    # 广播到所有连接的 harvesters
    for ws in list(harvester_clients):
        try:
            await ws.send(message)
        except Exception as e:
            print(f"⚠️ 发送刷新请求失败: {e}")
            harvester_clients.discard(ws)