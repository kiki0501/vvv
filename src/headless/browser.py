"""
无头浏览器管理模块

使用 Playwright 管理无头 Chrome 浏览器实例。
包含增强的反检测和指纹伪装功能。
"""

import asyncio
from typing import Optional, Callable
from pathlib import Path

try:
    from playwright.async_api import async_playwright, Browser, Page, BrowserContext
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

from .stealth import StealthConfig
from .terms_handler import TermsHandler


class HeadlessBrowser:
    """无头浏览器管理器 - 增强反检测版本"""
    
    # Vertex AI Studio URL
    VERTEX_AI_URL = "https://console.cloud.google.com/vertex-ai/studio/multimodal?mode=prompt&model=gemini-2.5-flash-lite-preview-09-2025"
    
    # 用户数据目录 (保存登录态)
    USER_DATA_DIR = "config/browser_data"
    
    def __init__(self):
        self.playwright = None
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self._is_running = False
        
        # 使用组合模式集成功能模块
        self._stealth_config = StealthConfig()
        self._terms_handler = TermsHandler()
    
    @staticmethod
    def check_availability() -> bool:
        """检查 Playwright 是否可用"""
        if not PLAYWRIGHT_AVAILABLE:
            print("❌ Playwright 未安装，请运行: pip install playwright && playwright install chromium")
            return False
        return True
    
    async def start(self, headless: bool = True) -> bool:
        """
        启动浏览器
        
        Args:
            headless: 是否无头模式 (调试时可设为 False)
        """
        if not self.check_availability():
            return False
        
        try:
            print("🌐 正在启动浏览器 (增强反检测模式)...")
            
            # 确保用户数据目录存在
            user_data_path = Path(self.USER_DATA_DIR)
            user_data_path.mkdir(parents=True, exist_ok=True)
            
            self.playwright = await async_playwright().start()
            
            # 随机选择分辨率和 User-Agent
            resolution = StealthConfig.get_random_resolution()
            user_agent = StealthConfig.get_random_user_agent()
            
            # 获取增强的启动参数
            launch_args = StealthConfig.get_stealth_args(headless)
            
            # 需要忽略的默认参数（这些会暴露自动化特征）
            ignore_args = StealthConfig.get_ignore_args(headless)
            
            self.context = await self.playwright.chromium.launch_persistent_context(
                user_data_dir=str(user_data_path),
                headless=headless,
                # 随机化视口大小
                viewport=resolution,
                screen=resolution,
                device_scale_factor=1.0,
                # 随机化 User-Agent
                user_agent=user_agent,
                # 增强启动参数
                args=launch_args,
                ignore_default_args=ignore_args,
                # 设置区域和语言
                locale="en-US",
                timezone_id="America/New_York",
                # 颜色方案
                color_scheme="light",
                # 减少动画 (性能优化)
                reduced_motion="reduce",
            )
            
            # 获取或创建页面
            if self.context.pages:
                self.page = self.context.pages[0]
            else:
                self.page = await self.context.new_page()
            
            # 设置条款处理器的页面
            self._terms_handler.set_page(self.page)
            
            # 注入反检测脚本 (在页面加载前)
            await self._inject_stealth_scripts()
            
            self._is_running = True
            print(f"✅ 浏览器已启动 (分辨率: {resolution['width']}x{resolution['height']})")
            return True
            
        except Exception as e:
            print(f"❌ 浏览器启动失败: {e}")
            return False
    
    async def _inject_stealth_scripts(self) -> None:
        """注入反检测脚本到所有页面"""
        if not self.context:
            return
        
        stealth_script = StealthConfig.get_stealth_script()
        
        # 为新页面自动注入脚本
        await self.context.add_init_script(stealth_script)
        
        # 为已存在的页面注入脚本
        for page in self.context.pages:
            try:
                await page.add_init_script(stealth_script)
            except Exception:
                pass
        
        print("🛡️ 反检测脚本已注入")
    
    async def navigate_to_vertex(self) -> bool:
        """导航到 Vertex AI Studio - 优化版本，支持条款并行检测"""
        if not self.page:
            print("❌ 浏览器未启动")
            return False
        
        try:
            print(f"🔗 正在导航到 Vertex AI Studio...")
            
            # 创建条款处理任务（并行运行）
            terms_task = asyncio.create_task(self._terms_handler.parallel_handler())
            
            # 使用较短的等待策略，不等待networkidle
            try:
                # 先等待DOM加载完成
                await self.page.goto(self.VERTEX_AI_URL, wait_until="domcontentloaded", timeout=30000)
                
                # 检查是否需要登录
                current_url = self.page.url
                if "accounts.google.com" in current_url:
                    print("⚠️ 需要登录 Google 账号")
                    print("   请在浏览器中完成登录，然后重新运行")
                    # 等待用户登录 (最多5分钟)
                    try:
                        await self.page.wait_for_url("**/vertex-ai/**", timeout=300000)
                        print("✅ 登录成功")
                    except:
                        print("❌ 登录超时")
                        terms_task.cancel()
                        return False
                
                # 等待页面进一步加载（但不要求networkidle）
                await asyncio.sleep(3)
                
            except Exception as e:
                # 如果是超时错误，检查是否因为条款对话框导致
                print(f"⚠️ 初始导航遇到问题: {e}")
                # 继续执行，可能条款对话框已经在处理中
            
            # 等待条款处理任务完成或超时
            try:
                await asyncio.wait_for(terms_task, timeout=15)
            except asyncio.TimeoutError:
                print("⚠️ 条款并行处理超时，尝试最终检测...")
                terms_task.cancel()
            except asyncio.CancelledError:
                pass
            
            # 最终一次条款检测
            await self._terms_handler.accept_terms_if_present()
            
            print("✅ 已到达 Vertex AI Studio")
            
            # 启动条款监控（用于后续可能出现的条款）
            await self.start_terms_monitoring()
            
            return True
            
        except Exception as e:
            print(f"❌ 导航失败: {e}")
            return False
    
    async def start_terms_monitoring(self, check_interval: float = 1.0) -> None:
        """
        启动条款监控任务 - 优化版本
        
        Args:
            check_interval: 备用定时检查间隔（秒），默认1秒
        """
        await self._terms_handler.start_monitoring(
            check_interval=check_interval,
            is_running_check=lambda: self._is_running
        )
    
    async def check_and_accept_terms(self) -> bool:
        """
        公开方法：检查并同意条款
        
        可以在需要时手动调用此方法
        """
        return await self._terms_handler.check_and_accept_terms()
    
    async def setup_request_interception(self, on_request: Callable) -> None:
        """
        设置请求拦截
        
        Args:
            on_request: 请求回调函数
        """
        if not self.page:
            return
        
        async def handle_request(request):
            url = request.url
            # 只关注 Vertex AI 相关请求
            if "batchGraphql" in url or "StreamGenerateContent" in url:
                await on_request(request)
        
        self.page.on("request", handle_request)
        print("🔍 请求拦截已设置")
    
    async def send_test_message(self, max_retries: int = 3) -> bool:
        """
        发送测试消息触发 API 请求 - 增强版本
        
        支持自动关闭 overlay 遮罩，多次重试
        
        Args:
            max_retries: 最大重试次数
            
        Returns:
            是否成功发送
        """
        if not self.page:
            return False
        
        for attempt in range(max_retries):
            try:
                if attempt > 0:
                    print(f"💬 重试发送测试消息 ({attempt + 1}/{max_retries})...")
                else:
                    print("💬 正在发送测试消息...")
                
                # 1. 先关闭任何可能存在的 overlay 遮罩层
                await self._dismiss_overlays()
                
                # 2. 等待输入框出现
                input_selector = 'textarea[aria-label*="message"], div[contenteditable="true"], textarea[placeholder*="message"], textarea[placeholder*="消息"]'
                try:
                    await self.page.wait_for_selector(input_selector, timeout=10000)
                except Exception:
                    # 如果等待超时，尝试刷新页面
                    if attempt < max_retries - 1:
                        print("   ⚠️ 输入框未出现，尝试刷新页面...")
                        await self._safe_reload()
                        continue
                    raise
                
                # 3. 使用 JavaScript 直接聚焦和输入（绕过 overlay 问题）
                success = await self.page.evaluate('''() => {
                    // 关闭所有 overlay
                    const overlays = document.querySelectorAll('.cdk-overlay-backdrop, .cdk-overlay-container > *');
                    overlays.forEach(el => {
                        if (el.classList.contains('cdk-overlay-backdrop')) {
                            el.click();  // 点击背景关闭
                        }
                    });
                    
                    // 查找输入框
                    const selectors = [
                        'textarea[aria-label*="message"]',
                        'div[contenteditable="true"]',
                        'textarea[placeholder*="message"]',
                        'textarea[placeholder*="消息"]'
                    ];
                    
                    let input = null;
                    for (const sel of selectors) {
                        input = document.querySelector(sel);
                        if (input && input.offsetParent !== null) break;
                        input = null;
                    }
                    
                    if (!input) return false;
                    
                    // 聚焦输入框
                    input.focus();
                    
                    // 设置内容
                    if (input.tagName === 'TEXTAREA') {
                        input.value = 'hi';
                        input.dispatchEvent(new Event('input', { bubbles: true }));
                    } else {
                        // contenteditable
                        input.textContent = 'hi';
                        input.dispatchEvent(new InputEvent('input', { bubbles: true, data: 'hi' }));
                    }
                    
                    return true;
                }''')
                
                if not success:
                    if attempt < max_retries - 1:
                        print("   ⚠️ 无法设置输入内容，重试中...")
                        await asyncio.sleep(1)
                        continue
                    print("❌ 未找到可用的输入框")
                    return False
                
                await asyncio.sleep(0.1)
                
                # 4. 按回车发送
                await self.page.keyboard.press("Enter")
                print("✅ 测试消息已发送")
                return True
                
            except Exception as e:
                error_msg = str(e)
                if "intercepts pointer events" in error_msg and attempt < max_retries - 1:
                    print(f"   ⚠️ 检测到 overlay 遮挡，尝试关闭...")
                    await self._dismiss_overlays()
                    await asyncio.sleep(0.5)
                    continue
                elif attempt < max_retries - 1:
                    print(f"   ⚠️ 发送失败: {error_msg[:50]}，重试中...")
                    await asyncio.sleep(1)
                    continue
                else:
                    print(f"❌ 发送消息失败: {e}")
                    return False
        
        return False
    
    async def _dismiss_overlays(self) -> None:
        """
        关闭页面上的 overlay 遮罩层
        
        处理 Google Cloud Console 常见的 overlay 类型：
        - cdk-overlay-backdrop (Material Design 对话框背景)
        - 模态对话框
        - 通知弹窗
        """
        if not self.page:
            return
        
        try:
            await self.page.evaluate('''() => {
                // 1. 点击所有 backdrop 关闭对话框
                const backdrops = document.querySelectorAll('.cdk-overlay-backdrop');
                backdrops.forEach(backdrop => {
                    if (backdrop.offsetParent !== null) {
                        backdrop.click();
                    }
                });
                
                // 2. 按 Escape 键关闭任何模态
                document.dispatchEvent(new KeyboardEvent('keydown', {
                    key: 'Escape',
                    code: 'Escape',
                    keyCode: 27,
                    which: 27,
                    bubbles: true
                }));
                
                // 3. 移除阻挡的 overlay 容器内容（最后手段）
                const overlayContainer = document.querySelector('.cdk-overlay-container');
                if (overlayContainer) {
                    // 检查是否有活跃的 backdrop
                    const activeBackdrop = overlayContainer.querySelector('.cdk-overlay-backdrop-showing');
                    if (activeBackdrop) {
                        // 尝试找到并点击关闭按钮
                        const closeButtons = overlayContainer.querySelectorAll(
                            'button[aria-label*="close"], button[aria-label*="Close"], ' +
                            'button[aria-label*="关闭"], .mat-dialog-close, ' +
                            'button.close, [mat-dialog-close]'
                        );
                        closeButtons.forEach(btn => btn.click());
                    }
                }
            }''')
            
            # 等待 overlay 动画完成
            await asyncio.sleep(0.3)
            
        except Exception as e:
            print(f"   ⚠️ 关闭 overlay 时出错: {e}")
    
    async def _safe_reload(self) -> bool:
        """
        安全地刷新页面
        
        Returns:
            是否成功刷新
        """
        if not self.page:
            return False
        
        try:
            print("   🔄 正在刷新页面...")
            await self.page.reload(wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(2)  # 等待页面稳定
            
            # 检查并处理可能出现的条款对话框
            await self._terms_handler.accept_terms_if_present()
            
            return True
        except Exception as e:
            print(f"   ⚠️ 页面刷新失败: {e}")
            return False
    
    async def close(self) -> None:
        """关闭浏览器"""
        self._is_running = False
        
        if self.context:
            await self.context.close()
            self.context = None
            self.page = None
        
        if self.playwright:
            await self.playwright.stop()
            self.playwright = None
        
        print("🔒 浏览器已关闭")
    
    @property
    def is_running(self) -> bool:
        return self._is_running
    
    # ========== 向后兼容的属性和方法 ==========
    
    @property
    def COMMON_RESOLUTIONS(self):
        """向后兼容：常见分辨率"""
        return StealthConfig.COMMON_RESOLUTIONS
    
    @property
    def COMMON_USER_AGENTS(self):
        """向后兼容：常见 User-Agent"""
        return StealthConfig.COMMON_USER_AGENTS
    
    def _get_stealth_args(self, headless: bool) -> list:
        """向后兼容：获取反检测参数"""
        return StealthConfig.get_stealth_args(headless)
    
    def _get_stealth_script(self) -> str:
        """向后兼容：获取反检测脚本"""
        return StealthConfig.get_stealth_script()
    
    async def _accept_terms_if_present(self) -> bool:
        """向后兼容：检测并同意条款"""
        return await self._terms_handler.accept_terms_if_present()
    
    async def _setup_terms_observer(self) -> None:
        """向后兼容：设置条款监听器"""
        await self._terms_handler.setup_observer()
    
    async def _setup_terms_observer_fast(self) -> None:
        """向后兼容：设置快速条款监听器"""
        await self._terms_handler.setup_observer_fast()
    
    async def _on_terms_detected(self) -> None:
        """向后兼容：条款检测回调"""
        self._terms_handler.on_terms_detected()