"""模型配置构建器"""

import json
from typing import Dict, Any, Optional, Tuple

from src.core import MODELS_CONFIG_FILE


class ModelConfigBuilder:
    """解析模型名称、处理后缀、构建生成配置"""
    
    def __init__(self):
        self.model_map = {}
        self._load_model_map()
    
    def _load_model_map(self) -> None:
        try:
            with open(MODELS_CONFIG_FILE, 'r', encoding='utf-8') as f:
                config = json.load(f)
                self.model_map = config.get('alias_map', {})
        except Exception as e:
            print(f"⚠️ 加载 models.json 失败: {e}")
    
    def parse_model_name(self, model: str) -> Tuple[str, Optional[str], Optional[str]]:
        """解析模型名称，返回 (backend_model, thinking_mode, resolution_mode)"""
        target_model = self.model_map.get(model, model)
        
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
        
        return target_model, thinking_mode, resolution_mode
    
    def build_generation_config(
        self,
        gen_config: Dict[str, Any],
        target_model: str,
        thinking_mode: Optional[str],
        resolution_mode: Optional[str],
        **kwargs
    ) -> Dict[str, Any]:
        """构建生成配置"""
        if thinking_mode:
            gen_config['thinkingConfig'] = {"includeThoughts": True}
            if thinking_mode == 'low':
                budget = 8192
            elif thinking_mode == 'high':
                budget = 32768
            else:
                budget = 8192
            
            gen_config['thinkingConfig']['budget_token_count'] = budget
            gen_config['thinkingConfig']['thinkingBudget'] = budget
            print(f"ℹ️ 思考模式: {thinking_mode}, 预算: {budget}")
        
        elif 'gemini-3-pro' in target_model and 'max_tokens' in kwargs and kwargs['max_tokens'] is not None:
            budget = int(kwargs['max_tokens'])
            gen_config['thinkingConfig'] = {
                "includeThoughts": True,
                "budget_token_count": budget,
                "thinkingBudget": budget
            }
            print(f"ℹ️ 思考模式 (自定义): 预算={budget}")
        
        if "image" in target_model:
            if 'responseModalities' not in gen_config:
                gen_config['responseModalities'] = ["TEXT", "IMAGE"]
            if 'imageConfig' not in gen_config:
                gen_config['imageConfig'] = {}
            
            gen_config['imageConfig']['personGeneration'] = "ALLOW_ALL"
            if 'imageOutputOptions' not in gen_config['imageConfig']:
                gen_config['imageConfig']['imageOutputOptions'] = {"mimeType": "image/png"}

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
                gen_config['imageConfig'].pop('imageSize', None)
                print(f"ℹ️ 图像生成: 默认尺寸")
        
        if not thinking_mode:
            gen_config.pop('thinkingConfig', None)
            gen_config.pop('thinking_config', None)
        
        if "image" not in target_model:
            gen_config.pop('imageConfig', None)
            gen_config.pop('sampleImageSize', None)
            gen_config.pop('width', None)
            gen_config.pop('height', None)
        
        if isinstance(gen_config, dict):
            if 'maxOutputTokens' in gen_config:
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
        
        return gen_config
    
    def build_safety_settings(self) -> list:
        """构建安全设置"""
        return [
            {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_CIVIC_INTEGRITY", "threshold": "BLOCK_NONE"}
        ]