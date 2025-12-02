"""Vertex AI Proxy 入口"""
import asyncio
import uvicorn
import websockets

from src.core import (
    load_config,
    TokenStatsManager,
    CredentialManager,
    PORT_API,
    PORT_WS
)
from src.api import VertexAIClient, create_app
from src.websocket import (
    init_websocket_handler,
    websocket_handler,
    request_token_refresh
)


# --- 全局实例 ---
stats_manager = TokenStatsManager()
cred_manager = CredentialManager()


# 全局浏览器实例（用于无头模式的按需刷新）
_headless_browser = None
# 连续失败计数器（用于错误处理重定向）
_refresh_fail_count = 0
# 触发重定向的失败阈值
_REDIRECT_THRESHOLD = 2


async def headless_token_refresh() -> None:
    """无头模式凭证刷新，连续失败时重定向到 Vertex AI Studio"""
    global _headless_browser, _refresh_fail_count
    
    if _headless_browser and _headless_browser.is_running:
        print("🔄 无头模式: 按需刷新凭证...")
        
        try:
            # 记录刷新前的凭证时间戳
            old_timestamp = cred_manager.last_updated
            
            # 先尝试关闭任何可能的 overlay
            await _headless_browser._dismiss_overlays()
            
            success = await _headless_browser.send_test_message()
            if success:
                # 等待凭证实际更新（最多等待 5 秒）
                for _ in range(10):
                    await asyncio.sleep(0.5)
                    if cred_manager.last_updated > old_timestamp:
                        print("✅ 无头模式: 凭证已更新")
                        _refresh_fail_count = 0
                        # 关键：主动通知所有等待者刷新已完成
                        cred_manager.refresh_event.set()
                        cred_manager.refresh_complete_event.set()
                        return  # 成功，直接返回
                
                # send_test_message 成功但凭证未更新，可能被 recaptcha 拦截
                print("⚠️ 无头模式: 消息已发送但凭证未更新")
                # 标记失败，解除等待
                cred_manager.mark_refresh_failed()
            
            # 失败处理
            _refresh_fail_count += 1
            print(f"❌ 无头模式: 凭证刷新失败 (连续失败 {_refresh_fail_count}/{_REDIRECT_THRESHOLD})")
            
            # 连续失败达到阈值，尝试多种恢复策略
            if _refresh_fail_count >= _REDIRECT_THRESHOLD:
                print("🔄 无头模式: 重复失败，尝试恢复...")
                _refresh_fail_count = 0  # 重置计数
                
                recovered = False
                
                # 策略1: 先尝试刷新当前页面
                try:
                    print("   📍 策略1: 刷新当前页面...")
                    if _headless_browser.page:
                        await _headless_browser._dismiss_overlays()
                        await _headless_browser.page.reload(wait_until="domcontentloaded", timeout=15000)
                        await asyncio.sleep(2)
                        await _headless_browser._dismiss_overlays()
                        
                        retry_success = await _headless_browser.send_test_message()
                        if retry_success:
                            print("   ✅ 页面刷新后恢复成功")
                            recovered = True
                except Exception as e:
                    print(f"   ⚠️ 页面刷新失败: {str(e)[:50]}")
                
                # 策略2: 重定向到 Vertex AI Studio
                if not recovered:
                    try:
                        print("   📍 策略2: 重定向到 Vertex AI Studio...")
                        if _headless_browser.page:
                            await _headless_browser.page.goto(
                                _headless_browser.VERTEX_AI_URL,
                                wait_until="domcontentloaded",
                                timeout=30000
                            )
                            print("   ✅ 已重定向，等待页面加载...")
                            await asyncio.sleep(3)
                            
                            # 处理可能出现的条款对话框
                            await _headless_browser.check_and_accept_terms()
                            await _headless_browser._dismiss_overlays()
                            
                            retry_success = await _headless_browser.send_test_message()
                            if retry_success:
                                print("   ✅ 重定向后恢复成功")
                                recovered = True
                            else:
                                print("   ⚠️ 重定向后仍然失败")
                    except Exception as e:
                        print(f"   ⚠️ 重定向失败: {str(e)[:50]}")
                
                # 所有策略失败，标记刷新失败以解除等待
                if not recovered:
                    print("⚠️ 无头模式: 所有恢复策略失败，标记刷新失败")
                    cred_manager.mark_refresh_failed()
            else:
                # 未达到阈值，也标记失败以解除当前请求的等待
                cred_manager.mark_refresh_failed()
                
        except Exception as e:
            print(f"❌ 无头模式: 凭证刷新异常: {e}")
            _refresh_fail_count += 1
            cred_manager.mark_refresh_failed()
    else:
        print("⚠️ 无头模式: 浏览器未运行，无法刷新凭证")
        cred_manager.mark_refresh_failed()


