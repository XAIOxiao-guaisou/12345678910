"""
core/stages/__init__.py
v2.8.0: Pipeline Stage 拆分初始化模块。

Pipeline 拆分策略：
  - PipelineOrchestrator (pipeline.py) 只负责按顺序调用各 Stage
  - 每个 Stage 是独立 Class，实现 BaseStage.run(context) 协议
  - 新 Stage 只需新建文件并注册到 PipelineOrchestrator，不修改已有 Stage

Stage 顺序：
  Stage1  → MemoryExtractionStage   (记忆建档/演化)
  Stage1.5→ VisualAssetStage        (视觉资产管线 · Phase 2 完整版)
  Stage1.5→ VisualAnchorStage       (视觉锚定骨架 · Phase 1 兼容版)
  Stage2  → StoryboardStage         (分镜生成)
  Stage4  → VideoGenStage           (I2V/T2V 视频生成派发 · Phase 2 完整版)
  Stage3  → VideoDispatchStage      (视频调度骨架 · Phase 1 兼容版)
  Stage5  → PostMuxStage            (多轨混流，v3.0.0 实装)
"""

from .base_stage import BaseStage, PipelineContext
from .stage1_memory import MemoryExtractionStage
from .stage1_5_visual_anchor import VisualAnchorStage
from .stage1_5_visual_asset import VisualAssetStage
from .stage2_storyboard import StoryboardStage
from .stage3_video_dispatch import VideoDispatchStage
from .stage4_video_gen import VideoGenStage, build_i2v_prompts_from_context
from .stage5_post_mux import PostMuxStage

__all__ = [
    "BaseStage",
    "PipelineContext",
    "MemoryExtractionStage",
    "VisualAnchorStage",
    "VisualAssetStage",          # Phase 2: 完整视觉资产管线（推荐使用）
    "StoryboardStage",
    "VideoDispatchStage",
    "VideoGenStage",             # Phase 2: I2V 完整视频生成派发（推荐使用）
    "build_i2v_prompts_from_context",
    "PostMuxStage",
]

