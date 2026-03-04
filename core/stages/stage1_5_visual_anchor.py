"""
core/stages/stage1_5_visual_anchor.py
v2.8.0: Stage 1.5 — 视觉锚定初始化 (从 pipeline.py 提取)

⚠️  关键设计：资产分离（用户修正已落地）
═══════════════════════════════════════════════════════════════════
  设定参考图（三视图）= turnaround_url
    - 用于生成分镜首帧图时的 API 垫图 / 提示词约束
    - 存入 素材表.turnaround_url 字段
    - 绝不直接传给视频生成网关

  分镜首帧图 = image_url（才是 I2V 的实际入参）
    - 单人、带场景背景的正常构图
    - 存入 素材表.图片URL 字段
    - 由 Stage3 VideoDispatchStage 拉取后注入视频 API
═══════════════════════════════════════════════════════════════════

首次建档流程（两步生图）：
  Step 1: 三视图生成 → 存 turnaround_url（风格约束用）
  Step 2: 场景首帧图生成（使用原始 visual_prompt，不含三视图后缀）→ 存 image_url

演化流程（lore 变化时）：
  复用旧 seed，用新 evolution_lore 迭代更新 image_url
"""
import asyncio
import logging
import time
from .base_stage import BaseStage, PipelineContext

logger = logging.getLogger(__name__)

# 三视图 Prompt 后缀（仅用于生成设定参考图，不用于视频入参）
_TURNAROUND_SUFFIX = (
    ", character turnaround sheet, multiple views: front, side, back, "
    "plain white background, no background"
)


