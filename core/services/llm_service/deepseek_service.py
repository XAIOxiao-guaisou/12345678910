import time
import requests
import logging
import json
import re
import os

logger = logging.getLogger(__name__)

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

SILICONFLOW_KEY = os.environ.get("SILICONFLOW_API_KEY", "")
if SILICONFLOW_KEY:
    FREE_PROXY_ENDPOINTS.insert(0, {
        "base_url": "https://api.siliconflow.cn/v1",
        "api_key": SILICONFLOW_KEY,
        "model_map": {"deepseek-chat": "deepseek-ai/DeepSeek-V3"},
        "name": "SiliconFlow (国内优先)"
    })

DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
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
    def call_openai_compatible_api(base_url: str, api_key: str, model: str, message: str, timeout: int = 150):
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
            "temperature": 0.7
        }
        resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        if not content or len(content) < 5:
            raise ValueError("响应内容过短或为空")
        return content

    @staticmethod
    def call_deepseek(message: str):
        """依次尝试各个免费反向代理调用 DeepSeek"""
        for proxy in FREE_PROXY_ENDPOINTS:
            proxy_model = proxy["model_map"].get("deepseek-chat", "deepseek-v3")
            logger.info(f"🔁 尝试公共服务: [{proxy['name']}] model={proxy_model}")
            try:
                content = DeepSeekService.call_openai_compatible_api(
                    base_url=proxy["base_url"],
                    api_key=proxy["api_key"],
                    model=proxy_model,
                    message=message,
                    timeout=120
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
        if raw_json.startswith('['):
            if not raw_json.endswith(']'):
                last_brace = raw_json.rfind('}')
                if last_brace != -1:
                    repaired = raw_json[:last_brace+1] + ']'
                    res = try_parse(repaired)
                    if res:
                        logger.warning(f"✅ 修复了被截断的 JSON，成功挽救 {len(res)} 条分镜。")
                        return res
        return None

    @staticmethod
    def smart_chunk_text(text, chunk_size=1200):
        text = text.strip()
        chunks = []
        start = 0
        while start < len(text):
            end = start + chunk_size
            if end >= len(text):
                chunk = text[start:].strip()
                if chunk: chunks.append(chunk)
                break
            for sep in ['\n\n', '\n', '。', '！', '？']:
                split_at = text.rfind(sep, start, end)
                if split_at > start:
                    end = split_at + len(sep)
                    break
            chunk = text[start:end].strip()
            if chunk: chunks.append(chunk)
            start = end
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
  {{"category": "类别(如世界观/地标/道具/角色)", "name": "词条名", "lore": "深层设定逻辑", "visual_aura": "你自主决定的视觉氛围与美学隐喻"}}
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
    def delta_extract_memory(chapter_text: str, existing_summary: str, novel_id: str = "") -> list:
        """
        v2.6.0 增量记忆提取。
        DeepSeek 对比现有记忆库，输出 UPDATE / INSERT / ARCHIVE 三类指令。
        返回符合 MemoryEngine.local_diff 格式的 list。
        """
        prompt = f"""你是一个小说世界观的「记忆分析将」，负责维护一个复杂长篇小说的设定词条库。

【现有记忆库里对比摘要（请不要重復创建其中已有的词条）】：
{existing_summary}

【当前章节原文（待分析）】：
{chapter_text[:3000]}

任务：
对比现有记忆库与当前章节，输出以下三类指令：
1. UPDATE: 已有词条需要修正（如角色获得新能力、地点改变、设定补充）
2. INSERT: 全新出现的词条（新角色、新地点、新道具）
3. ARCHIVE: 该词条已在本章明确季退出剧情（如角色死亡、地点毾灯）

注意事项：
- 无变化的词条无需输出（不要沿用现有词条做无意义的 INSERT）
- 每条输出必须包含 action 字段
- entity_id 如已知就填写（来自现有库摘要），如不知则留空
- 如无任何变化（章节未引入任何设定更新），则返回空数组 []

输出格式（严格 JSON 数组）：
[
  {{
    "action": "UPDATE",
    "entity_id": "e_a1b2c3d4",
    "novel_id": "{novel_id}",
    "category": "角色",
    "name": "角色名",
    "lore": "更新后的深层设定",
    "visual_aura": "视觉隐喻"
  }},
  {{
    "action": "INSERT",
    "entity_id": "",
    "novel_id": "{novel_id}",
    "category": "地标",
    "name": "新地点",
    "lore": "",
    "visual_aura": ""
  }},
  {{
    "action": "ARCHIVE",
    "entity_id": "e_x1y2z3w4",
    "novel_id": "{novel_id}",
    "name": "已死亱角色",
    "reason": "该角色在本章第12节死亡"
  }}
]
"""
        logger.info("DeepSeek 增量提取记忆差分...")
        for attempt in range(3):
            try:
                result_text = DeepSeekService.call_deepseek(prompt)
                parsed = DeepSeekService.extract_json_from_deepseek(result_text)
                if isinstance(parsed, list):
                    # 允许空数组（表示该章无设定变化）
                    if len(parsed) == 0:
                        logger.info("✅ [delta_extract] 该章节无设定变化，返回空列表")
                        return []
                    if "action" in parsed[0]:
                        logger.info(f"✅ [delta_extract] 成功解析 {len(parsed)} 条变更指令")
                        return parsed
                logger.warning(f"[delta_extract] 第{attempt+1}次返回格式异常: {str(result_text)[:200]}")
            except Exception as e:
                logger.error(f"[delta_extract] 第{attempt+1}次异常: {e}")
            import time as _t; _t.sleep(2)

        logger.warning("⚠️ delta_extract_memory 全部失败，返回空列表（该章记忆不变）")
        return []

    @staticmethod
    def generate_scenes_for_chunk(chunk_text, memory_context_str):
        prompt = f"""# Role
你是一位极具审美直觉的 AI 电影总导演。你不需要听从任何死板的摄影指令，你拥有完全的视听语言决定权。

【你的记忆空间】（由系统动态传入）：
{memory_context_str}

【当前待拍摄剧本】：
{chunk_text}

你的执导任务：

感知情绪：阅读剧本，结合你的【记忆空间】，自主感受这一幕的戏剧张力、角色的心理状态和环境的潜台词。

场面调度：根据你对情绪的理解，自主决定每一段 15 秒视频的呈现方式。什么时候该用宏大的远景展现孤独？什么时候该用压抑的特写展现恐惧？由你全权安排。

光影与质感：不要堆砌死板的相机参数。请用充满电影感和文学性的语言描述画面（如：“冰冷的晨光穿透百叶窗，空气中悬浮着不安的尘埃”）。阿里云万相模型极其擅长理解这种高维度的意境描述。

输出规范 (严格 JSON 数组)：
请将你的导演构思转化为以下结构的 JSON，以便制片系统（飞书多维表格）分配拍摄任务：
[
  {{
    "scene_num": 1,
    "summary": "简述剧情逻辑",
    "director_notes": "导演手记：简述你为什么选择这样拍，你的视听意图是什么（此项供人类制片人参考）",
    "visual_prompt": "你自主撰写的高维画面描述（无需死板的摄影机术语，注重画面内容、情绪光影、质感、主体动作的流畅描述。适合直接输入给中文视频大模型 Wan2.6）",
    "audio_prompt": "你构思的背景音景与台词"
  }}
]
"""
        for _ in range(3):
            try:
                result_text = DeepSeekService.call_deepseek(prompt)
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
