import time
import requests
import logging
import json
import re
import os
from pathlib import Path

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
        for attempt in range(4):
            try:
                result_text = DeepSeekService.call_deepseek(prompt)
                parsed = DeepSeekService.extract_json_from_deepseek(result_text)
                if isinstance(parsed, list) and len(parsed) > 0 and "name" in parsed[0]:
                    return parsed
            except Exception as e:
                logger.error(f"Error extracting memory context: {e}")
            wait_sec = min(5 * (2 ** attempt), 60)
            logger.warning(f"⚠️ [建档] 第 {attempt+1} 次失败，退避 {wait_sec} 秒后重试...")
            time.sleep(wait_sec)
            
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

【当前全量活跃实体池（请逐一审查本列表中的实体）：】
{existing_summary}

【当前章节原文（待分析）】：
{chapter_text[:3000]}

【“1+1 双轨制” 提取与审计指令】：
1. 存续审计（老角色）：对照上述“活跃实体池”，审视他们在当前章节中的存续状态与设定更新。即使池中的角色在本章【完全未出现】，也必须通过 UPDATE 返回其心跳状态（结合前文剧情推断其最后已知位置或状态），并严格保留其 entity_id 和 novel_id。
2. 新星探测（新角色）：寻找本章内符合条件但【不在活跃池中】的全新重要实体（新角色、新的特殊地点或道具）。
3. 对于未出场的活跃角色，必须输出其最后已知位置或状态（如："仍在客栈休息"、"重伤昏迷中"）到 last_known_state。
4. 如果未出场角色的状态对主线存在隐性威胁/干预，请记录在 implicit_carry 中。

任务：
对比现有记忆库与当前章节，输出以下三类指令：
1. UPDATE: 用于审计“活跃实体池”。必须包含 present_in_current(布尔值)。若未出场，则 present_in_current 为 false，并更新 last_known_state。
2. INSERT: 用于探测全新出现的词条。必须包含 present_in_current: true，且不需生成 entity_id。
3. ARCHIVE: 该活跃池中的词条已在本章明确退出剧情（如死亡、毁灭等）。

注意事项：
- 无变化的由于也需要心跳更新，所以活跃角色必须全部经过审视。
- 每条输出必须包含 action 字段。如果章节确无任何设定更迭与心跳，返回 []。

输出格式（严格 JSON 数组）：
[
  {{
    "action": "UPDATE",
    "present_in_current": false,
    "entity_id": "e_a1b2c3d4",
    "novel_id": "{novel_id}",
    "category": "角色",
    "name": "角色名",
    "lore": "更新后的深层设定",
    "visual_aura": "视觉隐喻",
    "last_known_state": "仍在客栈昏迷",
    "implicit_carry": "他的伤势依然让主角担忧"
  }},
  {{
    "action": "INSERT",
    "present_in_current": true,
    "entity_id": "",
    "novel_id": "{novel_id}",
    "category": "地标",
    "name": "新地点",
    "lore": "...",
    "visual_aura": "..."
  }}
]
"""
        logger.info("DeepSeek 增量提取记忆差分...")
        for attempt in range(4):
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
            wait_sec = min(5 * (2 ** attempt), 60)
            logger.warning(f"⚠️ [增量提取] 第 {attempt+1} 次失败，退避 {wait_sec} 秒...")
            import time as _t; _t.sleep(wait_sec)

        logger.warning("⚠️ delta_extract_memory 全部失败，返回空列表（该章记忆不变）")
        return []

    @staticmethod
    def generate_scenes_for_chunk(chunk_text, memory_context_str, previous_context: dict = None):
        # === 长篇短剧剧情连接优化：强行注入上一幕剧情与情绪流 ===
        prompt_prev = ""
        if previous_context and isinstance(previous_context, dict):
            prev_summary = previous_context.get("summary", "")
            prev_visual = previous_context.get("visual_prompt", "")
            prev_emotion = previous_context.get("emotion", "")
            prev_camera = previous_context.get("camera", "")
            prev_hook = previous_context.get("hook", "")

            if prev_summary or prev_visual:
                prompt_prev = f"""
