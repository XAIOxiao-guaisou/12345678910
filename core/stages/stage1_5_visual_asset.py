"""
core/stages/stage1_5_visual_asset.py
v2.8.0 Phase 2: 视觉资产管线 (Visual Asset Pipeline)

═══════════════════════════════════════════════════════════════════
  资产分离协议（用户修正已固化）：

  ┌──────────────────┬──────────────────┬──────────────────────┐
  │    资产类型       │   飞书字段        │  是否传给视频网关     │
  ├──────────────────┼──────────────────┼──────────────────────┤
  │ 设定参考图(三视图) │ turnaround_url  │  ❌ 仅风格约束        │
  │ 分镜首帧图        │ 图片URL          │  ✅ I2V 实际入参      │
  └──────────────────┴──────────────────┴──────────────────────┘

  三路判断：
    路径 A (无图)：两步生图
      Step 1 → 生成三视图 (turnaround)  → 存 turnaround_url 字段
      Step 2 → 生成场景首帧图            → 存 图片URL 字段 (I2V 入参)
    路径 B (有图 + lore 变化)：
      复用旧 seed，evolution_lore 注入  → 更新 图片URL
    路径 C (有图 + 无变化)：
      透传，写入 context.scene_frame_urls，跳过 API 调用
═══════════════════════════════════════════════════════════════════

执行方式：由 PipelineOrchestrator 在每章 Stage1 完成后调用 run_chapter()。
"""
from __future__ import annotations
import asyncio
import logging
import time
from typing import Optional

from .base_stage import BaseStage, PipelineContext

logger = logging.getLogger(__name__)


