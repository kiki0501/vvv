"""
websocket - WebSocket 通信模块

提供与浏览器 Harvester 脚本的 WebSocket 通信功能，
用于凭证收集和 Token 刷新。

主要组件:
- harvester_clients: 存储连接的客户端集合
- websocket_handler: WebSocket 连接处理函数
- request_token_refresh: 请求 Token 刷新函数
- init_websocket_handler: 初始化函数（设置 CredentialManager 依赖）
"""

from .handler import (
    harvester_clients,
    websocket_handler,
    request_token_refresh,
    init_websocket_handler
)

__all__ = [
    "harvester_clients",
    "websocket_handler",
    "request_token_refresh",
    "init_websocket_handler"
]