"""
core/stages/base_stage.py
v2.8.0: Stage 协议基类 + PipelineContext 数据容器。
所有具体 Stage 必须继承 BaseStage 并实现 run()。
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any


@dataclass
class PipelineContext:
    """
    流水线各 Stage 之间共享的数据容器（上下文令牌 Token）。
    Stage 读取上游数据、写入下游所需的输出，通过 context 流转。
    严禁 Stage 之间直接互相引用，只能通过 context 通信。
    """
    # ── 输入参数（由 PipelineOrchestrator 初始化）──────────────────────────
    novel_id: str = ""
    task_id: str = ""
    gateway: str = "wan_2_6"
    image_gateway: str = "aliyun"
    sandbox_mode: bool = True
    memory_lock: bool = False
    chunk_size: int = 1000
    video_params: dict = field(default_factory=dict)
    files: list = field(default_factory=list)        # [{name, content}, ...]

    # ── Stage1 输出 ────────────────────────────────────────────────────────
    active_memories: list = field(default_factory=list)   # 有效记忆词条列表

    # ── Stage1.5 输出 ──────────────────────────────────────────────────────
    # 资产分离：两类 URL 严格区分
    # turnaround_urls: {entity_id: url}  设定参考图（三视图），不进 I2V
    # scene_frame_urls: {entity_id: url} 分镜首帧图，才是 I2V 的 image_url
    turnaround_urls: dict = field(default_factory=dict)
    scene_frame_urls: dict = field(default_factory=dict)

    # ── Stage2 输出 ────────────────────────────────────────────────────────
    all_scenes: list = field(default_factory=list)
    # (prompt, image_url) 元组列表，image_url 来自 scene_frame_urls
    visual_prompts: list = field(default_factory=list)

    # ── Stage3 输出 ────────────────────────────────────────────────────────
    downloaded_video_paths: list = field(default_factory=list)

    # ── Stage5 输出（v3.0.0）─────────────────────────────────────────────
    muxed_output_paths: list = field(default_factory=list)

    # ── 通用 ───────────────────────────────────────────────────────────────
    errors: list = field(default_factory=list)
    metadata: dict = field(default_factory=dict)   # 任意附加元数据


class BaseStage:
    """
    所有流水线 Stage 的抽象基类。

    约定：
      - run() 接收并返回同一个 PipelineContext 对象（就地修改）
      - Stage 内部异常必须 catch 后写入 context.errors，不允许向上冒泡
        （除非是 Fail-Fast 的严重错误，应在文档字符串中注明）
    """
    STAGE_NAME: str = "BaseStage"

    async def run(self, context: PipelineContext) -> PipelineContext:
        raise NotImplementedError(
            f"{self.__class__.__name__} 未实现 run() 方法"
        )

    def _log_error(self, context: PipelineContext, msg: str) -> None:
        """将错误写入 context.errors 并同步记录日志。"""
        import logging
        logging.getLogger(self.__class__.__module__).error(msg)
        context.errors.append({"stage": self.STAGE_NAME, "error": msg})
