"""配置加载"""

import json
import os
from typing import Dict, Any, Optional

from .constants import MODELS_CONFIG_FILE, CONFIG_FILE


def build_model_maps() -> Dict[str, Dict[str, Any]]:
    """解析models.json，创建模型映射"""
    model_to_backend_map = {}
    try:
        with open(MODELS_CONFIG_FILE, 'r', encoding='utf-8') as f:
            config = json.load(f)
            models = config.get('models', [])
            for model_name in models:
                base_name = model_name
                resolution = None
                thinking = None

                if model_name.endswith("-1k"):
                    base_name = model_name[:-3]
                    resolution = "1k"
                elif model_name.endswith("-2k"):
                    base_name = model_name[:-3]
                    resolution = "2k"
                elif model_name.endswith("-4k"):
                    base_name = model_name[:-3]
                    resolution = "4k"
                
                if base_name.endswith("-low"):
                    base_name = base_name[:-4]
                    thinking = "low"
                elif base_name.endswith("-high"):
                    base_name = base_name[:-5]
                    thinking = "high"

                model_to_backend_map[model_name] = {
                    "backend_model": base_name,
                    "resolution": resolution,
                    "thinking": thinking
                }
    except Exception as e:
        print(f"⚠️ 构建模型映射失败: {e}")
    return model_to_backend_map


def load_config() -> Dict[str, Any]:
    """加载配置"""
    default_config = {
        "enable_sd_api": True,
        "enable_gui": True,
        "credential_mode": "headful",
        "headless": {
            "browser": "playwright",
            "auto_refresh_interval": 180,
            "show_browser": False
        }
    }
    if not os.path.exists(CONFIG_FILE):
        return default_config
    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            config = json.load(f)
            if "headless" in config:
                default_config["headless"].update(config["headless"])
                del config["headless"]
            default_config.update(config)
            return default_config
    except Exception as e:
        print(f"⚠️ 加载配置失败: {e}")
        return default_config