【前情接续强制约束（必须通过视听语言无缝承接）】：
- 上幕结尾剧情: {prev_summary}
- 上幕结尾画面: {prev_visual}
- 上幕遗留情绪: {prev_emotion}
- 上幕结尾运镜: {prev_camera}
- 上幕制造的悬念(Hook): {prev_hook}
"""

        prompt = f"""# Role
你是一位深谙竖屏 AI 短剧（1-3分钟/集）流量密码的顶级总导演。你不需要听从任何死板的摄影指令，你拥有完全的视听语言决定权，擅长每 10-15 秒制造一次视觉或情绪波峰。

【你的记忆空间】（由系统动态传入）：
{memory_context_str}
{prompt_prev}
【当前待拍摄剧本】：
{chunk_text}

你的执导任务：

1. 短剧衔接与视听法则 (极高优先级)：
如果你收到了【前情接续强制约束】，你的第一个分镜必须无缝点燃上幕遗留的悬念（Hook）！
- 动作连读：如果上一幕角色动作未完成（如“伸手”），本幕第一个画面必须自然继承（如“紧紧抓住了门把手”）。
- 情绪平滑渐变：承接上幕遗留情绪，如上幕是“极度恐慌”，本幕开头可以是“眼神微颤”，随后再发生情绪转折。切忌情绪出现突兀断层。
- 转场技巧：巧妙利用关键道具特写、相似线条或相同景别切换，实现跨集/跨幕的无断点平滑过渡。

2. 10s短视频节奏论：
- 每个分镜代表一段实际生成的视频流（通常为 5-15 秒）。
- 拒绝无效垃圾铺垫：该分镜内必须交代一个核心动作推进、情绪爆发或线索揭示。强因果，快节奏。

3. 首尾闭环与钩子前置：
- 你拆解出的一批分镜应具备内部逻辑闭环。
- 尤其注意：在本次输出的【最后一个分镜】结尾，必须预埋一个强烈的“悬念 / 危机 / 身份反转”钩子（Hook），以此诱导观众继续往下看！

4. 光影与质感调度：
- 不要死板堆砌相机参数。用充满电影感和文学性的词汇描绘光影、质感、构图（如“冰冷的晨光穿透百叶窗，空气中悬浮着不安的尘埃”）。目前主流视频生成大模型极其擅长理解此类高维意境描述。

5. 实体追踪与视觉强锚定：
- 每一幕都必须列出涉及的全部 entity_id（必须严格从【你的记忆空间】挑选）。
- 若实体在本幕出场，必须在 visual_prompt 中以极高权重继承【你的记忆空间】里规定的“视觉氛围约束”，维持角色穿搭/外貌/特殊疤痕的每一丝细节连续。