async def start_headless_mode(config: dict) -> None:
    """启动无头浏览器模式"""
    global _headless_browser
    
    try:
        from src.headless import HeadlessBrowser, CredentialHarvester
    except ImportError as e:
        print(f"❌ 无法导入无头模块: {e}")
        print("   请确保已安装 playwright: pip install playwright && playwright install chromium")
        return
    
    headless_config = config.get("headless", {})
    show_browser = headless_config.get("show_browser", False)
    
    print("🤖 无头模式启动中...")
    
    # 创建浏览器实例并保存全局引用
    browser = HeadlessBrowser()
    _headless_browser = browser
    
    def on_credentials(data):
        cred_manager.update(data)
        cred_manager.refresh_complete_event.set()
    
    harvester = CredentialHarvester(on_credentials=on_credentials)
    
    # 启动浏览器
    if not await browser.start(headless=not show_browser):
        print("❌ 无头浏览器启动失败")
        _headless_browser = None
        return
    
    # 设置请求拦截
    await browser.setup_request_interception(harvester.handle_request)
    
    # 导航到 Vertex AI
    if not await browser.navigate_to_vertex():
        print("❌ 无法访问 Vertex AI Studio")
        await browser.close()
        _headless_browser = None
        return
    
    print("🔄 无头模式: 获取初始凭证...")
    await browser.send_test_message()
    
    print("✅ 无头模式已就绪 (按需刷新)")
    
    # 保持浏览器运行
    try:
        while browser.is_running:
            await asyncio.sleep(1)
    finally:
        await browser.close()
        _headless_browser = None


async def main():
    """启动服务器"""
    config = load_config()
    credential_mode = config.get("credential_mode", "headful")
    
    print(f"\n📋 凭证模式: {credential_mode}")
    
    init_websocket_handler(cred_manager)
    
    if credential_mode == "headless":
        refresh_callback = headless_token_refresh
    else:
        refresh_callback = request_token_refresh
    
    vertex_client = VertexAIClient(
        cred_manager=cred_manager,
        stats_manager=stats_manager,
        request_token_refresh_callback=refresh_callback
    )
    
    app = create_app(vertex_client, stats_manager)
    
    if config.get("enable_sd_api", False):
        try:
            from src.api import sd_api_compat
            sd_api_compat.vertex_client = vertex_client
            app.include_router(sd_api_compat.router)
            print("✅ SD API 兼容模块已加载")
        except ImportError:
            print("⚠️ 无法导入 src.api.sd_api_compat")
    
    tasks = []
    
    if credential_mode == "headful":
        print("🌐 有头模式: 等待浏览器脚本连接...")
        ws_server = websockets.serve(websocket_handler, "0.0.0.0", PORT_WS)
        tasks.append(ws_server)
        
    elif credential_mode == "headless":
        print("🤖 无头模式: 自动获取凭证...")
        tasks.append(asyncio.create_task(start_headless_mode(config)))
        
    elif credential_mode == "manual":
        print("📄 手动模式: 使用已保存的凭证")
        if not cred_manager.get_credentials():
            print("⚠️ 未找到凭证文件，请先运行有头模式获取凭证")
    
    uvicorn_config = uvicorn.Config(app, host="0.0.0.0", port=PORT_API, log_level="info")
    server = uvicorn.Server(uvicorn_config)
    
    print(f"\n🚀 代理服务器已启动")
    print(f"   - API: http://0.0.0.0:{PORT_API}")
    if credential_mode == "headful":
        print(f"   - WS:  ws://0.0.0.0:{PORT_WS}")
    
    tasks.append(server.serve())
    await asyncio.gather(*tasks)


if __name__ == "__main__":
    config = load_config()
    
    def server_runner():
        asyncio.run(main())
    
    if config.get("enable_gui", False):
        try:
            from src.gui import gui
            print("🖼️ GUI 模式启动中...")
            gui.run(server_runner, stats_manager)
        except Exception as e:
            print(f"⚠️ GUI 启动失败: {e}，回退到终端模式")
            server_runner()
    else:
        server_runner()