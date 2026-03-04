"""
core/stages/stage2_storyboard.py
v2.8.0: Stage 2 — 分镜生成 (从 pipeline.py 提取)

职责：
  - 按章节顺序串行，内部切片并发（Semaphore=3）
  - 每切片最多 3 次重试
  - 生成成功后写入飞书剧本拆解表
  - 装配 (prompt, image_url) 元组（从 context.scene_frame_urls 匹配），存入 context.visual_prompts
"""
import asyncio
import logging
from .base_stage import BaseStage, PipelineContext

logger = logging.getLogger(__name__)


class StoryboardStage(BaseStage):
    """Stage 2: 分镜生成。按章节顺序处理，内部切片 Semaphore(3) 并发。"""
    STAGE_NAME = "Stage2_Storyboard"

    def __init__(self, bitable, task_manager):
        self.bitable = bitable
        self.task_manager = task_manager

    async def run(self, context: PipelineContext) -> PipelineContext:
        """
        处理 context.files 中的所有章节文件，串行迭代后写入 context.visual_prompts。
        """
        from core.services.llm_service.deepseek_service import DeepSeekService
        from core.protocols.render_protocol import RenderProtocol

        episode_counter = [1]
        active_memories = context.active_memories

        for ch_idx, file_info in enumerate(context.files):
            chapter_scenes, chapter_name = await self._run_chapter(
                context=context,
                file_info=file_info,
                ch_idx=ch_idx,
                active_memories=active_memories,
                DeepSeekService=DeepSeekService,
            )
            if not chapter_scenes:
                logger.warning(f"[Stage2] {file_info.get('name')} 无分镜输出，跳过")
                continue

            # RenderProtocol 注入视觉配置
            for scene in chapter_scenes:
                prompt_raw = scene.get("master_prompt", scene.get("visual_prompt", ""))
                if isinstance(prompt_raw, dict):
                    prompt_raw = "\n".join(f"{k}: {v}" for k, v in prompt_raw.items())
                vp = {**context.video_params, "_sandbox_mode": True} if context.sandbox_mode else context.video_params
                scene["visual_prompt"] = RenderProtocol.inject_render_config(
                    str(prompt_raw), vp, context.gateway
                )

            # 序号连续递增
            start_ep = episode_counter[0]
            episode_counter[0] += len(chapter_scenes)

            ids = self.bitable.insert_new_parsed_scenes(
                chapter_scenes,
                episode_start=start_ep,
                task_id=context.task_id,
                sandbox_mode=context.sandbox_mode,
                novel_id=context.novel_id,
                chapter_name=chapter_name,
                gateway=context.gateway,
            )
            context.all_scenes.extend(chapter_scenes)

            # 装配 (prompt, image_url) 供 Stage3
            if not context.sandbox_mode:
                asset_cache = await self._fetch_asset_cache(context, chapter_scenes)
                for sc in chapter_scenes:
                    vp_str = sc.get("visual_prompt", "")
                    if not (vp_str and isinstance(vp_str, str) and len(vp_str) > 10):
                        continue
                    img_url = ""
                    for eid in (sc.get("entity_ids") or []):
                        # 优先用 scene_frame_urls（I2V 正确资产），降级查 asset_cache（飞书表）
                        if str(eid) in context.scene_frame_urls:
                            img_url = context.scene_frame_urls[str(eid)]
                            break
                        if str(eid) in asset_cache:
                            img_url = asset_cache[str(eid)]
                            break
                    context.visual_prompts.append((vp_str, img_url) if img_url else vp_str)

            logger.info(
                f"[Stage2] {chapter_name} 写入 {len(ids)} 条，"
                f"集数 {start_ep}~{episode_counter[0]-1}"
            )

        return context

    async def _fetch_asset_cache(self, context: PipelineContext, chapter_scenes: list) -> dict:
        """从飞书素材表并发查询本章实体的 image_url（I2V 备用）。"""
        chapter_entity_ids = set()
        for sc in chapter_scenes:
            for eid in (sc.get("entity_ids") or []):
                if eid:
                    chapter_entity_ids.add(str(eid))

        if not chapter_entity_ids:
            return {}

        async def _fetch_one(eid: str):
            a = await asyncio.to_thread(
                self.bitable.get_asset_by_entity, eid, context.novel_id
            )
            url = a.get("image_url", "") if a.get("found") else ""
            return eid, url

        results = await asyncio.gather(
            *[_fetch_one(eid) for eid in chapter_entity_ids],
            return_exceptions=True,
        )
        return {
            eid: url
            for eid, url in results
            if not isinstance((eid, url), Exception) and isinstance(url, str) and url
        }

    async def _run_chapter(
        self, context, file_info, ch_idx, active_memories, DeepSeekService
    ) -> tuple:
        """单章节分镜生成，内部切片顺序重试。"""
        cname = file_info.get("name", f"chapter_{ch_idx+1}")
        ctext = file_info.get("content", "")
        chunks = DeepSeekService.smart_chunk_text(ctext, context.chunk_size)

        rolling_context = [None]
        chapter_scenes = []
        MAX_RETRY = 3

        for chunk_idx, chunk_text in enumerate(chunks):
            chunk_success = False
            for attempt in range(1, MAX_RETRY + 1):
                try:
                    def _mem_ctx():
                        relevant = [
                            m for m in active_memories
                            if (m.get("name") and m["name"] in chunk_text)
                            or m.get("category") in ["世界观", "氛围", "基调"]
                        ]
                        ctx_str = "【全局世界观与当前段落相关的记忆词条】\n"
                        for rm in relevant:
                            ctx_str += (
                                f"- [{rm.get('category','设定')}] {rm.get('name','')} "
                                f"(ID:{rm.get('entity_id','')}): {rm.get('lore','')}\n"
                            )
                        return ctx_str

                    res = await asyncio.to_thread(
                        DeepSeekService.generate_scenes_for_chunk,
                        chunk_text, _mem_ctx(), rolling_context[0]
                    )
                    if isinstance(res, list) and res:
                        chapter_scenes.extend(res)
                        last = res[-1]
                        rolling_context[0] = {
                            "summary": last.get("summary", ""),
                            "visual_prompt": last.get("visual_prompt", ""),
                            "emotion": last.get("emotion", ""),
                            "camera": last.get("camera", ""),
                            "hook": last.get("hook", ""),
                        }
                        chunk_success = True
                        break
                    else:
                        if attempt < MAX_RETRY:
                            await asyncio.sleep(2 * attempt)
                except Exception as e:
                    logger.error(f"[Stage2][{cname}] 切片{chunk_idx+1} 第{attempt}次异常: {e}")
                    if attempt < MAX_RETRY:
                        await asyncio.sleep(2 * attempt)
            if not chunk_success:
                logger.error(f"[Stage2][{cname}] 切片{chunk_idx+1} 彻底失败，跳过")

        return chapter_scenes, cname
