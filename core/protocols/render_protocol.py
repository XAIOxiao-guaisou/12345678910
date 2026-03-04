import json
import re

class RenderProtocol:
    TAG_START = "[RENDER_CONFIG]"
    TAG_END = "[/RENDER_CONFIG]"

    @classmethod
    def resolve_multi_frame(cls, image_urls: list, gateway: str) -> dict:
        """v3.0.0-PRO: 根据目标网关，将多帧 List[ImageURL] 映射为特定的 KV."""
        if not image_urls:
            return {}
        
        config = {}
        gateway_lower = gateway.lower() if gateway else ""
        
        # 如果是支持插值或多帧的网关（比如类似 seedance）
        if "seedance" in gateway_lower and len(image_urls) > 1:
            config["first_frame"] = image_urls[0]
            config["last_frame"] = image_urls[-1]
        else:
            # 默认：降级为首帧 I2V (Wan2.6 等)
            config["image_url"] = image_urls[0]
            
        return config

    @classmethod
    def inject_render_config(cls, prompt: str, config: dict, gateway: str = "") -> str:
        """将配置字典按规矩注入到 Prompt 末尾。"""
        # 如果 config 里包含 image_urls，先做协议映射
        if "image_urls" in config:
            urls = config.pop("image_urls")
            resolved = cls.resolve_multi_frame(urls, gateway)
            config.update(resolved)
            
        tag = f"\n\n{cls.TAG_START}\n{json.dumps(config, ensure_ascii=False)}\n{cls.TAG_END}"
        return f"{prompt}{tag}"

    @classmethod
    def extract_render_config(cls, raw_text: str):
        pattern = rf"{re.escape(cls.TAG_START)}(.*?){re.escape(cls.TAG_END)}"
        match = re.search(pattern, raw_text, re.DOTALL)
        if match:
            try:
                config = json.loads(match.group(1).strip())
                clean_prompt = re.sub(pattern, "", raw_text, flags=re.DOTALL).strip()
                return clean_prompt, config, True
            except:
                pass
        return raw_text, {}, False