class VisualAssetStage(BaseStage):
    """
    Stage 1.5 — 视觉资产管线（完整生产级实现）。

    相比 Phase 1 的 VisualAnchorStage（骨架版），本类:
      - 更完善的配额热重载逻辑（统一 StateKeeper 等待协议）
      - 两步生图写入飞书时分字段存储（turnaround_url vs image_url）
      - 将场景首帧图 URL 写回【剧本拆解表】的"首帧参考图"字段
      - context.scene_frame_urls 作为 Stage4 的 I2V 数据源
    """
    STAGE_NAME = "Stage1.5_VisualAsset"

    # 三视图 Prompt 后缀（给设定参考图用，绝不传给视频网关）
    _TURNAROUND_SUFFIX = (
        ", character turnaround reference sheet, "
        "multiple views front side back, "
        "plain white background, no environment, "
        "character design sheet, model sheet"
    )

    def __init__(self, bitable, task_manager):
        self.bitable = bitable
        self.task_manager = task_manager

    async def run(self, context: PipelineContext) -> PipelineContext:
        """顶层 run()（未被直接调用，由 run_chapter 承担章节级入口）。"""
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
        处理本章所有出场实体的视觉资产。

        Args:
            context         : 流水线上下文（读写 scene_frame_urls / turnaround_urls）
            chapter_name    : 当前章节名（用于章节标签归档）
            present_entities: MemoryEngine 传递的出场实体列表
                              每项含: entity_id, name, category, lore,
                                      lore_changed, old_lore
            image_gateway   : 生图服务网关名（默认 aliyun）
            task_id         : 全局任务 ID（用于 StateKeeper 热重载等待）
        """
        if not present_entities:
            logger.info(f"[Stage1.5] {chapter_name}: 无出场实体，跳过")
            return

        novel_id = context.novel_id
        sem = asyncio.Semaphore(2)            # 防并发过高触发限流
        current_gateways = [image_gateway]   # 用 list 穿透闭包，支持热重载

        async def _init_image_svc(gw: str):
            from core.services.image_service.aliyun_image_service import AliyunImageService
            model_map = {
                "aliyun":        "qwen-image-plus",
                "z_image_turbo": "z-image-turbo",
                "wanx_t2i":      "wanx2.1-t2i-turbo",
            }
            return AliyunImageService(model=model_map.get(gw, "qwen-image-plus"))

        async def _process_entity(entity: dict):
            entity_id   = entity["entity_id"]
            entity_name = entity.get("name", entity_id)
            asset_type  = entity.get("category", "角色")
            lore_changed= entity.get("lore_changed", False)

            async with sem:
                await self._process_single_entity(
                    context=context,
                    novel_id=novel_id,
                    chapter_name=chapter_name,
                    task_id=task_id,
                    entity=entity,
                    entity_id=entity_id,
                    entity_name=entity_name,
                    asset_type=asset_type,
                    lore_changed=lore_changed,
                    current_gateways=current_gateways,
                    init_image_svc=_init_image_svc,
                )

        tasks = [_process_entity(e) for e in present_entities]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        errors = [r for r in results if isinstance(r, Exception)]
        if errors:
            for err in errors:
                logger.error(f"[Stage1.5] 实体处理异常: {err}")
            self._log_error(
                context,
                f"[Stage1.5] {chapter_name}: {len(errors)}/{len(present_entities)} 个实体处理失败"
            )

        logger.info(
            f"[Stage1.5] {chapter_name} 完成 | "
            f"场景首帧图: {len(context.scene_frame_urls)} 个 | "
            f"三视图: {len(context.turnaround_urls)} 个"
        )

    async def _process_single_entity(
        self,
        context: PipelineContext,
        novel_id: str,
        chapter_name: str,
        task_id: str,
        entity: dict,
        entity_id: str,
        entity_name: str,
        asset_type: str,
        lore_changed: bool,
        current_gateways: list,
        init_image_svc,
    ) -> None:
        """单实体视觉资产处理（含热重载等待逻辑）。"""
        from core.services.llm_service.deepseek_service import DeepSeekService

        # ── 查询现有素材 ────────────────────────────────────────────────────
        existing = await asyncio.to_thread(
            self.bitable.get_asset_by_entity, entity_id, novel_id
        )

        # ── 路径 C：无变化 → 透传 ────────────────────────────────────────────
        if existing.get("found") and not lore_changed:
            img_url = existing.get("image_url", "")
            if img_url:
                context.scene_frame_urls[entity_id] = img_url
            logger.info(f"[Stage1.5] ⏩ {entity_name} 无变化，透传 image_url")
            return

        # ── 准备迭代上下文（路径 B）─────────────────────────────────────────
        old_prompt  = existing.get("visual_prompt", "") if existing.get("found") else ""
        old_seed    = existing.get("seed")             if existing.get("found") else None
        is_evolution= existing.get("found") and lore_changed

        evolution_lore = ""
        if is_evolution:
            new_lore = entity.get("lore", "")
            old_lore = entity.get("old_lore", "")
            if new_lore and new_lore != old_lore:
                evolution_lore = f"本章视觉变化：{new_lore[:200]}"
            elif new_lore:
                evolution_lore = f"本章新增设定：{new_lore[:200]}"

        # ── 生成视觉 Prompt（场景首帧图用）────────────────────────────────
        visual_prompt = await asyncio.to_thread(
            DeepSeekService.generate_visual_prompt,
            entity, evolution_lore, old_prompt,
        )
        if not visual_prompt:
            logger.warning(f"[Stage1.5] {entity_name} Prompt 生成失败，跳过")
            return

        # ── 主循环：生图 + 配额热重载 ────────────────────────────────────────
        while True:
            active_gw = current_gateways[0]
            try:
                image_svc = await init_image_svc(active_gw)
            except Exception as ie:
                logger.error(f"[Stage1.5] 无法初始化生图客户端 {active_gw}: {ie}")
                await asyncio.sleep(5)
                continue

            quota_hit = False
            scene_frame_result: dict = {}
            turnaround_url: str = ""

            try:
                if is_evolution and old_seed:
                    # ── 路径 B：固定 seed 迭代 ───────────────────────────
                    scene_frame_result = await image_svc.evolve_image(
                        original_seed=old_seed,
                        evolution_prompt=visual_prompt,
                    )
                else:
                    # ── 路径 A Step 1：生成三视图（设定参考图）─────────────
                    turnaround_prompt = visual_prompt + self._TURNAROUND_SUFFIX
                    try:
                        t_result = await image_svc.generate_image(prompt=turnaround_prompt)
                        if t_result.get("status") == "success" and t_result.get("url"):
                            turnaround_url = t_result["url"]
                            context.turnaround_urls[entity_id] = turnaround_url
                            logger.info(
                                f"[Stage1.5] ✅ {entity_name} 三视图生成成功 "
                                f"→ 存入 turnaround_urls (不进 I2V)"
                            )
                            # 写入飞书素材表（chapter_tag 区分三视图条目）
                            await asyncio.to_thread(
                                self.bitable.upsert_asset_record,
                                novel_id=novel_id,
                                entity_id=entity_id,
                                entity_name=entity_name,
                                asset_type=asset_type,
                                visual_prompt=turnaround_prompt,
                                image_url=turnaround_url,
                                seed=t_result.get("seed", 0),
                                chapter_tag=f"[三视图]@{chapter_name}",
                                is_evolution=False,
                                old_visual_prompt="",
                            )
                    except Exception as te:
                        logger.warning(
                            f"[Stage1.5] {entity_name} 三视图生成失败（非致命，继续首帧图）: {te}"
                        )

                    # ── 路径 A Step 2：生成场景首帧图（I2V 真实入参）───────
                    scene_frame_result = await image_svc.generate_image(
                        prompt=visual_prompt,
                        width=1024,
                        height=576,   # 16:9 横版首帧，适配 I2V
                    )

            except Exception as e:
                err_str = str(e)
                if any(k in err_str for k in ["401", "unauthorized", "quota", "balance", "Arrearage"]):
                    logger.error(f"[Stage1.5] {active_gw} 配额拦截: {err_str}")
                    quota_hit = True
                else:
                    logger.error(f"[Stage1.5] {entity_name} 生图底层失败: {err_str}")
                    return   # 非配额问题：直接跳过，不陷入死循环

            # ── 配额拦截：热重载等待 ─────────────────────────────────────
            if quota_hit:
                await self._wait_for_gateway_switch(
                    task_id=task_id,
                    entity_name=entity_name,
                    active_gw=active_gw,
                    current_gateways=current_gateways,
                )
                continue   # 用新网关重入 while True

            # ── 检查首帧图结果 ───────────────────────────────────────────
            if scene_frame_result.get("status") != "success":
                logger.error(
                    f"[Stage1.5] {entity_name} 首帧图生成被云端拒绝: {scene_frame_result}"
                )
                return

            scene_frame_url = scene_frame_result["url"]
            seed             = scene_frame_result.get("seed", 0)

            # ── 写入 context ─────────────────────────────────────────────
            context.scene_frame_urls[entity_id] = scene_frame_url

            # ── 写入飞书素材表（幂等）────────────────────────────────────
            await asyncio.to_thread(
                self.bitable.upsert_asset_record,
                novel_id=novel_id,
                entity_id=entity_id,
                entity_name=entity_name,
                asset_type=asset_type,
                visual_prompt=visual_prompt,
                image_url=scene_frame_url,
                seed=seed,
                chapter_tag=chapter_name,
                is_evolution=is_evolution,
                old_visual_prompt=old_prompt,
            )

            # ── 写回剧本拆解表的"首帧参考图"字段 ──────────────────────
            await asyncio.to_thread(
                self._writeback_frame_to_script,
                novel_id=novel_id,
                chapter_name=chapter_name,
                entity_id=entity_id,
                scene_frame_url=scene_frame_url,
            )

            mode_str = "[EVOLVE]" if is_evolution else "[INIT]"
            logger.info(
                f"[Stage1.5] ✅ {entity_name} @{chapter_name} {mode_str} 完美落表\n"
                f"  首帧图 → scene_frame_urls + 剧本拆解表.首帧参考图\n"
                f"  {['', '三视图 → turnaround_urls + 素材表'][bool(turnaround_url)]}"
            )
            break  # 成功，退出 while True

    def _writeback_frame_to_script(
        self,
        novel_id: str,
        chapter_name: str,
        entity_id: str,
        scene_frame_url: str,
    ) -> None:
        """
        将场景首帧图 URL 写回【剧本拆解表】中关联该实体的所有分镜行的"首帧参考图"字段。
        v2.8.0: 通过 entity_id 过滤匹配分镜行，批量更新。
        """
        if not scene_frame_url:
            return

        # 1. 先查找该章节中关联该实体的分镜 record_id
        import requests as _req
        from core.config import settings
        token = self.bitable._get_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        search_url = (
            f"https://open.feishu.cn/open-apis/bitable/v1/apps/"
            f"{settings.FEISHU_APP_TOKEN_SCRIPT}"
            f"/tables/{settings.FEISHU_TABLE_SCRIPT}/records/search"
        )
        update_url = (
            f"https://open.feishu.cn/open-apis/bitable/v1/apps/"
            f"{settings.FEISHU_APP_TOKEN_SCRIPT}"
            f"/tables/{settings.FEISHU_TABLE_SCRIPT}/records/batch_update"
        )

        try:
            resp = _req.post(search_url, headers=headers, json={
                "page_size": 200,
                "filter": {
                    "conjunction": "and",
                    "conditions": [
                        {"field_name": "所属小说ID",     "operator": "is", "value": [novel_id]},
                        {"field_name": "所属章节文件名", "operator": "is", "value": [chapter_name]},
                        {"field_name": "关联记忆实体",   "operator": "contains", "value": [entity_id]},
                    ]
                }
            })
            resp.raise_for_status()
            items = resp.json().get("data", {}).get("items", [])
            if not items:
                logger.debug(
                    f"[Stage1.5] 未找到 entity_id={entity_id} 在 {chapter_name} 的关联分镜行"
                )
                return

            # 2. 批量写回"首帧参考图"（文本字段存 URL，附件字段需特殊处理）
            update_records = [
                {
                    "record_id": row["record_id"],
                    "fields": {"首帧参考图": scene_frame_url}
                }
                for row in items
            ]
            for i in range(0, len(update_records), 100):
                batch = update_records[i:i + 100]
                upd_resp = _req.post(update_url, headers=headers, json={"records": batch})
                upd_resp.raise_for_status()
                body = upd_resp.json()
                if body.get("code") == 0:
                    logger.info(
                        f"[Stage1.5] 回填剧本拆解表.首帧参考图: "
                        f"{len(body.get('data',{}).get('records',[]))} 行 "
                        f"(entity={entity_id})"
                    )
                else:
                    logger.warning(f"[Stage1.5] 首帧回填异常: {body.get('msg')}")
        except Exception as e:
            logger.error(f"[Stage1.5] _writeback_frame_to_script 失败: {e}")

    async def _wait_for_gateway_switch(
        self,
        task_id: str,
        entity_name: str,
        active_gw: str,
        current_gateways: list,
    ) -> None:
        """配额拦截后挂起，轮询 StateKeeper 等待前端热切换生图模型。"""
        from core.api.state import StateKeeper
        state_keeper = StateKeeper()

        if task_id:
            await state_keeper.append_log(task_id, {
                "stage": "quota_error",
                "status": "error",
                "chapter": f"生图任务({entity_name})",
                "message": (
                    f"[{active_gw}] 生图配额耗尽！"
                    "请在左侧面板切换【生图模型】以继续补齐视觉资产。"
                ),
                "ts": time.time(),
            }, force_save=True)

        if not task_id:
            logger.error(f"[Stage1.5] {entity_name}: 无 task_id，无法热重载，放弃")
            return

        logger.warning(
            f"⏳ [Stage1.5] {entity_name} 生图挂起，等待前端切换网关 ({active_gw})..."
        )
        while True:
            await asyncio.sleep(5)
            master = await state_keeper.get_task(task_id)
            if not master:
                continue

            db_gw = master.get("image_gateway", active_gw)
            if db_gw != active_gw:
                logger.info(f"[Stage1.5] 热重载: {active_gw} → {db_gw}")
                current_gateways[0] = db_gw
                return

            if master.get("retry_signal"):
                await state_keeper.update_task(task_id, {"retry_signal": False}, force_save=True)
                logger.info(f"[Stage1.5] {entity_name}: 强制重试指令")
                return

            if master.get("finished"):
                logger.warning(f"[Stage1.5] {entity_name}: 主任务已终止，退出")
                return
