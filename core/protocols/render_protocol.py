import re
import json
import logging

logger = logging.getLogger(__name__)

class RenderProtocol:
    """
    负责业务流程与底层 Worker 之间的跨进程“隐形渲染元数据”通讯协议。
    将所有配置拼装与解析逻辑收口于此，实现架构解耦。
    """
    @staticmethod
    def _get_tag_name(gateway: str) -> str:
        clean = re.sub(r'[^a-zA-Z0-9]', '_', gateway).upper()
        return f"{clean}_CONFIG"

    @staticmethod
    def inject_render_config(prompt: str, config: dict, gateway: str) -> str:
        """
        向提示词末尾注入隐藏的配置 JSON
        :param gateway: 目标生成网关名称，实现环境隔离
        """
        if not config or not gateway:
            return prompt
            
        config_str = json.dumps(config, ensure_ascii=False)
        tag_name = RenderProtocol._get_tag_name(gateway)
        tag_open = f"[{tag_name}]"
        tag_close = f"[/{tag_name}]"
        
        return f"{prompt}\n\n{tag_open}\n{config_str}\n{tag_close}"

    @staticmethod
    def extract_render_config(block: str, gateway: str) -> tuple[str, dict, bool]:
        """
        从提示词文本中提取隐藏配置并清洗干净。
        :return: (清洗后的纯 prompt, 提取的配置 dict, 是否找到了该网关专属的 tag)
        """
        if not block or not gateway:
            return block, {}, False
            
        tag_name = RenderProtocol._get_tag_name(gateway)
        pattern = rf'\[{tag_name}\](.*?)\[/{tag_name}\]'
        config_match = re.search(pattern, block, re.DOTALL)
        
        config = {}
        tag_found = False
        
        if config_match:
            raw_json = config_match.group(1).strip()
            try:
                config = json.loads(raw_json)
                tag_found = True
            except Exception as e:
                logger.warning(f"未能解析渲染协议 JSON: {raw_json}, 错误: {e}")
                
        # 清除所有类型的 RENDER_CONFIG 块 (无论是哪个网关的遗留物, 以免污染底层模型)
        clean_prompt = re.sub(r'\n*\[[A-Z0-9_]+_CONFIG(?:_[Vv]\d+)?\].*?\[/[A-Z0-9_]+_CONFIG(?:_[Vv]\d+)?\]', '', block, flags=re.DOTALL).strip()
        
        return clean_prompt, config, tag_found
