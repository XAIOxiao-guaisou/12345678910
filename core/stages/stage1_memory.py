"""
core/stages/stage1_memory.py
v2.8.0: Stage 1 — 记忆建档与演化 (从 pipeline.py 提取)

职责：
  - 首次建档：调用 DeepSeekService.generate_memory_context → 写入飞书记忆中枢
  - 增量演化：调用 MemoryEngine.evolve_memory → 增量更新现有词条
  - 输出：context.active_memories（过滤废弃词条后的有效词条列表）
"""
import logging
from .base_stage import BaseStage, PipelineContext

logger = logging.getLogger(__name__)


class MemoryExtractionStage(BaseStage):
    """
    Stage 1: 记忆中枢增量建档/演化。

    对 novel_id 粒度加锁（通过 TaskManager）保证同一小说的章节串行处理。
    首次建档走全量提取，再次进入走 MemoryEngine 增量演化。
    """
    STAGE_NAME = "Stage1_Memory"

    def __init__(self, bitable, task_manager):
        self.bitable = bitable
        self.task_manager = task_manager

    async def run(self, context: PipelineContext) -> PipelineContext:
        """
        执行单章节记忆演化，写入 context.active_memories。
        context.files 中的每个 file_info 按顺序处理（在 PipelineOrchestrator 调度层迭代）。
        """
        from core.services.llm_service.deepseek_service import DeepSeekService
        from core.services.llm_service.memory_engine import MemoryEngine

        novel_id = context.novel_id

        # 全量记忆召回（过滤废弃词条）
        try:
            all_memories_dict = self.bitable.get_memories_by_novel(novel_id)
            context.active_memories = [
                v["fields"] for v in all_memories_dict.values()
                if v["fields"].get("status") != "废弃"
            ]
            logger.info(
                f"[Stage1] novel_id={novel_id} 记忆召回完成，"
                f"有效词条 {len(context.active_memories)} 条"
            )
        except Exception as e:
            self._log_error(context, f"[Stage1] 记忆召回失败: {e}")

        return context

    async def run_chapter(
        self, context: PipelineContext,
        chapter_text: str,
        chapter_name: str,
        chapter_index: int,
    ) -> dict:
        """
        单章节记忆演化入口（由 PipelineOrchestrator 在 files 循环中调用）。
        返回 MemoryEngine.evolve_memory 的结果 dict。
        """
        from core.services.llm_service.deepseek_service import DeepSeekService
        from core.services.llm_service.memory_engine import MemoryEngine

        novel_id = context.novel_id

        if context.memory_lock:
            logger.info(f"[Stage1] MemoryLock 已启用，跳过 {chapter_name} 的记忆演化")
            return {"status": "success", "message": "Memory lock enabled.", "stage1_entities": []}

        # 检查现有记忆：首次全量建档 vs 增量演化
        try:
            existing = self.bitable.get_memories_by_novel(novel_id)
            if not existing:
                logger.info(f"[Stage1] 首次建档 novel_id={novel_id} → 全量提取记忆...")
                memory_data = DeepSeekService.generate_memory_context(chapter_text)
                self.bitable.insert_memory_records(memory_data, novel_id=novel_id)
                return {"status": "success", "stage1_entities": []}
            else:
                return await MemoryEngine.evolve_memory(
                    novel_id=novel_id,
                    chapter_text=chapter_text,
                    chapter_name=chapter_name,
                    bitable=self.bitable,
                    chapter_index=chapter_index,
                )
        except Exception as e:
            self._log_error(context, f"[Stage1] {chapter_name} 演化异常: {e}")
            return {"status": "error", "message": str(e), "stage1_entities": []}
