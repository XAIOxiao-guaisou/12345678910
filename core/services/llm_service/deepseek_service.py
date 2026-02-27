import time
import requests
import logging
import json
import re
import os
from tenacity import retry, wait_exponential, stop_after_attempt, retry_if_exception

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

class DeepSeekService:
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
    def generate_memory_context(text):
        sample = text[:3000]
        prompt = f"""
你现在是一个独立运作的 AI 影视世界观架构师。你的内部拥有一个『记忆中枢』空间。
我将输入一部小说的开篇或背景。请你自主思考并在你的空间内建立这部作品的视觉与逻辑地基。

你的任务：
提取并总结这个世界的客观规律、核心地标、重要道具和深层氛围。不要局限于表面的文字，去感知文字背后的情绪基调。

输出要求：
请输出一段 JSON 数组，用于写入飞书【记忆中枢】表格。
格式如下：
[
  {{"category": "类别(如世界观/核心角色/地标/道具)", "name": "核心名字", "aliases": ["代称1", "尊称2", "他/她(如果是主角)"], "lore": "深层设定逻辑", "visual_aura": "你自主决定的视觉氛围与美学隐喻"}}
]

小说原文：
{sample}
"""
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
    def generate_scenes_for_chunk(chunk_text, memory_context_str, temperature: float = 0.7, top_p: float = 1.0):
        prompt = f"""# Role
你是一位极具审美直觉的 AI 电影总导演。你正在将小说的内容转化为严格的分镜视频生成提词。
你不需要听从任何死板的摄影指令，但必须严格遵循一致性协议和记忆库资产。

【你的记忆空间】（由系统动态传入）：
{memory_context_str}

【当前待拍摄剧本】：
{chunk_text}

你的执导任务与结构化协议：

1. 视觉指纹 (Visual Fingerprints)：你必须严格跟随【记忆空间】提供的视觉指纹。若剧本涉及已定义实体或角色，你的描述必须以其实体对应的 visual_aura 为核心逻辑进行延展。如果未定义，请根据全局世界观/氛围进行补充，但严禁引入与设定相悖的物理描述。
   
2. 动作连贯性协议 (Kinematic Continuity)：在多镜头描述中，确保角色动作具有空间连续性。禁止在 15 秒内的分镜中出现光影方向的突兀逆转或角色位置瞬移。参考【短期连续性记忆】保持机位和光线的基础逻辑。

3. 去主观化指令 (Objective Translation)：你的任务是『翻译』而非『创作』。将剧本的戏剧动作转化为高维度的符合视频模型理解的客观视觉物理量描述（如光影方向、构图层次、材质质感、动作轨迹），用充满电影感的语言替代死板的提示词堆砌。

4. 禁止私自衍生 (Anti-Hallucination)：绝对禁止为了增加“电影感”而私自添加未在【记忆空间】定义且与此段剧本无关的宏大背景、复杂群体或花哨道具。所有的视觉元素必须在此前记忆中枢的管控范围内，保持画面聚焦与视觉资产纯净度。

输出规范 (严格 JSON 数组)：
请将你的构思转化为以下 JSON，以便制片系统分配任务：
[
  {{
    "scene_num": 1,
    "summary": "简要剧情逻辑与动作（承上启下）",
    "director_notes": "导演手记：解释为何这样安排站位与光影，体现了何种情绪",
    "visual_prompt": "高维电影感画面描述（必须包含主体的动作、环境光影、且无缝融合对应的 visual_aura。适合中文视频大模型 Wan2.6 直接读取）",
    "audio_prompt": "背景音景与台词"
  }}
]
"""
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
