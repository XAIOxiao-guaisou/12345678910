"""
core/stages/stage4_video_gen.py
v2.8.0 Phase 2: I2V 视频生成派发 Stage

═══════════════════════════════════════════════════════════════════
  本 Stage 是 Phase 1 VideoDispatchStage 的完整升级版：
  - 从 context.visual_prompts 中读取 (prompt, image_url) 元组
  - image_url 来源于 context.scene_frame_urls（场景首帧图，非三视图）
  - 通过 video_service/factory.get_video_api() 调用已升级的 I2V 协议
  - 支持 T2V 降级：无 image_url 时自动传空字符串走文生视频端点
  - 含配额热重载、QPS 退避、最大轮询超时
═══════════════════════════════════════════════════════════════════
"""
from __future__ import annotations
import asyncio
import logging
import os
import time
from typing import Any

from .base_stage import BaseStage, PipelineContext

logger = logging.getLogger(__name__)


class VideoGenStage(BaseStage):
    """
    Stage 4 — I2V / T2V 视频生成派发（完整生产级实现）。

    调用 pipeline.py 中的 run_video_generation，将 context.visual_prompts
    中已装配好 image_url 的 (prompt, image_url) 元组批量提交视频 API。
    """
    STAGE_NAME = "Stage4_VideoGen"

    def __init__(self, pipeline_orchestrator):
        """
        Args:
            pipeline_orchestrator: PipelineOrchestrator 实例，
                                   借用其 run_video_generation 方法。
        """
        self.orchestrator = pipeline_orchestrator

    async def run(self, context: PipelineContext) -> PipelineContext:
        """
        沙盒模式跳过；生产模式下将 visual_prompts 提交给视频生成服务。

        在派发前对 visual_prompts 进行 image_url 完整性校验：
          - 有 image_url → I2V 模式（首帧图锁定角色/场景）
          - 无 image_url → T2V 降级（纯文生视频）
          - 均打印日志，供后续复盘优化
        """
        if context.sandbox_mode:
            logger.info("[Stage4] sandbox_mode=True，跳过视频派发")
            return context

        if not context.visual_prompts:
            logger.warning("[Stage4] 无视觉提示词，跳过视频派发")
            return context

        # ── 统计 I2V / T2V 比例 ────────────────────────────────────────
        i2v_count = sum(
            1 for p in context.visual_prompts
            if isinstance(p, tuple) and len(p) == 2 and p[1]
        )
        t2v_count = len(context.visual_prompts) - i2v_count
        logger.info(
            f"[Stage4] 生产模式 | gateway={context.gateway}\n"
            f"  总分镜: {len(context.visual_prompts)} 个\n"
            f"  I2V (含首帧图): {i2v_count} 个\n"
            f"  T2V (纯文生视频): {t2v_count} 个 [{'⚠️配额不足/首帧图缺失' if t2v_count > 0 else '✅'}]"
        )

        # ── 派发到视频生成服务（同步等待，直至轮询完成或配额异常被挂起）───────────────
        await self.orchestrator.run_video_generation(
            account=context.novel_id,
            prompts=context.visual_prompts,
            gateway=context.gateway,
            video_params=context.video_params,
            task_id=context.task_id,
        )

        logger.info(f"[Stage4] 视频生成任务已完成派发并退出等待 ✅")
        return context

    async def run_sync(self, context: PipelineContext) -> list[str]:
        """
        同步等待模式（用于测试或需要获取下载路径的场景）。
        直接调用 orchestrator.run_video_generation 并等待结果。
        下载完成的视频路径写入 context.downloaded_video_paths。
        """
        if context.sandbox_mode or not context.visual_prompts:
            return []

        logger.info(f"[Stage4][SYNC] 同步模式，等待 {len(context.visual_prompts)} 个任务完成...")
        results = await self.orchestrator.run_video_generation(
            account=context.novel_id,
            prompts=context.visual_prompts,
            gateway=context.gateway,
            video_params=context.video_params,
            task_id=context.task_id,
        )
        if isinstance(results, list):
            paths = [r for r in results if isinstance(r, str) and os.path.exists(r)]
            context.downloaded_video_paths.extend(paths)
            logger.info(f"[Stage4][SYNC] 完成，本地视频: {len(paths)} 个")
            return paths
        return []


# ──────────────────────────────────────────────────────────────────────────
# 独立工具函数：不依赖 Stage 类，可在 pipeline.py 中直接 import 使用
# ──────────────────────────────────────────────────────────────────────────

def build_i2v_prompts_from_context(
    context: PipelineContext,
    scenes: list,
) -> list:
    """
    从 context.scene_frame_urls 和 scenes 列表中装配 (prompt, image_url) 元组。

    Args:
        context: 含 scene_frame_urls 的流水线上下文
        scenes:  Stage2 生成的分镜列表，每项含 visual_prompt 和 entity_ids

    Returns:
        list of (str, str) | str：
          - (visual_prompt, image_url) -> I2V 模式
          - visual_prompt             -> T2V 降级模式
    """
    prompts = []
    i2v_hits = 0
    t2v_fallbacks = 0

    for sc in scenes:
        vp = sc.get("visual_prompt", "")
        if not (vp and isinstance(vp, str) and len(vp) > 10):
            continue

        image_url = ""
        for eid in (sc.get("entity_ids") or []):
            url = context.scene_frame_urls.get(str(eid), "")
            if url:
                image_url = url
                i2v_hits += 1
                break

        if not image_url:
            t2v_fallbacks += 1

        prompts.append((vp, image_url) if image_url else vp)

    logger.info(
        f"[build_i2v_prompts] 装配完成: "
        f"I2V={i2v_hits}, T2V(降级)={t2v_fallbacks}, 总计={len(prompts)}"
    )
    return prompts
