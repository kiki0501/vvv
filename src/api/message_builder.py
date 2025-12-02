"""消息构建器，OpenAI格式转Vertex AI格式"""

import json
from typing import Dict, Any, List, Tuple, Optional

from src.utils.image import extract_images_from_assistant_message


class MessageBuilder:
    """将OpenAI格式消息转换为Vertex AI格式"""
    
    def __init__(self):
        self.system_instruction = ""
        self.chat_history = []
        self.all_assistant_images_with_turn = []
    
    def build(self, messages: List[Dict[str, Any]], tools: Optional[List[Dict]] = None) -> Tuple[str, List[Dict]]:
        """构建Vertex AI格式的消息"""
        self.system_instruction = ""
        self.chat_history = []
        self.all_assistant_images_with_turn = []
        
        last_user_index = -1
        assistant_turn_number = 0
        
        for i, msg in enumerate(messages):
            if msg['role'] == 'user':
                last_user_index = i
            elif msg['role'] == 'assistant':
                assistant_turn_number += 1
                # 收集assistant消息中的图片
                assistant_content = msg['content'] if isinstance(msg['content'], str) else ""
                if assistant_content and 'data:image/' in assistant_content and ';base64,' in assistant_content:
                    _, image_parts = extract_images_from_assistant_message(assistant_content)
                    if image_parts:
                        for img_part in image_parts:
                            self.all_assistant_images_with_turn.append((assistant_turn_number, img_part))
        
        if self.all_assistant_images_with_turn:
            print(f"ℹ️ 共收集 {len(self.all_assistant_images_with_turn)} 张历史图片")
        
        for i, msg in enumerate(messages):
            if msg['role'] == 'system':
                self.system_instruction += msg['content'] + "\n"
            elif msg['role'] == 'user':
                parts = self._build_user_parts(msg, i, last_user_index)
                self.chat_history.append({"role": "user", "parts": parts})
            elif msg['role'] == 'assistant':
                self._add_assistant_message(msg)
        
        if tools:
            self._inject_tools(tools)
        
        return self.system_instruction.strip(), self.chat_history
    
    def _build_user_parts(self, msg: Dict[str, Any], index: int, last_user_index: int) -> List[Dict]:
        """构建用户消息的parts"""
        parts = []
        
        if index == last_user_index and self.all_assistant_images_with_turn:
            parts.append({"text": f"[以下是之前对话中生成的 {len(self.all_assistant_images_with_turn)} 张图片：]"})
            current_turn = 0
            for turn_num, img_part in self.all_assistant_images_with_turn:
                if turn_num != current_turn:
                    current_turn = turn_num
                    parts.append({"text": f"[第 {turn_num} 轮生成的图片:]"})
                parts.append(img_part)
            
            parts.append({"text": "[以上是历史图片，用户新请求如下:]"})
            print(f"ℹ️ 注入 {len(self.all_assistant_images_with_turn)} 张历史图片")
        
        if isinstance(msg['content'], str):
            parts.append({"text": msg['content']})
        elif isinstance(msg['content'], list):
            for part in msg['content']:
                if part['type'] == 'text':
                    parts.append({"text": part['text']})
                elif part['type'] == 'image_url':
                    image_url = part['image_url']['url']
                    if image_url.startswith('data:'):
                        header, encoded = image_url.split(',', 1)
                        mime_type = header.split(':')[1].split(';')[0]
                        parts.append({
                            "inlineData": {
                                "mimeType": mime_type,
                                "data": encoded
                            }
                        })
        
        return parts
    
    def _add_assistant_message(self, msg: Dict[str, Any]) -> None:
        """添加助手消息到聊天历史"""
        assistant_content = msg['content'] if isinstance(msg['content'], str) else ""
        
        if assistant_content and 'data:image/' in assistant_content and ';base64,' in assistant_content:
            cleaned_text, _ = extract_images_from_assistant_message(assistant_content)
            
            if cleaned_text.strip():
                self.chat_history.append({"role": "model", "parts": [{"text": cleaned_text}]})
            else:
                self.chat_history.append({"role": "model", "parts": [{"text": "[已生成图片]"}]})
        else:
            if assistant_content:
                self.chat_history.append({"role": "model", "parts": [{"text": assistant_content}]})
    
    def _inject_tools(self, tools: List[Dict]) -> None:
        """注入工具到系统指令"""
        print(f"ℹ️ 注入 {len(tools)} 个工具")
        tools_xml = "\n\n<available_tools>\n"
        for tool in tools:
            function = tool.get('function', {})
            tools_xml += f"  <tool>\n"
            tools_xml += f"    <name>{function.get('name', '')}</name>\n"
            tools_xml += f"    <description>{function.get('description', '')}</description>\n"
            params = function.get('parameters', {})
            tools_xml += f"    <parameters>{json.dumps(params)}</parameters>\n"
            tools_xml += f"  </tool>\n"
        tools_xml += "</available_tools>\n"
        tools_xml += "\nIMPORTANT: To use a tool, you MUST output a <tool_calls> block. "
        self.system_instruction += tools_xml