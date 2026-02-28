import time
import requests
import logging
import json
import re
import os
from tenacity import retry, wait_exponential, stop_after_attempt, retry_if_exception
from jinja2 import Environment, FileSystemLoader

logger = logging.getLogger(__name__)

def _is_requests_retryable(e: BaseException) -> bool:
    import requests
    if isinstance(e, requests.exceptions.HTTPError):
        resp = getattr(e, 'response', None)
        if resp is not None and getattr(resp, 'status_code', 500) in (400, 401, 403, 404):
            return False # Business/Auth failures, immediately fail and propagate
    if isinstance(e, requests.exceptions.RequestException):
        return True
    return False

from core.config import settings

# ---------------------------------------------------------
# DeepSeek API Logic
# ---------------------------------------------------------
FREE_PROXY_ENDPOINTS = [
    {
        "base_url": "https://api.llm7.io/v1",
        "api_key": "null",
        "model_map": {"deepseek-chat": "deepseek/deepseek-chat-v3-0324"},
        "name": "LLM7 (高频无需注册)"
    },
    {
        "base_url": "https://fresedgpt.space/v1",
        "api_key": "null",
        "model_map": {"deepseek-chat": "deepseek-v3"},
        "name": "FresedGPT (每日免费)"
    },
]

SILICONFLOW_KEY = settings.SILICONFLOW_API_KEY
if SILICONFLOW_KEY:
    FREE_PROXY_ENDPOINTS.insert(0, {
        "base_url": "https://api.siliconflow.cn/v1",
        "api_key": SILICONFLOW_KEY,
        "model_map": {"deepseek-chat": "deepseek-ai/DeepSeek-V3"},
        "name": "SiliconFlow (国内优先)"
    })

DEEPSEEK_API_KEY = settings.DEEPSEEK_API_KEY
if DEEPSEEK_API_KEY:
    FREE_PROXY_ENDPOINTS.append({
        "base_url": "https://api.deepseek.com/v1",
        "api_key": DEEPSEEK_API_KEY,
        "model_map": {"deepseek-chat": "deepseek-chat"},
        "name": "DeepSeek Official (付费保底)"
    })

# ---------------------------------------------------------
# Style Presets
# ---------------------------------------------------------
STYLE_PRESETS = {
    "realistic": {
        "name": "电影感真人风格",
        "visual_style": "High-end cinematic, photorealistic, shot on Hasselblad H6D, 80mm, f/2.8, Rembrandt lighting, 8k resolution, quiet luxury texture, real-world physics.",
        "negative_prompt": "anime, cartoon, illustration, drawing, 2D, fake, plastic texture."
    },
    "anime": {
        "name": "高品质动漫风格",
         "visual_style": "High-quality Anime style, Makoto Shinkai style, extreme details, 8k resolution, vibrant colors, expressive character movements, highly coherent scenes, NO photorealism.",
        "negative_prompt": "photorealistic, real person, 3D render, noisy texture, blurry background."
    }
}

# =========================================================
# Jinja2 Prompt Environment (Cached)
# =========================================================
PROMPTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prompts")
jinja_env = Environment(loader=FileSystemLoader(PROMPTS_DIR), cache_size=0)

