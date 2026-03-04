from core.config import settings
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

ALIYUN_API_KEY = settings.ALIYUN_API_KEY or settings.DASHSCOPE_API_KEY
if ALIYUN_API_KEY:
    FREE_PROXY_ENDPOINTS.append({
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "api_key": ALIYUN_API_KEY,
        "model_map": {"deepseek-chat": "qwen-plus"}, # 使用 qwen-plus 作为 deepseek-chat 的降级平替
        "name": "Aliyun Qwen-Plus (官方稳定保底)"
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
        if resp.status_code != 200:
            logger.error(f"HTTP {resp.status_code} - {resp.text}")
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        if not content or len(content) < 5:
            raise ValueError("响应内容过短或为空")
        return content

    @staticmethod
    def call_deepseek(message: str, force_index: int = None):
        """
        依次尝试各个免费反向代理调用 DeepSeek。
        如果指定了 force_index，则优先从该索引对应的代理开始尝试，确保外层重试轮循不同的代理。
        """
        endpoints = FREE_PROXY_ENDPOINTS[:]
        if force_index is not None:
            idx = force_index % len(endpoints)
            # Reorder list to start at force_index
            endpoints = endpoints[idx:] + endpoints[:idx]

        for proxy in endpoints:
            proxy_model = proxy["model_map"].get("deepseek-chat", "deepseek-v3")
            logger.info(f"🔁 尝试大模型服务: [{proxy['name']}] model={proxy_model}")
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
            
        raise Exception("❌ 所有大模型公共产出节点均失效，请稍后再试或配置 SILICONFLOW_API_KEY / ALIYUN_API_KEY。")

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
我将输入一部小说，建立这部作品的视觉与逻辑地基。

你的任务：
提取并总结核心实体。特别注意：世界观与角色的【视觉氛围】必须是具有**强连续性、电影感和便于视频动态生成的**。我们最终生成的是10秒一段的连续短片。

输出要求：
请输出一段 JSON 数组，用于写入飞书【记忆中枢】表格。
格式如下：
[
  {{"category": "类别(如世界观/地标/道具/角色)", "name": "词条名", "lore": "深层设定逻辑", "visual_aura": "详细的视觉氛围与美学隐喻(要求：电影级光影、材质质感、适合视频动态展现的具体特征)"}}
]

小说原文：
{sample}
"""
        logger.info("DeepSeek 提取记忆中枢世界观...")
        for attempt in range(4):
            try:
                result_text = DeepSeekService.call_deepseek(prompt, force_index=attempt)
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
1. 存续审计（老角色）：对照上述“活跃实体池”，审视他们在当前章节中的存续状态与设定更新。必须通过 UPDATE 返回其心跳状态。
2. 新星探测（新角色）：寻找本章内符合条件但【不在活跃池中】的全新重要实体。
3. 状态锚定（极高优先级）：为了支撑下一集的 10秒 视频【无缝承接】，你必须在 last_known_state 中详细记录该实体在本章落幕时的【最终物理位置、具体姿势形态、遗留的面部情绪】（如："站在崖边拔剑，眼神决绝，长发被风吹乱"），这比剧情概述更重要。
4. 如果角色未出场，也需推断其潜伏的 last_known_state。

任务：
对比现有记忆库与当前章节，输出以下三类指令：
1. UPDATE: 必须包含 present_in_current(布尔值)。必须在 last_known_state 详尽刻画章节结尾的视觉/物理遗留状态。
2. INSERT: 用于探测全新出现的词条。必须包含 present_in_current: true。
3. ARCHIVE: 该活跃池中的词条已在本章明确退出剧情。

注意：每条输出必须包含 action 字段。

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
    "visual_aura": "视觉隐喻与固定外观特征",
    "last_known_state": "本章结束时，他在客栈窗前饮酒，眼神寂寥，残阳打在侧脸",
    "implicit_carry": "他的缺席让主角产生疑虑"
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
                result_text = DeepSeekService.call_deepseek(prompt, force_index=attempt)
                parsed = DeepSeekService.extract_json_from_deepseek(result_text)
                if isinstance(parsed, list):
                    # 允许空数组（表示该章无设定变化）
                    if len(parsed) == 0:
                        logger.info("✅ [delta_extract] 该章节无设定变化，返回空列表")
                        return []
                    if "action" in parsed[0]:
                        logger.info(f"✅ [delta_extract] 成功解析 {len(parsed)} 条变更指令")
                        return parsed
                    
                raise ValueError(f"增量提取数据格式错误 (期待包含 action 字段的 JSON 数组): {str(result_text)[:100]}...")
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
- 极度重要：你的第一个分镜(scene)的开场画面与运镜，必须严丝合缝地接在以下画面的【最后一秒】之后。
- 上集结尾剧情: {prev_summary}
- 上集结尾画面: {prev_visual}
- 上集遗留情绪: {prev_emotion}
- 上集结尾运镜: {prev_camera}
- 上集悬念(Hook): {prev_hook}
"""

        prompt = f"""# Role
你是一位深谙竖屏 AI 短剧流量密码的顶级总导演。你的任务是将小说文本转化为可以供视频大模型直接生成的【10秒视频分镜】。

【你的记忆空间】（由系统动态传入）：
{memory_context_str}
{prompt_prev}
【当前待拍摄剧本】：
{chunk_text}

你的执导任务：

1. 【核心：10秒视频节奏论与防碎片化】(违背此条将导致生成大量重复废弃视频)：
- 每个 `scene_num` (分镜) 必须且只能代表一段长达 10 秒 的真实视频！
- 绝对禁止微观切分：不要把属于同一时间、同一场景下连贯发生的微小动作（比如“他抬起头”、“她皱了皱眉”、“他说完一句话”）切分成多个不同的分镜！
- 强制合并合并：你应该把剧本中几句话、一段交流、一组连贯的互动合并进同一个 10秒的分镜内。比如：“男主推门而入，看着眼前的狼藉，震惊地跌坐在地” 这是一个完整的 10秒镜头，而不是三个分镜！
- 防治画面重复：如果连续两个分镜的视觉要素高度一致且只是小动作变化，请立即将它们合并为一个！必须确保时间线实质推进。

2. 【无缝承接与运镜法则】(极高优先级)：
- 当存在【前情接续强制约束】时，你的第一个分镜的 visual_prompt 必须明确写出：如何承接上一个镜头的结尾状态。
- 如果上一集是“拉近锁定女主震惊的脸”，本集开头最好是“从女主震惊的面部特写开始，镜头随之向右摇摄拉开，展示她所看到的画面”。禁止发生空间跳跃或姿势突变！
- 每个分镜的视觉描述都要写明摄像机的运动轨迹（如：平移、推轨、环绕、定焦特写逐渐拉远）。

3. 首尾闭环与钩子前置：
- 最后一个分镜结尾，必须预埋一个强烈的“悬念 / 危机 / 身份反转”钩子（Hook），以此诱导观众继续看下一集！

4. 光影与质感调度：
- 不要死板堆砌相机参数。用充满电影感和文学性的词汇描绘光影、动态动作、环境交互。

5. 实体追踪与视觉强锚定：
- 每一幕必须列出涉及的 entity_id。若实体出场，必须在 visual_prompt 中以极高权重继承【记忆空间】里的视觉特征。

输出规范 (严格 JSON 数组)：
[
  {{
    "scene_num": 1,
    "summary": "简述本集(10秒)内包含的完整动作流",
    "entity_ids": ["e_xxxx_001"],
    "director_notes": "导演手记",
    "emotion": "本集核心情绪",
    "camera": "转场与运镜描述（必须指明本集内镜头的流动轨迹）",
    "hook": "本集悬念，最后一个镜头必须有",
    "visual_prompt": "高维画面描述(直接用于生图和生视频)。包含：承接上集的起幅状态、10秒内主体的完整连贯动作、运镜流变、光影质感。",
    "audio_prompt": "画外音台词"
  }}
]
"""
        for attempt in range(4):
            try:
                result_text = DeepSeekService.call_deepseek(prompt, force_index=attempt)
                logger.info(f"DeepSeek Two-Stage Stage 2 Output (Length: {len(result_text)}):\n{result_text[:500]}...")
                parsed = DeepSeekService.extract_json_from_deepseek(result_text)
                if isinstance(parsed, list) and len(parsed) > 0 and ("visual_prompt" in parsed[0] or "master_prompt" in parsed[0]):
                    return parsed
                else:
                    raise ValueError(f"分镜生成结果缺乏预期格式或缺失 visual_prompt。类型: {type(parsed)}")
            except Exception as e:
                logger.error(f"Error calling deepseek (attempt {attempt}): {e}")
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
                    f"You are a professional AI cinematic image director.\n"
                    f"Base visual DNA (keep these unchanged): {old_prompt}\n"
                    f"Visual evolution in this chapter: {evolution_lore}\n"
                    f"Entity: {entity.get('name','')}, Category: {entity.get('category','')}\n"
                    f"Generate updated Chinese image prompt (comma-separated tags, 80-150 words). "
                    f"The image must have cinematic lighting and be ready for dynamic video animation. "
                    f"Output ONLY the Chinese prompt, no explanation."
                )
            else:
                prompt_text = (
                    f"You are a professional AI cinematic image director.\n"
                    f"Entity: {entity.get('name','')}, Category: {entity.get('category','')}\n"
                    f"Lore: {entity.get('lore','')}\n"
                    f"Visual aura: {entity.get('visual_aura','')}\n"
                    f"Generate a Chinese image prompt (comma-separated tags, 80-150 words). "
                    f"DO NOT use static restrictors. Emphasize cinematic lighting, dynamic tension, and readiness for video animation. "
                    f"End with: 电影感光影, 动态张力, 杰作, 最高画质, 极具细节, 8k. "
                    f"Output ONLY the Chinese prompt, no explanation."
                )

        logger.info(f"🎨 [VisualPrompt] 生成 {'迭代' if is_evolution else '初始'} Prompt: {entity.get('name','')}")
        for attempt in range(4):
            try:
                result = DeepSeekService.call_deepseek(prompt_text, force_index=attempt)
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

    @staticmethod
    def generate_visual_prompt_for_asset_type(
        entity: dict,
        asset_type: str = "scene_frame",
        evolution_lore: str = "",
        old_prompt: str = "",
        scene_context: dict = None,
    ) -> str:
        """
        v2.8.0 Phase 2: Prompt 分流路由 — 三视图 vs 场景首帧图。

        ┌─────────────────┬──────────────────────────────────┐
        │  asset_type     │  J2 模板                          │
        ├─────────────────┼──────────────────────────────────┤
        │ "turnaround"    │ visual_asset_turnaround.j2       │
        │                 │  → 纯白背景三视图（风格约束用）   │
        ├─────────────────┼──────────────────────────────────┤
        │ "scene_frame"   │ visual_asset_scene_frame.j2      │
        │  (默认)         │  → 带场景背景的首帧图（I2V入参）  │
        └─────────────────┴──────────────────────────────────┘

        Args:
            entity        : 实体字典（含 category/name/lore/visual_aura）
            asset_type    : "turnaround" 或 "scene_frame"
            evolution_lore: 本章视觉变化描述（非空时代入模板）
            old_prompt    : 上一章节 Prompt（迭代基线）
            scene_context : 场景上下文（scene_frame 模式时注入 summary/emotion/camera）

        Returns:
            str: DeepSeek 生成的中文 Prompt，供相应的 AliyunImageService 调用
        """
        prompts_dir = Path(__file__).parent.parent.parent.parent / "prompts"

        # ── 模板选择 ────────────────────────────────────────────────────────
        if asset_type == "turnaround":
            template_file = "visual_asset_turnaround.j2"
            log_mode = "三视图[设定参考图，不进I2V]"
            fallback_suffix = (
                ", character turnaround sheet, multiple views front side back, "
                "plain white background, model sheet, highly detailed, masterpiece"
            )
        else:
            template_file = "visual_asset_scene_frame.j2"
            log_mode = "场景首帧图[I2V实际入参]"
            fallback_suffix = (
                ", real scene background, cinematic lighting, dynamic tension, "
                "16:9 aspect ratio, masterpiece, best quality, highly detailed, 8k"
            )

        logger.info(
            f"🎨 [VisualPrompt|{asset_type}] 生成 {log_mode}: {entity.get('name','')}"
        )

        # ── 尝试 Jinja2 模板 ─────────────────────────────────────────────────
        prompt_text = ""
        try:
            from jinja2 import Environment, FileSystemLoader, UndefinedError
            env = Environment(
                loader=FileSystemLoader(str(prompts_dir)),
                trim_blocks=True,
                lstrip_blocks=True,
            )
            tpl = env.get_template(template_file)
            ctx: dict = {"entity": entity}
            if evolution_lore:
                ctx["evolution_lore"] = evolution_lore
            if old_prompt:
                ctx["old_prompt"] = old_prompt
            if scene_context and isinstance(scene_context, dict):
                ctx["scene_context"] = scene_context
            prompt_text = tpl.render(**ctx)
        except Exception as e:
            logger.warning(
                f"[VisualPrompt|{asset_type}] Jinja2 模板 {template_file} 失败: {e}，降级"
            )
            # 内置降级模板（两套）
            base = (
                f"实体名称: {entity.get('name','')}\n"
                f"类别: {entity.get('category','')}\n"
                f"设定: {entity.get('lore','')[:200]}\n"
                f"视觉氛围: {entity.get('visual_aura','')[:200]}\n"
            )
            if evolution_lore:
                base += f"本章变化: {evolution_lore}\n"
            if asset_type == "turnaround":
                prompt_text = (
                    base + "生成三视图设定参考图（纯白背景、正侧背三角度、标准站姿），"
                    "中文标签串格式，80-120词。"
                    "末尾加：character turnaround, plain white background, model sheet"
                )
            else:
                scene_desc = ""
                if scene_context:
                    scene_desc = f"场景: {scene_context.get('summary','')[:100]}\n"
                prompt_text = (
                    base + scene_desc +
                    "生成场景首帧图（含真实背景环境、角色动态姿态、电影感光影），"
                    "横版16:9构图，中文标签串格式，80-150词。"
                    "末尾加：cinematic lighting, dynamic tension, 16:9, masterpiece, 8k"
                )

        # ── 调用 DeepSeek 生成 ───────────────────────────────────────────────
        for attempt in range(4):
            try:
                result = DeepSeekService.call_deepseek(prompt_text)
                result = result.strip()
                if result.startswith("```"):
                    result = "\n".join(
                        l for l in result.split("\n")
                        if not l.strip().startswith("```")
                    ).strip()
                if result and len(result) > 20:
                    logger.info(
                        f"✅ [VisualPrompt|{asset_type}] {entity.get('name','')} "
                        f"→ {result[:80]}..."
                    )
                    return result
                logger.warning(f"[VisualPrompt|{asset_type}] 第{attempt+1}次返回过短")
            except Exception as e:
                logger.error(f"[VisualPrompt|{asset_type}] 第{attempt+1}次异常: {e}")
            time.sleep(min(5 * (2 ** attempt), 60))

        # 终极降级
        fallback = (
            f"{entity.get('visual_aura', '')} {entity.get('lore','')[:50]}"
            + fallback_suffix
        ).strip(", ")
        logger.warning(f"[VisualPrompt|{asset_type}] 全部失败，降级: {fallback[:60]}")
        return fallback

