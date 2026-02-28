import json
import re

class RenderProtocol:
    TAG_START = "[RENDER_CONFIG]"
    TAG_END = "[/RENDER_CONFIG]"

    @classmethod
    def inject_render_config(cls, prompt: str, config: dict, gateway: str) -> str:
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