class DeepSeekService:
    @staticmethod
    def load_prompt(template_name: str, style: str = "default", **kwargs) -> str:
        """
        加载并渲染 Jinja2 提示词模板。
        支持根据风格退避：如果 `style/template_name` 存在则使用，否则回退到 `default/template_name`。
        """
        try:
            # 尝试加载风格特化模板
            template_path = f"{style}/{template_name}"
            template = jinja_env.get_template(template_path)
            return template.render(**kwargs)
        except Exception:
            # Fallback 到 default
            try:
                template_path = f"default/{template_name}"
                template = jinja_env.get_template(template_path)
                return template.render(**kwargs)
            except Exception as e:
                logger.error(f"严重错误：无法加载 Prompt 模板 {template_name} ({e})")
                raise FileNotFoundError(f"Missing essential prompt template: {template_name}")


    @staticmethod
    @retry(wait=wait_exponential(multiplier=1, min=4, max=60), stop=stop_after_attempt(5), retry=retry_if_exception(_is_requests_retryable))
    def call_openai_compatible_api(base_url: str, api_key: str, model: str, message: str, timeout: int = 150, temperature: float = 0.7, top_p: float = 1.0):
        url = f"{base_url.rstrip('/')}/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": "你是一位专业的顶级短剧编剧。"},
                {"role": "user", "content": message}
            ],
            "max_tokens": 4096,
            "temperature": temperature,
            "top_p": top_p
        }
        resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        if not content or len(content) < 5:
            raise ValueError("响应内容过短或为空")
        return content

    @staticmethod
    def call_deepseek(message: str, temperature: float = 0.7, top_p: float = 1.0):
        """依次尝试各个免费反向代理调用 DeepSeek"""
        for proxy in FREE_PROXY_ENDPOINTS:
            model_map = proxy.get("model_map", {})
            if isinstance(model_map, dict):
                proxy_model = model_map.get("deepseek-chat", "deepseek-v3")
            else:
                proxy_model = "deepseek-v3"
            logger.info(f"🔁 尝试公共服务: [{proxy['name']}] model={proxy_model}")
            try:
                content = DeepSeekService.call_openai_compatible_api(
                    base_url=proxy["base_url"],
                    api_key=proxy["api_key"],
                    model=proxy_model,
                    message=message,
                    timeout=120,
                    temperature=temperature,
                    top_p=top_p
                )
                return content
            except Exception as e:
                logger.warning(f"⚠️ [{proxy['name']}] 异常: {e}，切换下一个...")
            time.sleep(1)
            
        raise Exception("❌ 所有 DeepSeek 公共产出节点均失效，请稍后再试或配置 SILICONFLOW_API_KEY。")

    @staticmethod
    def extract_json_from_deepseek(text):
        if not text: return None
        text = text.strip()
        
        def try_parse(s):
            try: return json.loads(s)
            except: return None

        # Code block format
        match = re.search(r'```(?:json)?\s*([\[\{].*?[\]\}])\s*```', text, re.DOTALL)
        if match:
            res = try_parse(match.group(1))
            if res: return res

        # Raw search
        match = re.search(r'([\[\{].*)', text, re.DOTALL)
        if not match: return None
        raw_json = match.group(1).strip()
        res = try_parse(raw_json)
        if res: return res
        
        # Repair missing bracket
        if isinstance(raw_json, str) and raw_json.startswith('['):
            if not raw_json.endswith(']'):
                last_brace = raw_json.rfind('}')
                if last_brace != -1:
                    repaired = str(raw_json)[:int(last_brace)+1] + ']'
                    res = try_parse(repaired)
                    if res:
                        logger.warning(f"✅ 修复了被截断的 JSON (Missing ']')，成功挽救 {len(res)} 条分镜。")
                        return res
                # Deep JSON Repair if missing brace completely
                last_quote = raw_json.rfind('"')
                if last_quote != -1 and raw_json.rfind('}') < last_quote:
                    # Very abruptly ended
                    repaired2 = str(raw_json)[:int(last_quote)] + '"}]'
                    res2 = try_parse(repaired2)
                    if res2:
                        logger.warning(f"✅ 深度修复了严重截断的 JSON，成功挽救 {len(res2)} 条分镜。")
                        return res2
        return None

    def smart_chunk_text(raw_text: str, chunk_size: int = 1200) -> list:
        text_str = str(raw_text).strip()
        chunks = []
        start_idx = 0
        while start_idx < len(text_str):
            end_idx = start_idx + chunk_size
            if end_idx >= len(text_str):
                chunk = text_str[start_idx:].strip()
                if chunk: chunks.append(chunk)
                break
            for sep in ['\n\n', '\n', '。', '！', '？']:
                split_at = text_str.rfind(sep, start_idx, end_idx)
                if split_at > start_idx:
                    end_idx = split_at + len(sep)
                    break
            chunk = text_str[start_idx:end_idx].strip()
            if chunk: chunks.append(chunk)
            start_idx = end_idx
        return chunks

    @staticmethod
    def extract_characters(text):
        sample = text[:2000]
        prompt = f"""
分析以下小说文本，提取主要角色信息。只返回纯JSON数组：
[
  {{ "name": "角色姓名", "gender": "性别", "appearance": "外貌特征30字内" }}
]
小说原文：
{sample}
"""
        logger.info("DeepSeek 提取角色信息...")
        result_text = DeepSeekService.call_deepseek(prompt)
        parsed = DeepSeekService.extract_json_from_deepseek(result_text)
        return parsed if (isinstance(parsed, list) and len(parsed) > 0 and "name" in parsed[0]) else []

    @staticmethod
    def generate_memory_context(text, style_key: str = "default"):
        sample = text[:3000]
        prompt = DeepSeekService.load_prompt("stage1_memory.j2", style=style_key, sample=sample)

        logger.info("DeepSeek 提取记忆中枢世界观...")
        for _ in range(3):
            try:
                result_text = DeepSeekService.call_deepseek(prompt)
                parsed = DeepSeekService.extract_json_from_deepseek(result_text)
                if isinstance(parsed, list) and len(parsed) > 0 and "name" in parsed[0]:
                    return parsed
            except Exception as e:
                logger.error(f"Error extracting memory context: {e}")
            time.sleep(2)
            
        # 容错机制：如果建档失败，整个管线应当暂停并抛出警报
        raise RuntimeError("阶段一建档失败！DeepSeek 未能正确返回 JSON 格式的记忆中枢数据，管线中断。")

    @staticmethod
    def generate_scenes_for_chunk(chunk_text, memory_context_str, style_key: str = "default", temperature: float = 0.7, top_p: float = 1.0):
        prompt = DeepSeekService.load_prompt("stage2_scenes.j2", style=style_key, memory_context_str=memory_context_str, chunk_text=chunk_text)

        for _ in range(3):
            try:
                result_text = DeepSeekService.call_deepseek(prompt, temperature=temperature, top_p=top_p)
                logger.info(f"DeepSeek Two-Stage Stage 2 Output (Length: {len(result_text)}):\n{result_text[:500]}...")
                parsed = DeepSeekService.extract_json_from_deepseek(result_text)
                if isinstance(parsed, list) and len(parsed) > 0 and ("visual_prompt" in parsed[0] or "master_prompt" in parsed[0]):
                    return parsed
                else:
                    logger.warning(f"Failed to parse or missing keys. Parsed type: {type(parsed)}")
            except Exception as e:
                logger.error(f"Error calling deepseek: {e}")
                pass
            time.sleep(2)
        return []

    @staticmethod
    def consistency_audit(scenes_array_json: str, memory_context_str: str, style_key: str = "default", temperature: float = 0.2, top_p: float = 0.9):
        """
        Stage 3: Consistency Audit Phase
        回顾生成的全部场景，检查是否有光影突变、角色衣着断层现象，并返回修正补丁。
        """
        # Strip any injected [xxx_CONFIG] tags before auditing so LLM isn't confused
        import re
        safe_scenes_json = re.sub(r'\[[A-Z0-9_]+_CONFIG(?:\_[Vv]\d+)?\].*?\[/[A-Z0-9_]+_CONFIG(?:\_[Vv]\d+)?\]', '', scenes_array_json, flags=re.DOTALL)

        prompt = DeepSeekService.load_prompt("stage3_audit.j2", style=style_key, memory_context_str=memory_context_str, scenes_array_json=safe_scenes_json)

        logger.info("DeepSeek 执行一致性审计 (Stage 3)...")
        for _ in range(3):
            try:
                result_text = DeepSeekService.call_deepseek(prompt, temperature=temperature, top_p=top_p)
                parsed = DeepSeekService.extract_json_from_deepseek(result_text)
                if isinstance(parsed, dict) and "fixes" in parsed:
                    return parsed
            except Exception as e:
                logger.error(f"Error in consistency audit: {e}")
            time.sleep(2)
        return {"fixes": []}
