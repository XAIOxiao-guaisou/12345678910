"""
core/stages/stage5_post_mux.py
v3.0.0: Stage 5 — 后期制作混流 (正式激活)

职责：
  1. 读取 context.downloaded_video_paths：每幕原始静音视频
  2. 读取 context.all_scenes：每幕的「旁白与台词」、「音效提示词」字段
  3. 调用 TTSService.synthesize() 生成人声干声 .mp3
  4. 调用 SFXService.generate_sfx() 生成环境音效 .wav（非致命，可跳过）
  5. 调用 ffmpeg_service.mux() 多轨合并为最终 .mp4
  6. 写入 context.muxed_output_paths

并发策略：
  - 每集（视频）并发 SFX 生成（非致命）+ TTS 生成（致命于 mux 前校验）
  - 使用 asyncio.Semaphore(2) 控制同时并发 mux 数量，防止 FFmpeg 竞争

时空对齐策略（从 config 或构造参数传入）：
  freeze_frame  - TTS > 视频时，最后一帧定格延长（推荐）
  loop_video    - 循环视频画面填满 TTS
  trim_audio    - 截断音频适配视频（会丢台词，慎用）
"""
from __future__ import annotations
import asyncio
import logging
import os
import tempfile
from pathlib import Path
from typing import Optional

from .base_stage import BaseStage, PipelineContext

logger = logging.getLogger(__name__)


class PostMuxStage(BaseStage):
    """
    Stage 5: 音视频后期混流 — v3.0.0 正式实装版。
    """
    STAGE_NAME = "Stage5_PostMux"

    def __init__(
        self,
        sync_strategy: str = "freeze_frame",
        output_dir: str = "",
        ffmpeg_binary: str = "ffmpeg",
        enable_sfx: bool = True,
    ):
        """
        Args:
            sync_strategy : 时空对齐策略（freeze_frame/loop_video/trim_audio）
            output_dir    : 成片输出目录（留空则读 config.OUTPUT_DIR）
            ffmpeg_binary : FFmpeg 可执行文件路径（留空则读 config.FFMPEG_BINARY）
            enable_sfx    : 是否尝试生成 SFX（False=强制跳过，加速测试用）
        """
        from core.config import settings
        self.sync_strategy  = sync_strategy
        self.output_dir     = output_dir or getattr(settings, "OUTPUT_DIR", "Download/output")
        self.ffmpeg_binary  = ffmpeg_binary or getattr(settings, "FFMPEG_BINARY", "ffmpeg")
        self.enable_sfx     = enable_sfx

    async def run(self, context: PipelineContext) -> PipelineContext:
        """
        对 context.downloaded_video_paths 中的每个视频执行后期制作。
        FFmpeg 环境预检失败时，记录 context.errors 并直接返回（不阻断整个流水线）。
        """
        if not context.downloaded_video_paths:
            logger.info("[Stage5] 无下载视频，跳过后期混流")
            return context

        # ── FFmpeg 环境预检 ─────────────────────────────────────────────
        from core.services.post_production.ffmpeg_service import check_ffmpeg_env
        try:
            check_ffmpeg_env(self.ffmpeg_binary)
        except EnvironmentError as e:
            self._log_error(context, f"[Stage5] FFmpeg 环境预检失败，跳过后期制作: {e}")
            return context

        os.makedirs(self.output_dir, exist_ok=True)

        # ── 初始化服务 ──────────────────────────────────────────────────
        from core.services.post_production.tts_service import TTSService
        from core.services.post_production.sfx_service import SFXService
        from core.services.post_production.audio_orchestrator import AudioOrchestrator
        import tempfile
        tts_svc = TTSService()
        sfx_svc = SFXService() if self.enable_sfx else None
        tmp_dir = tempfile.mkdtemp(prefix="postprod_")
        orchestrator = AudioOrchestrator(
            tts_service=tts_svc,
            sfx_service=sfx_svc,
            output_dir=tmp_dir,
        )

        sem = asyncio.Semaphore(2)   # 最多同时 2 路 mux

        tasks = [
            self._process_episode(
                context=context,
                ep_index=i,
                video_path=vp,
                orchestrator=orchestrator,
                sem=sem,
            )
            for i, vp in enumerate(context.downloaded_video_paths)
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        errs = [r for r in results if isinstance(r, Exception)]
        if errs:
            for err in errs:
                self._log_error(context, f"[Stage5] 集数处理异常: {err}")

        logger.info(
            f"[Stage5] ✅ 后期制作完成: "
            f"{len(context.muxed_output_paths)}/{len(context.downloaded_video_paths)} 集成功"
        )
        return context

    async def _process_episode(
        self,
        context: PipelineContext,
        ep_index: int,
        video_path: str,
        orchestrator,
        sem: asyncio.Semaphore,
    ) -> None:
        """单集后期制作：AudioOrchestrator 并发生成 TTS+SFX → FFmpeg mux。"""
        from core.services.post_production.ffmpeg_service import mux

        scene = context.all_scenes[ep_index] if ep_index < len(context.all_scenes) else {}
        ep_name = f"{context.novel_id}_ep{ep_index + 1:03d}"

        # ── 并发生成 TTS + SFX ──────────────────────────────────────────
        tts_path, sfx_path = await orchestrator.produce(
            scene=scene, ep_index=ep_index, ep_name=ep_name
        )

        # ── FFmpeg mux ──────────────────────────────────────────────────
        output_path = os.path.join(self.output_dir, f"{ep_name}_final.mp4")
        async with sem:
            try:
                result = await mux(
                    video_path=video_path,
                    tts_path=tts_path,
                    sfx_path=sfx_path,
                    bgm_path=None,
                    output_path=output_path,
                    sync_strategy=self.sync_strategy,
                    ffmpeg_binary=self.ffmpeg_binary,
                )
                context.muxed_output_paths.append(result)
                logger.info(f"[Stage5] ✅ 集{ep_index+1} 成片: {result}")
            except Exception as e:
                logger.error(f"[Stage5] 集{ep_index+1} mux 失败: {e}")
                self._log_error(context, f"[Stage5] ep{ep_index+1} mux 失败: {e}")
