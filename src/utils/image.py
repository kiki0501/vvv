"""
图片处理工具模块

从assistant消息中提取base64图片，转换为Vertex AI的inlineData格式
支持多轮图片编辑场景
"""

import re
from typing import Tuple, List, Dict, Any

# 匹配 ![Generated Image](data:image/xxx;base64,XXX) 格式
IMAGE_MARKDOWN_PATTERN = re.compile(
    r'!\[(?:Generated Image|[^\]]*)\]\((data:image/([a-zA-Z0-9+.-]+);base64,([A-Za-z0-9+/=]+))\)'
)

def extract_images_from_assistant_message(content: str) -> Tuple[str, List[Dict[str, Any]]]:
    """
    从assistant消息中提取base64图片，转换为Vertex AI的inlineData格式
    
    Args:
        content: assistant消息内容
        
    Returns:
        (清理后的文本, inlineData列表)
        - 清理后的文本：图片被替换为 [Image] 占位符
        - inlineData列表：提取的图片数据，格式为 {"inlineData": {"mimeType": "image/png", "data": "base64..."}}
    """
    inline_data_parts = []
    
    def replace_with_placeholder(match):
        full_data_url = match.group(1)
        mime_subtype = match.group(2)  # e.g., "png", "jpeg"
        base64_data = match.group(3)
        
        # 构建mimeType
        mime_type = f"image/{mime_subtype}"
        
        # 添加到inlineData列表
        inline_data_parts.append({
            "inlineData": {
                "mimeType": mime_type,
                "data": base64_data
            }
        })
        
        # 返回占位符，让模型知道这里曾有一张图片
        return f"[Image {len(inline_data_parts)}]"
    
    # 替换所有图片markdown为占位符，同时提取图片数据
    cleaned_text = IMAGE_MARKDOWN_PATTERN.sub(replace_with_placeholder, content)
    
    return cleaned_text, inline_data_parts