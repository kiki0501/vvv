"""
条款处理模块

提供自动检测和同意服务条款对话框的功能。
"""

import asyncio
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.async_api import Page


class TermsHandler:
    """条款处理器 - 自动检测和同意服务条款"""
    
    # 条款检测选择器
    TERMS_SELECTORS = [
        'p.notranslate',
        '[role="dialog"] p',
        '.mdc-dialog__content p',
        '[aria-modal="true"] p'
    ]
    
    # 复选框选择器
    CHECKBOX_SELECTORS = [
        'input.mdc-checkbox__native-control[type="checkbox"]',
        '[role="dialog"] input[type="checkbox"]',
        '.mdc-checkbox input[type="checkbox"]',
        'input[type="checkbox"][aria-label*="agree"]',
        'input[type="checkbox"][aria-label*="同意"]'
    ]
    
    # 同意按钮选择器
    BUTTON_SELECTORS = [
        'span.mdc-button__label:has-text("同意")',
        'span.mdc-button__label:has-text("Agree")',
        'span.mdc-button__label:has-text("Accept")',
        'button:has-text("同意")',
        'button:has-text("Agree")',
        'button:has-text("Accept")',
        '[role="dialog"] button[type="submit"]',
        '.mdc-dialog__actions button:last-child'
    ]
    
    # 快速检测选择器
    QUICK_CHECK_SELECTORS = [
        'p.notranslate',
        '[role="dialog"]',
        '.mdc-dialog',
        '[aria-modal="true"]'
    ]
    
    # 条款关键词
    TERMS_KEYWORDS = ['terms', 'agree', '条款', '同意', 'consent', 'accept']
    
    def __init__(self, page: Optional["Page"] = None):
        """
        初始化条款处理器
        
        Args:
            page: Playwright 页面对象
        """
        self.page = page
        self._observer_active = False
        self._terms_detected_event = asyncio.Event()
    
    def set_page(self, page: "Page") -> None:
        """设置页面对象"""
        self.page = page
        self._observer_active = False
    
    async def setup_observer(self) -> None:
        """
        设置 MutationObserver 监听条款对话框（兼容版本）
        """
        await self.setup_observer_fast()
    
    async def setup_observer_fast(self) -> None:
        """
        设置快速响应的 MutationObserver 监听条款对话框
        
        优化：移除限流，直接响应DOM变化
        """
        if not self.page or self._observer_active:
            return
        
        try:
            # 注入优化的 MutationObserver 脚本
            await self.page.evaluate('''() => {
                // 避免重复设置
                if (window.__termsObserverActive) return;
                window.__termsObserverActive = true;
                
                // 条款对话框的多种选择器（覆盖不同情况）
                const termsSelectors = [
                    'p.notranslate',
                    '[role="dialog"]',
                    '.mdc-dialog',
                    '[aria-modal="true"]',
                    '.terms-dialog',
                    '.consent-dialog'
                ];
                
                // 检查是否是条款对话框
                const isTermsDialog = (element) => {
                    if (!element) return false;
                    const text = element.textContent?.toLowerCase() || '';
                    const keywords = ['terms', 'agree', '条款', '同意', 'consent', 'accept'];
                    return keywords.some(k => text.includes(k));
                };
                
                // 检查函数 - 立即执行版本
                const checkForTerms = () => {
                    for (const selector of termsSelectors) {
                        const elements = document.querySelectorAll(selector);
                        for (const el of elements) {
                            if (isTermsDialog(el) && el.offsetParent !== null) {
                                // 找到可见的条款对话框，立即触发自定义事件
                                window.dispatchEvent(new CustomEvent('termsDialogDetected', {
                                    detail: { element: el }
                                }));
                                return true;
                            }
                        }
                    }
                    return false;
                };
                
                // 立即检查一次
                checkForTerms();
                
                // 设置快速响应的 MutationObserver（无限流）
                const observer = new MutationObserver((mutations) => {
                    // 直接检查，不限流
                    // 使用 queueMicrotask 确保尽快执行但不阻塞
                    queueMicrotask(checkForTerms);
                });
                
                // 观察整个文档的变化
                observer.observe(document.body, {
                    childList: true,
                    subtree: true,
                    attributes: true,
                    attributeFilter: ['class', 'style', 'hidden', 'aria-hidden']
                });
                
                console.log('[Terms Observer] 快速条款监听器已启动');
            }''')
            
            self._observer_active = True
            print("👁️ 条款监听器已启动 (快速响应)")
        except Exception as e:
            print(f"⚠️ 设置条款监听器失败: {e}")
    
    def on_terms_detected(self) -> None:
        """条款对话框被检测到时的回调"""
        self._terms_detected_event.set()
    
    async def check_terms_present(self) -> bool:
        """
        快速检查是否存在条款对话框
        
        Returns:
            是否存在条款对话框
        """
        if not self.page:
            return False
        
        try:
            has_terms = await self.page.evaluate('''() => {
                const selectors = ['p.notranslate', '[role="dialog"]', '.mdc-dialog', '[aria-modal="true"]'];
                for (const sel of selectors) {
                    const el = document.querySelector(sel);
                    if (el && el.offsetParent !== null) {
                        const text = el.textContent?.toLowerCase() || '';
                        if (text.includes('terms') || text.includes('agree') ||
                            text.includes('条款') || text.includes('同意') ||
                            text.includes('consent') || text.includes('accept')) {
                            return true;
                        }
                    }
                }
                return false;
            }''')
            return has_terms
        except Exception:
            return False
    
    async def accept_terms_if_present(self) -> bool:
        """
        自动检测并同意条款 - 优化版本
        
        使用多种选择器策略，提高兼容性
        
        Returns:
            是否成功处理条款（或者不存在条款）
        """
        if not self.page:
            return False
        
        try:
            # 尝试查找条款元素
            terms_element = None
            for selector in self.TERMS_SELECTORS:
                terms_element = await self.page.query_selector(selector)
                if terms_element:
                    # 检查是否可见
                    is_visible = await terms_element.is_visible()
                    if is_visible:
                        break
                    terms_element = None
            
            if not terms_element:
                print("ℹ️ 未检测到条款对话框")
                return True
            
            print("📜 检测到条款对话框，正在自动同意...")
            
            # 1. 智能滚动条款内容到底部
            await self.page.evaluate('''() => {
                // 查找所有可能的滚动容器
                const scrollableSelectors = [
                    '.mdc-dialog__content',
                    '[role="dialog"] [style*="overflow"]',
                    '.terms-content',
                    '.consent-content'
                ];
                
                for (const selector of scrollableSelectors) {
                    const containers = document.querySelectorAll(selector);
                    for (const container of containers) {
                        const style = window.getComputedStyle(container);
                        if (style.overflow === 'auto' || style.overflow === 'scroll' ||
                            style.overflowY === 'auto' || style.overflowY === 'scroll') {
                            // 平滑滚动到底部
                            container.scrollTo({
                                top: container.scrollHeight,
                                behavior: 'smooth'
                            });
                        }
                    }
                }
                
                // 备选：查找条款文本并滚动
                const termsText = document.querySelector('p.notranslate');
                if (termsText) {
                    termsText.scrollIntoView({ block: 'end', behavior: 'smooth' });
                }
            }''')
            
            # 最小等待滚动完成
            await asyncio.sleep(0.1)
            print("   ✓ 已滚动到条款底部")
            
            # 2. 尝试勾选同意复选框（快速版本）
            checkbox = None
            for selector in self.CHECKBOX_SELECTORS:
                checkbox = await self.page.query_selector(selector)
                if checkbox:
                    is_visible = await checkbox.is_visible()
                    if is_visible:
                        break
                    checkbox = None
            
            if checkbox:
                is_checked = await checkbox.is_checked()
                if not is_checked:
                    # 直接点击，减少延迟
                    await checkbox.click()
                    await asyncio.sleep(0.05)
                print("   ✓ 已勾选同意复选框")
            else:
                print("   ℹ️ 未找到复选框（可能不需要）")
            
            # 3. 点击同意按钮（快速版本）
            agree_button = None
            for selector in self.BUTTON_SELECTORS:
                try:
                    agree_button = await self.page.query_selector(selector)
                    if agree_button:
                        is_visible = await agree_button.is_visible()
                        is_enabled = await agree_button.is_enabled()
                        if is_visible and is_enabled:
                            break
                        agree_button = None
                except Exception:
                    continue
            
            if agree_button:
                # 直接点击，最小化延迟
                await agree_button.click()
                await asyncio.sleep(0.2)
                print("   ✓ 已点击同意按钮")
            else:
                print("   ⚠️ 未找到同意按钮")
                return False
            
            print("✅ 条款已自动同意")
            return True
            
        except Exception as e:
            print(f"⚠️ 自动同意条款失败: {e}")
            return False
    
    async def check_and_accept_terms(self) -> bool:
        """
        公开方法：检查并同意条款
        
        可以在需要时手动调用此方法
        """
        return await self.accept_terms_if_present()
    
    async def start_monitoring(self, check_interval: float = 1.0, is_running_check: callable = None) -> None:
        """
        启动条款监控任务 - 优化版本
        
        使用更短的检查间隔和立即检测机制
        
        Args:
            check_interval: 备用定时检查间隔（秒），默认1秒
            is_running_check: 检查是否继续运行的回调函数
        """
        if not self.page:
            return
        
        # 设置优化的 MutationObserver
        await self.setup_observer_fast()
        
        async def monitor_loop():
            # 首次立即检查
            await self.accept_terms_if_present()
            
            while is_running_check is None or is_running_check():
                try:
                    # 等待事件或超时（缩短超时时间）
                    try:
                        await asyncio.wait_for(
                            self._terms_detected_event.wait(),
                            timeout=check_interval
                        )
                        # 事件触发，立即处理条款
                        self._terms_detected_event.clear()
                        await self.accept_terms_if_present()
                    except asyncio.TimeoutError:
                        # 超时后进行一次快速主动检查
                        has_terms = await self.check_terms_present()
                        if has_terms:
                            await self.accept_terms_if_present()
                            
                except Exception as e:
                    print(f"⚠️ 条款监控出错: {e}")
                    await asyncio.sleep(0.5)
        
        # 在后台运行监控任务
        asyncio.create_task(monitor_loop())
        print("🔄 条款监控任务已启动 (优化版)")
    
    async def parallel_handler(self, max_attempts: int = 30) -> bool:
        """
        并行处理条款的协程
        
        在导航过程中并行检测和处理条款
        
        Args:
            max_attempts: 最大检测次数
            
        Returns:
            是否成功处理条款
        """
        # 等待一小段时间让页面开始加载
        await asyncio.sleep(2)
        
        # 持续检测条款直到成功处理或超时
        for attempt in range(max_attempts):
            try:
                has_terms = await self.check_terms_present()
                
                if has_terms:
                    print("📜 并行检测到条款对话框，立即处理...")
                    success = await self.accept_terms_if_present()
                    if success:
                        print("✅ 条款已在导航过程中处理完成")
                        return True
            except Exception:
                # 页面可能还在加载中，忽略错误继续尝试
                pass
            
            await asyncio.sleep(1)  # 每秒检测一次
        
        return False