"""
浏览器反检测模块

提供浏览器指纹伪装和反自动化检测功能。
"""

import random
from typing import List, Dict


class StealthConfig:
    """反检测配置管理器"""
    
    # 常见的屏幕分辨率 (用于随机化)
    COMMON_RESOLUTIONS: List[Dict[str, int]] = [
        {"width": 1920, "height": 1080},
        {"width": 1366, "height": 768},
        {"width": 1536, "height": 864},
        {"width": 1440, "height": 900},
        {"width": 1280, "height": 800},
    ]
    
    # 常见的 User-Agent (Chrome 最新版本)
    COMMON_USER_AGENTS: List[str] = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    ]
    
    @classmethod
    def get_random_resolution(cls) -> Dict[str, int]:
        """获取随机分辨率"""
        return random.choice(cls.COMMON_RESOLUTIONS)
    
    @classmethod
    def get_random_user_agent(cls) -> str:
        """获取随机 User-Agent"""
        return random.choice(cls.COMMON_USER_AGENTS)
    
    @staticmethod
    def get_stealth_args(headless: bool) -> List[str]:
        """
        获取增强反检测的浏览器启动参数
        
        这些参数可以有效绕过大多数自动化检测
        
        Args:
            headless: 是否为无头模式
            
        Returns:
            浏览器启动参数列表
        """
        args = [
            # === 核心反检测参数 ===
            "--disable-blink-features=AutomationControlled",
            
            # === 性能和稳定性 ===
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu-sandbox",
            
            # === 隐藏自动化特征 ===
            "--disable-infobars",
            "--disable-background-networking",
            "--disable-background-timer-throttling",
            "--disable-backgrounding-occluded-windows",
            "--disable-breakpad",
            "--disable-component-extensions-with-background-pages",
            "--disable-component-update",
            "--disable-default-apps",
            "--disable-extensions",
            "--disable-features=TranslateUI",
            "--disable-hang-monitor",
            "--disable-ipc-flooding-protection",
            "--disable-popup-blocking",
            "--disable-prompt-on-repost",
            "--disable-renderer-backgrounding",
            "--disable-sync",
            "--metrics-recording-only",
            "--no-first-run",
            "--password-store=basic",
            "--use-mock-keychain",
            
            # === WebRTC 防泄露 ===
            "--disable-webrtc-hw-decoding",
            "--disable-webrtc-hw-encoding",
            "--disable-webrtc-multiple-routes",
            "--disable-webrtc-hw-vp8-encoding",
            "--enforce-webrtc-ip-permission-check",
            "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
            
            # === 内存和性能优化 ===
            "--memory-pressure-off",
        ]
        
        # 针对无头模式的特殊处理
        if headless:
            args.extend([
                "--headless=new",
                "--disable-software-rasterizer",
            ])
        
        return args
    
    @staticmethod
    def get_stealth_script() -> str:
        """
        获取注入页面的反检测 JavaScript 脚本
        
        这些脚本会伪装浏览器的各种属性，使其看起来像真实用户浏览器
        
        Returns:
            JavaScript 脚本字符串
        """
        return '''
        // === 核心反检测脚本 ===
        
        // 1. 移除 webdriver 标志
        Object.defineProperty(navigator, 'webdriver', {
            get: () => undefined,
            configurable: true
        });
        
        // 2. 伪造 plugins (真实浏览器有插件)
        Object.defineProperty(navigator, 'plugins', {
            get: () => {
                const plugins = [
                    {
                        name: 'Chrome PDF Plugin',
                        filename: 'internal-pdf-viewer',
                        description: 'Portable Document Format',
                        length: 1
                    },
                    {
                        name: 'Chrome PDF Viewer',
                        filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai',
                        description: '',
                        length: 1
                    },
                    {
                        name: 'Native Client',
                        filename: 'internal-nacl-plugin',
                        description: '',
                        length: 2
                    }
                ];
                plugins.item = (i) => plugins[i] || null;
                plugins.namedItem = (name) => plugins.find(p => p.name === name) || null;
                plugins.refresh = () => {};
                return plugins;
            },
            configurable: true
        });
        
        // 3. 伪造 languages
        Object.defineProperty(navigator, 'languages', {
            get: () => ['en-US', 'en', 'zh-CN', 'zh'],
            configurable: true
        });
        
        // 4. 伪造 platform
        Object.defineProperty(navigator, 'platform', {
            get: () => 'Win32',
            configurable: true
        });
        
        // 5. 伪造 hardwareConcurrency (CPU核心数)
        Object.defineProperty(navigator, 'hardwareConcurrency', {
            get: () => 8,
            configurable: true
        });
        
        // 6. 伪造 deviceMemory
        Object.defineProperty(navigator, 'deviceMemory', {
            get: () => 8,
            configurable: true
        });
        
        // 7. 伪造 maxTouchPoints
        Object.defineProperty(navigator, 'maxTouchPoints', {
            get: () => 0,
            configurable: true
        });
        
        // 8. 伪造 connection (网络信息)
        if (navigator.connection) {
            Object.defineProperty(navigator.connection, 'rtt', {
                get: () => 50,
                configurable: true
            });
        }
        
        // 9. 覆盖 chrome runtime (防止检测)
        window.chrome = {
            runtime: {
                onConnect: { addListener: () => {}, removeListener: () => {} },
                onMessage: { addListener: () => {}, removeListener: () => {} },
                sendMessage: () => {},
                connect: () => ({ onMessage: { addListener: () => {} }, postMessage: () => {}, disconnect: () => {} })
            },
            loadTimes: () => ({}),
            csi: () => ({})
        };
        
        // 10. 伪造 Permissions API
        const originalQuery = window.navigator.permissions?.query;
        if (originalQuery) {
            window.navigator.permissions.query = (parameters) => {
                if (parameters.name === 'notifications') {
                    return Promise.resolve({ state: Notification.permission });
                }
                return originalQuery.call(navigator.permissions, parameters);
            };
        }
        
        // 11. 伪造 WebGL 渲染器信息
        const getParameterProxyHandler = {
            apply: function(target, thisArg, args) {
                const param = args[0];
                
                // UNMASKED_VENDOR_WEBGL
                if (param === 37445) {
                    return 'Google Inc. (NVIDIA)';
                }
                // UNMASKED_RENDERER_WEBGL
                if (param === 37446) {
                    return 'ANGLE (NVIDIA, NVIDIA GeForce GTX 1080 Direct3D11 vs_5_0 ps_5_0, D3D11)';
                }
                
                return Reflect.apply(target, thisArg, args);
            }
        };
        
        // 应用到 WebGL 和 WebGL2
        ['WebGLRenderingContext', 'WebGL2RenderingContext'].forEach(contextName => {
            const Context = window[contextName];
            if (Context) {
                const getParameter = Context.prototype.getParameter;
                Context.prototype.getParameter = new Proxy(getParameter, getParameterProxyHandler);
            }
        });
        
        // 12. 伪造 screen 属性
        const screenProps = {
            colorDepth: 24,
            pixelDepth: 24
        };
        
        for (const [prop, value] of Object.entries(screenProps)) {
            try {
                Object.defineProperty(screen, prop, {
                    get: () => value,
                    configurable: true
                });
            } catch(e) {}
        }
        
        // 13. 阻止 Headless 检测
        Object.defineProperty(document, 'hidden', {
            get: () => false,
            configurable: true
        });
        
        Object.defineProperty(document, 'visibilityState', {
            get: () => 'visible',
            configurable: true
        });
        
        console.log('[Stealth] 反检测脚本已注入');
        '''
    
    @staticmethod
    def get_ignore_args(headless: bool) -> List[str]:
        """
        获取需要忽略的默认参数
        
        这些参数会暴露自动化特征
        
        Args:
            headless: 是否为无头模式
            
        Returns:
            需要忽略的参数列表
        """
        ignore_args = [
            "--enable-automation",
            "--enable-blink-features=IdleDetection",
        ]
        if headless:
            ignore_args.append("--headless")
        return ignore_args