输出规范 (严格 JSON 数组)：
请将你的构思转化为以下结构，供自动制片引擎调用：
[
  {{
    "scene_num": 1,
    "summary": "简述本分镜(5-15s)的剧情动作流与起承转合",
    "entity_ids": ["e_xxxx_001", "e_xxxx_002"],
    "director_notes": "导演手记：简述你为什么选择这样拍，视听意图在哪？",
    "emotion": "本幕核心情绪轨迹（如：从震惊到内敛的愤怒）",
    "camera": "转场与运镜提示（如：承接上幕面部特写，镜头跟随主角缓慢拉开）",
    "hook": "本幕末尾抛出的钩子（前几个分镜可为空，但最后一个分镜必须设立强有力的危机/悬念 Hook！）",
    "visual_prompt": "你自主撰写的高维画面描述，直接用于分发给视频大模型。(必须包含动作连续性表现、情绪光影、质感细节的极具张力的连续描述)",
    "audio_prompt": "背景音景与画外音台词"
  }}
]
"""
        for attempt in range(4):
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
            wait_sec = min(5 * (2 ** attempt), 60)
            logger.warning(f"⚠️ [剧本拆解] 第 {attempt+1} 次失败，退避 {wait_sec} 秒...")
            time.sleep(wait_sec)
        return []

    @staticmethod
    def generate_visual_prompt(
        entity: dict,
        evolution_lore: str = "",
        old_prompt: str = "",
    ) -> str:
        """
        v2.7.0: 通用化实体视觉 Prompt 生成器。

        Args:
            entity:         包含 category/name/lore/visual_aura 的实体字典
            evolution_lore: 本章视觉演进描述（空=初始建档，非空=迭代更新）
            old_prompt:     上一章节确立的英文 Prompt（迭代时使用）

        Returns:
            str: 英文生图 Prompt（直接传给 PollinationsService）
        """
        # 尝试使用 Jinja2 模板，降级时用内置模板字符串
        prompts_dir = Path(__file__).parent.parent.parent.parent / "prompts"
        is_evolution = bool(evolution_lore.strip())
        template_file = "visual_prompt_evolve.j2" if is_evolution else "visual_prompt_generate.j2"

        prompt_text = ""
        try:
            from jinja2 import Environment, FileSystemLoader, StrictUndefined
            env = Environment(
                loader=FileSystemLoader(str(prompts_dir)),
                undefined=StrictUndefined,
                trim_blocks=True,
                lstrip_blocks=True,
            )
            tpl = env.get_template(template_file)
            ctx = {"entity": entity}
            if is_evolution:
                ctx["evolution_lore"] = evolution_lore
                ctx["old_prompt"] = old_prompt
            prompt_text = tpl.render(**ctx)
        except Exception as e:
            logger.warning(f"Jinja2 模板加载失败 ({template_file}): {e}，使用内置模板降级")
            if is_evolution:
                prompt_text = (
                    f"You are a professional AI image director.\n"
                    f"Base visual DNA (keep these unchanged): {old_prompt}\n"
                    f"Visual evolution in this chapter: {evolution_lore}\n"
                    f"Entity: {entity.get('name','')}, Category: {entity.get('category','')}\n"
                    f"Generate updated Chinese image prompt (comma-separated tags, 80-150 words). "
                    f"Output ONLY the Chinese prompt, no explanation."
                )
            else:
                prompt_text = (
                    f"You are a professional AI image director.\n"
                    f"Entity: {entity.get('name','')}, Category: {entity.get('category','')}\n"
                    f"Lore: {entity.get('lore','')}\n"
                    f"Visual aura: {entity.get('visual_aura','')}\n"
                    f"Generate a Chinese image prompt (comma-separated tags, 80-150 words, "
                    f"static visual elements only, end with 杰作, 最高画质, 极具细节, 8k). "
                    f"Output ONLY the Chinese prompt, no explanation."
                )

        logger.info(f"🎨 [VisualPrompt] 生成 {'迭代' if is_evolution else '初始'} Prompt: {entity.get('name','')}")
        for attempt in range(4):
            try:
                result = DeepSeekService.call_deepseek(prompt_text)
                # 清理：去除 markdown 代码块包装，提取纯文本
                result = result.strip()
                if result.startswith("```"):
                    lines = result.split("\n")
                    result = "\n".join(
                        l for l in lines
                        if not l.strip().startswith("```")
                    ).strip()
                if result and len(result) > 20:
                    logger.info(f"✅ [VisualPrompt] {entity.get('name','')} → {result[:80]}...")
                    return result
                logger.warning(f"[VisualPrompt] 第{attempt+1}次返回过短: {result[:50]}")
            except Exception as e:
                logger.error(f"[VisualPrompt] 第{attempt+1}次异常: {e}")
            wait_sec = min(5 * (2 ** attempt), 60)
            logger.warning(f"⚠️ [视觉Prompt] 第 {attempt+1} 次失败，退避 {wait_sec} 秒...")
            time.sleep(wait_sec)

        # 降级：返回基于 visual_aura 的简单 Prompt
        fallback = (
            f"{entity.get('visual_aura', '')} {entity.get('lore', '')[:50]}, "
            f"杰作, 最高画质, 极具细节, 8k"
        ).strip(", ")
        logger.warning(f"[VisualPrompt] 全部失败，使用降级 Prompt: {fallback[:60]}")
        return fallback
