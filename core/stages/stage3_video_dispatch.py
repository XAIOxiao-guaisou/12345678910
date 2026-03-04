"""
core/stages/stage3_video_dispatch.py
v2.8.0: Stage 3 — 视频生成调度 (从 pipeline.py 提取)

职责：
  - 接收 context.visual_prompts（已含 image_url 的元组，或纯 str T2V 模式）
  - 通过 PipelineOrchestrator.run_video_generation 异步派发视频任务
  - 将下载到本地的视频路径写入 context.downloaded_video_paths
  - 沙盒模式下跳过派发

设计约束：
  - 此 Stage 以 asyncio.create_task 触发，不阻塞上游流水线返回
  - 下载得到的本地路径储存于 context，供 Stage5 PostMuxStage 消费
"""
import asyncio
import logging
from .base_stage import BaseStage, PipelineContext

logger = logging.getLogger(__name__)


class VideoDispatchStage(BaseStage):
    """Stage 3: 视频生成异步调度。"""
    STAGE_NAME = "Stage3_VideoDispatch"

    def __init__(self, pipeline_orchestrator):
        # 持有 PipelineOrchestrator 引用，借用其 run_video_generation 逻辑
        self.orchestrator = pipeline_orchestrator

    async def run(self, context: PipelineContext) -> PipelineContext:
        """
        在生产模式下，将 context.visual_prompts 异步派发给视频生成服务。
        沙盒模式直接跳过。
        """
        if context.sandbox_mode:
            logger.info("[Stage3] sandbox_mode=True，跳过视频派发")
            return context

        if not context.visual_prompts:
            logger.warning("[Stage3] 无视觉提示词，跳过视频派发")
            return context

        logger.info(
            f"[Stage3] 生产模式开启，派发 {len(context.visual_prompts)} 个分镜 "
            f"→ gateway={context.gateway}"
        )
        # 使用 create_task 异步派发，不阻塞当前流水线
        asyncio.create_task(
            self.orchestrator.run_video_generation(
                account=context.novel_id,
                prompts=context.visual_prompts,
                gateway=context.gateway,
                video_params=context.video_params,
                task_id=context.task_id,
            )
        )
        return context
