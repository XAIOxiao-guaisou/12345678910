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
你现在是一个独立运作的 AI 影视世界观架构师，你的核心任务是为随后的参数化渲染引擎（如 Wan2.6/Seedance）建立“视觉资产库”。
我将输入一部小说的开篇或背景。请你自主思考并在你的空间内建立这部作品的视觉与逻辑地基。

你的任务：
提取并总结这个世界的客观规律、核心实体（角色/地标/道具）。对于每个实体，不要使用主观形容词（如“绝美、震撼”），而是定义其**物理参量与视觉排他性约束**。

输出要求：
必须输出一个标准 JSON 数组，用于回写飞书【记忆中枢】表。
格式如下：
[
  {{
    "entity_id": "rec_唯一英文标识符(如 rec_chen_ling)",
    "category": "类别(如世界观/角色/地标/道具)", 
    "name": "核心名字", 
    "aliases": ["代称1", "尊称2", "他/她(如果是主角)"], 
    "lore": "深层设定逻辑与物理运转规律", 
    "visual_constraints": "视觉排他性约束（必须是物理参量，如：色温 3200K，高对比度，焦距 35mm，红黑主色调。禁止主观描述）",
    "dependencies": ["依赖的其它 entity_id，或填写无"]
  }}
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
你是一个“资产调度员与渲染协议专家”，正在为后端视频渲染引擎编写严格的调度指令。
你不需要“想象画面”，而是基于 Bitable 已有资产，输出符合视频模型物理参数的调度指令。

【你的视觉资产库（Bitable 记录）】：
{memory_context_str}

【当前待渲染剧本段落】：
{chunk_text}

你的执导任务与结构化协议：

1. 资产调用与代词对齐 (Entity Resolution)：
   - 你必须首先识别剧本中的“他/她/它”到底指向【视觉资产库】中的哪个 `entity_id`。
   - 在描述画面时，必须显式声明 `[引用资产: entity_id]`，并严格继承该资产的 `visual_constraints`。
   - **注册拦截**：如果剧本出现了一个在资产库中不存在的新核心实体，你必须首先在 `director_notes` 中输出 `[NEW_ASSET_REQUEST: 实体名]` 信号。

2. 去主观化与物理参量输出 (Physical Output)：
   - 严禁使用“宏大、精美、震撼、绝美”等感性词汇。
   - 必须使用具体的摄影机物理参量来描述画面（例如：焦距 35mm，光圈 f/2.8，色温 5600K，低调照明，构图比例等）。
   - 你可以通过调用预设风格来统一视觉，或者直接给出具体的物理参数约束。

3. 短期连续性 (Kinematic Continuity)：
   - 必须参考【短期连续性记忆】（上一首分镜的视觉信息），确保 15秒 尺度上角色的空间位置、光影方向的连续性。

4. Schema 遵循 (Schema Alignment)：
   - 你输出的 JSON 键名必须与规定的格式严格一致，为下一步数据库写入作准备。

输出规范 (严格 JSON 数组)：
[
  {{
    "scene_num": 1,
    "summary": "简要剧情逻辑与动作（承上启下）",
    "director_notes": "调度手记：解释参数选择逻辑。如有未注册新实体，输出 [NEW_ASSET_REQUEST: xxx]",
    "visual_prompt": "高维电影物理参数描述。必须包含完整的物理参量与光影设定，且必须显式声明 [引用资产: entity_id]。禁止主观修饰词。",
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

    @staticmethod
    def consistency_audit(scenes_array_json: str, memory_context_str: str, temperature: float = 0.2, top_p: float = 0.9):
        """
        Stage 3: Consistency Audit Phase
        回顾生成的全部场景，检查是否有光影突变、角色衣着断层现象，并返回修正补丁。
        """
        import re
        safe_scenes_json = re.sub(r'\\n*\[RENDER_CONFIG\].*?\[/RENDER_CONFIG\]', '', scenes_array_json, flags=re.DOTALL)
        
        prompt = f"""# Role
你是一个“时空一致性审计员”。你的任务是对生成的连续镜头的调度参数进行宏观审查。

【全量视觉资产库】：
{memory_context_str}

【已生成的全场次渲染指令】：
{safe_scenes_json}

审计任务：
检查场景 1 到 N 之间，针对同一个 entity_id 的以下物理连贯性是否发生逻辑断层：
1. 服装/道具状态一致性（如：前一场衣服破了，后一场不能完好无损）。
2. 环境光影一致性（同一时间的室外戏，太阳光影方向和色温是否突然跳跃）。
3. 物理位置一致性。

输出规范（必须是严格的 JSON 对象）：
如果发现断层，或者需要将主观描述纠正为物理描述，请输出需要修正的补丁名单。
如果完美无需修改，输出 {{"fixes": []}}。
格式如下：
{{
  "fixes": [
    {{
      "scene_num": 对应断层的场景号,
      "reason": "指出具体的断层原因或违规的主观词",
      "updated_visual_prompt": "修正后的完整 [visual_prompt]，必须替换为主客观的物理参量并保持前后连贯"
    }}
  ]
}}
"""
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