class VisualAnchorStage(BaseStage):
    """
    Stage 1.5: 视觉锚定。
    对本章 present_in_current=True 的实体进行三路判断：
      A. 无图 → 两步生图（三视图 + 场景首帧图）
      B. 有图 + lore 变化 → 迭代更新 image_url（保留旧 seed）
      C. 无变化 → 透传跳过
    """
    STAGE_NAME = "Stage1.5_VisualAnchor"

    def __init__(self, bitable, task_manager):
        self.bitable = bitable
        self.task_manager = task_manager

    async def run(self, context: PipelineContext) -> PipelineContext:
        """顶层 run()，暂不使用（由 PipelineOrchestrator 直接调用 run_chapter）。"""
        return context

    async def run_chapter(
        self,
        context: PipelineContext,
        chapter_name: str,
        present_entities: list,
        image_gateway: str = "aliyun",
        task_id: str = "",
    ) -> None:
        """
        处理单章节所有出场实体的视觉资产构建。
        直接修改 飞书素材表，并同步更新 context.scene_frame_urls / turnaround_urls。
        """
        if not present_entities:
            return

        from core.api.state import StateKeeper
        from core.services.image_service.aliyun_image_service import AliyunImageService
        from core.services.llm_service.deepseek_service import DeepSeekService

        state_keeper = StateKeeper()
        novel_id = context.novel_id
        sem = asyncio.Semaphore(2)
        current_gateways = [image_gateway]

        async def _init_image_svc(gateway_name: str):
            model_map = {
                "aliyun": "qwen-image-plus",
                "z_image_turbo": "z-image-turbo",
            }
            return AliyunImageService(model=model_map.get(gateway_name, "qwen-image-plus"))

        async def _process_one(entity: dict):
            entity_id = entity["entity_id"]
            entity_name = entity["name"]
            asset_type = entity.get("category", "角色")
            lore_changed = entity.get("lore_changed", False)

            async with sem:
                existing = await asyncio.to_thread(
                    self.bitable.get_asset_by_entity, entity_id, novel_id
                )

                # 路径 C：无变化，透传
                if existing.get("found") and not lore_changed:
                    logger.info(f"[Stage1.5] {entity_name} 无视觉变化，透传现有素材")
                    context.scene_frame_urls[entity_id] = existing.get("image_url", "")
                    return

                old_prompt = existing.get("visual_prompt", "") if existing.get("found") else ""
                old_seed = existing.get("seed") if existing.get("found") else None
                is_evolution = existing.get("found") and lore_changed

                evolution_lore = ""
                if is_evolution:
                    new_lore = entity.get("lore", "")
                    old_lore = entity.get("old_lore", "")
                    evolution_lore = f"本章视觉变化：{new_lore[:200]}" if new_lore else ""

                while True:
                    active_gateway = current_gateways[0]
                    try:
                        image_svc = await _init_image_svc(active_gateway)
                    except Exception as ie:
                        logger.error(f"[Stage1.5] 无法初始化生图客户端 {active_gateway}: {ie}")
                        await asyncio.sleep(5)
                        continue

                    # DeepSeek 生成英文视觉 Prompt（场景首帧图用）
                    visual_prompt = await asyncio.to_thread(
                        DeepSeekService.generate_visual_prompt,
                        entity, evolution_lore, old_prompt,
                    )
                    if not visual_prompt:
                        logger.warning(f"[Stage1.5] {entity_name} Prompt 生成失败，跳过")
                        break

                    quota_error_triggered = False
                    scene_frame_result = {}

                    try:
                        if is_evolution and old_seed:
                            # 路径 B：迭代更新，复用旧 seed
                            scene_frame_result = await image_svc.evolve_image(
                                original_seed=old_seed,
                                evolution_prompt=visual_prompt,
                            )
                        else:
                            # 路径 A 的 Step 1：生成三视图（设定参考图）
                            turnaround_prompt = visual_prompt + _TURNAROUND_SUFFIX
                            try:
                                turnaround_result = await image_svc.generate_image(
                                    prompt=turnaround_prompt
                                )
                                t_url = turnaround_result.get("url", "")
                                if t_url:
                                    context.turnaround_urls[entity_id] = t_url
                                    logger.info(
                                        f"[Stage1.5] {entity_name} 三视图生成成功，"
                                        f"存入 turnaround_urls (不进 I2V)"
                                    )
                                    # 写入飞书素材表 turnaround_url 字段（幂等追加）
                                    await asyncio.to_thread(
                                        self.bitable.upsert_asset_record,
                                        novel_id=novel_id,
                                        entity_id=entity_id,
                                        entity_name=entity_name,
                                        asset_type=asset_type,
                                        visual_prompt=turnaround_prompt,
                                        image_url=t_url,
                                        seed=turnaround_result.get("seed"),
                                        chapter_tag=f"turnaround@{chapter_name}",
                                        is_evolution=False,
                                        old_visual_prompt="",
                                    )
                            except Exception as te:
                                logger.warning(f"[Stage1.5] {entity_name} 三视图生成失败（非致命）: {te}")

                            # 路径 A 的 Step 2：生成场景首帧图（真正的 I2V 入参）
                            scene_frame_result = await image_svc.generate_image(
                                prompt=visual_prompt
                            )

                    except Exception as e:
                        err_str = str(e)
                        if any(k in err_str for k in ["401", "unauthorized", "quota", "balance", "Arrearage"]):
                            logger.error(f"[Stage1.5] {active_gateway} 配额拦截: {err_str}")
                            quota_error_triggered = True
                        else:
                            logger.error(f"[Stage1.5] {entity_name} 生图失败: {err_str}")
                            break

                    if quota_error_triggered:
                        # 热重载等待逻辑（与原 pipeline.py 一致）
                        if task_id:
                            from core.api.state import StateKeeper as SK
                            sk = SK()
                            await sk.append_log(task_id, {
                                "stage": "quota_error", "status": "error",
                                "chapter": f"生图任务({entity_name})",
                                "message": f"[{active_gateway}] 账户配额耗尽！请在左侧切换【生图模型】继续",
                                "ts": time.time()
                            }, force_save=True)
                        if not task_id:
                            logger.error("[Stage1.5] 无全局 task_id，强制中止")
                            break
                        while True:
                            await asyncio.sleep(5)
                            master_task = await state_keeper.get_task(task_id)
                            db_gw = master_task.get("image_gateway", active_gateway)
                            if db_gw != active_gateway:
                                current_gateways[0] = db_gw
                                quota_error_triggered = False
                                break
                            if master_task and master_task.get("retry_signal"):
                                await state_keeper.update_task(task_id, {"retry_signal": False}, force_save=True)
                                quota_error_triggered = False
                                break
                            if master_task and master_task.get("finished"):
                                return
                        continue

                    if scene_frame_result.get("status") != "success":
                        logger.error(f"[Stage1.5] {entity_name} 场景首帧图生成失败: {scene_frame_result}")
                        break

                    # 成功：存入 context 和飞书素材表
                    image_url = scene_frame_result["url"]
                    seed = scene_frame_result["seed"]
                    context.scene_frame_urls[entity_id] = image_url

                    await asyncio.to_thread(
                        self.bitable.upsert_asset_record,
                        novel_id=novel_id,
                        entity_id=entity_id,
                        entity_name=entity_name,
                        asset_type=asset_type,
                        visual_prompt=visual_prompt,
                        image_url=image_url,
                        seed=seed,
                        chapter_tag=chapter_name,
                        is_evolution=is_evolution,
                        old_visual_prompt=old_prompt,
                    )
                    logger.info(
                        f"[Stage1.5] {entity_name} @{chapter_name} "
                        f"{'[EVOLVE]' if is_evolution else '[INIT]'} 完美落表"
                    )
                    break  # 成功，跳出 while True

        tasks = [_process_one(e) for e in present_entities]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        errs = [r for r in results if isinstance(r, Exception)]
        if errs:
            logger.warning(f"[Stage1.5] {len(errs)} 个实体处理异常: {errs}")
        logger.info(
            f"[Stage1.5] chapter={chapter_name} 共处理 {len(present_entities)} 个实体，"
            f"场景首帧图命中 {len(context.scene_frame_urls)} 个，"
            f"三视图命中 {len(context.turnaround_urls)} 个"
        )
