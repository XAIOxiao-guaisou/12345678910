"""
memory_engine.py — v2.6.0 记忆进化引擎 (MemoryEngine)

核心职责:
  1. local_diff: 本地 O(1) 增量差分算法
     (existing_dict vs new_entries) → {update, insert, archive}
  2. evolve_memory: 全流程增量进化
     - 从飞书预载该 novel_id 全量记忆
     - 调用 DeepSeek delta_extract
     - local_diff 分类
     - 批量写回飞书 (batch_update + batch_create)
     - 事务性 Stage1 标记
  3. build_existing_summary: 生成供 DeepSeek 参考的简化摘要字符串

数据流:
  get_memories_by_novel(novel_id) → existing_dict
       ↓
  DeepSeekService.delta_extract_memory(chapter_text, existing_summary)
       ↓
  local_diff(existing_dict, new_entries) → {update, insert, archive}
       ↓
  batch_update_memories(update_list) + batch_create_memories(insert_list)
       ↓  (仅全部写回成功后)
  mark_chapter_stage1_complete(novel_id, chapter_name)
"""

import hashlib
import logging
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.services.db_service.feishu_bitable import FeishuBitableManager
    from core.services.llm_service.deepseek_service import DeepSeekService

logger = logging.getLogger(__name__)


class MemoryEngine:
    """
    记忆进化引擎：负责处理设定冲突检测与增量更新逻辑。
    全程采用"全量预载 + 本地 Diff + 批量写"架构，
    将网络开销从 O(N) 压降至 O(1) 级别。
    """

    # ------------------------------------------------------------------
    # 核心 Diff 算法
    # ------------------------------------------------------------------
    @staticmethod
    def local_diff(existing: dict, new_entries: list) -> dict:
        """
        在内存中执行增量差分，不发出任何网络请求。

        Args:
            existing: get_memories_by_novel() 返回的 Dict
                      key = entity_id
                      value = {"record_id": str, "fields": {...}}
            new_entries: DeepSeek delta_extract 返回的列表，每条格式:
                {
                  "action":      "INSERT" | "UPDATE" | "ARCHIVE",
                  "entity_id":   str (可选，INSERT 时由引擎生成),
                  "novel_id":    str,
                  "category":    str,
                  "name":        str,
                  "lore":        str,
                  "visual_aura": str,
                  "reason":      str (ARCHIVE 时填写原因),
                }

        Returns:
            {
              "update":  [(record_id, feishu_field_dict), ...],
              "insert":  [feishu_field_dict, ...],
              "archive": [(record_id, {"status": "废弃"}), ...],
            }
        """
        result = {"update": [], "insert": [], "archive": []}

        for entry in new_entries:
            action = entry.get("action", "INSERT").upper()
            entity_id = entry.get("entity_id", "")
            novel_id = entry.get("novel_id", "")
            name = entry.get("name", "")

            # entity_id 兜底生成（与 feishu_bitable 规则一致）
            if not entity_id and novel_id and name:
                entity_id = f"e_{hashlib.sha1(f'{novel_id}:{name}'.encode()).hexdigest()[:8]}"

            if action == "ARCHIVE":
                rec = existing.get(entity_id)
                if rec:
                    result["archive"].append((
                        rec["record_id"],
                        {"status": "废弃"}
                    ))
                    logger.debug(f"[local_diff] ARCHIVE: {name} ({entity_id})")
                else:
                    logger.warning(f"[local_diff] ARCHIVE 目标未找到: {entity_id} ({name})，跳过")
                continue

            # 构造飞书字段（Update / Insert 共用）
            feishu_fields = {}
            if entry.get("category"):   feishu_fields["类别"] = entry["category"]
            if name:                     feishu_fields["词条名"] = name
            if entry.get("lore"):        feishu_fields["深层设定逻辑"] = entry["lore"]
            if entry.get("visual_aura"): feishu_fields["视觉氛围与美学隐喻"] = entry["visual_aura"]
            if novel_id:                 feishu_fields["所属小说ID"] = novel_id
            if entity_id:                feishu_fields["entity_id"] = entity_id
            feishu_fields["status"] = "进化" if action == "UPDATE" else "活跃"

            if action == "UPDATE":
                rec = existing.get(entity_id)
                if rec:
                    # 检查人类干预锁：如果飞书中状态被修改为“锁定”，则放弃 AI 的 UPDATE 提议
                    if rec["fields"].get("status") == "锁定":
                        logger.info(f"[local_diff] 🔒 UPDATE 跳过: 词条 '{name}' 已被人工锁定")
                        continue
                    
                    # version 自增
                    old_version = rec["fields"].get("version", 1)
                    feishu_fields["version"] = old_version + 1
                    result["update"].append((rec["record_id"], feishu_fields))
                    logger.debug(f"[local_diff] UPDATE: {name} (v{old_version} → v{old_version+1})")
                else:
                    # entity_id 在现有库未找到，降级为 INSERT
                    logger.warning(f"[local_diff] UPDATE 目标 {entity_id} 不存在，降级为 INSERT")
                    feishu_fields["version"] = 1
                    result["insert"].append(feishu_fields)
            else:  # INSERT
                # 防重名：若同名词条已存在，改为 UPDATE
                name_match = next(
                    (v for v in existing.values() if v["fields"].get("name") == name),
                    None
                )
                if name_match:
                    logger.warning(
                        f"[local_diff] INSERT 发现重名词条 '{name}'，自动改为 UPDATE，防止重复词条"
                    )
                    old_version = name_match["fields"].get("version", 1)
                    feishu_fields["version"] = old_version + 1
                    feishu_fields["status"] = "进化"
                    result["update"].append((name_match["record_id"], feishu_fields))
                else:
                    feishu_fields["version"] = 1
                    result["insert"].append(feishu_fields)

        logger.info(
            f"[local_diff] 差分结果: UPDATE={len(result['update'])} "
            f"INSERT={len(result['insert'])} ARCHIVE={len(result['archive'])}"
        )
        return result

    # ------------------------------------------------------------------
    # 摘要生成 (供 DeepSeek delta_extract 参考，节省 Token)
    # ------------------------------------------------------------------
    @staticmethod
    def build_existing_summary(existing: dict, max_entries: int = 60) -> str:
        """
        将现有记忆库精简为 DeepSeek 可读的文本摘要。
        自动过滤「废弃」词条（不占用 Token）。
        max_entries 限制传入 DeepSeek 的最大词条数，防止超出 context 窗口。
        """
        active = [
            v["fields"] for v in existing.values()
            if v["fields"].get("status") != "废弃"
        ]
        if not active:
            return "（当前小说记忆库为空，请进行完整初始建档）"

        # 截断保护
        if len(active) > max_entries:
            logger.warning(f"[MemoryEngine] 记忆词条 {len(active)} 条，截断至 {max_entries} 条供 Delta 参考")
            active = active[:max_entries]

        lines = ["【现有记忆库摘要（仅供增量对比，勿重复创建）】"]
        for m in active:
            status_tag = f"[{m.get('status', '活跃')}]"
            lines.append(
                f"- {status_tag} [{m.get('category', '')}] "
                f"{m.get('name', '')}（entity_id: {m.get('entity_id', 'N/A')}）: "
                f"{m.get('lore', '')[:80]}"
            )
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # 全流程: 章节记忆演化（事务性写回）
    # ------------------------------------------------------------------
    @staticmethod
    async def evolve_memory(
        novel_id: str,
        chapter_text: str,
        chapter_name: str,
        bitable: "FeishuBitableManager",
        chapter_index: int = 0,
    ) -> dict:
        """
        单章节记忆增量演化全流程（事务性）。

        流程:
          1. 从飞书全量预载该 novel_id 记忆（一次 GET，O(1)）
          2. 生成现有记忆摘要
          3. DeepSeek delta_extract（判断哪些词条需要 UPDATE/INSERT/ARCHIVE）
          4. local_diff 在内存中分类
          5. batch_update + batch_create 写回飞书
          6. 仅在写回确认后标记 Stage1 完成（事务一致性）

        Returns:
            {
              "status": "success" | "error",
              "updated": int, "inserted": int, "archived": int,
              "message": str
            }
        """
        # 延迟导入，避免循环依赖
        from core.services.llm_service.deepseek_service import DeepSeekService

        logger.info(f"🧠 [MemoryEngine] 开始演化: novel={novel_id}, chapter={chapter_name}")

        # Step 1: 全量预载（一次翻页式 GET）
        existing = bitable.get_memories_by_novel(novel_id)
        logger.info(f"📚 [MemoryEngine] 预载完毕: {len(existing)} 条现有词条")

        # Step 2: 生成摘要（自动过滤废弃词条）
        existing_summary = MemoryEngine.build_existing_summary(existing)

        # Step 3: DeepSeek Delta 提取
        try:
            new_entries = DeepSeekService.delta_extract_memory(
                chapter_text=chapter_text,
                existing_summary=existing_summary,
                novel_id=novel_id,
            )
            if not isinstance(new_entries, list):
                raise ValueError(f"delta_extract_memory 返回非列表类型: {type(new_entries)}")
        except Exception as e:
            logger.error(f"❌ [MemoryEngine] DeepSeek delta_extract 失败: {e}")
            return {"status": "error", "message": str(e), "updated": 0, "inserted": 0, "archived": 0}

        logger.info(f"🔍 [MemoryEngine] DeepSeek 输出 {len(new_entries)} 条变更指令")

        # Step 4: 本地 Diff（零网络开销）
        diff = MemoryEngine.local_diff(existing, new_entries)

        # Step 5: 事务性写回飞书
        update_ok = 0
        insert_ids = []
        archive_ok = 0
        write_error = False

        if diff["update"]:
            # 补充 last_update_chapter
            for i, (rid, fields) in enumerate(diff["update"]):
                fields["last_update_chapter"] = chapter_name
                diff["update"][i] = (rid, fields)
            update_ok = bitable.batch_update_memories(diff["update"])
            if update_ok == 0 and diff["update"]:
                logger.error("[MemoryEngine] batch_update_memories 全部失败")
                write_error = True

        if diff["insert"] and not write_error:
            # 补充 last_update_chapter
            for fields in diff["insert"]:
                fields["last_update_chapter"] = chapter_name
            insert_ids = bitable.batch_create_memories(diff["insert"])
            if not insert_ids and diff["insert"]:
                logger.error("[MemoryEngine] batch_create_memories 全部失败")
                write_error = True

        if diff["archive"] and not write_error:
            archive_ok = bitable.batch_update_memories(diff["archive"])

        if write_error:
            logger.error(
                f"❌ [MemoryEngine] 写回部分失败，章节 {chapter_name} Stage1 标记跳过，"
                f"重启任务时将重新处理该章节（断点续传安全）"
            )
            return {
                "status": "partial_error",
                "message": "部分写回失败，Stage1 未标记，可安全重试",
                "updated": update_ok,
                "inserted": len(insert_ids),
                "archived": archive_ok,
            }

        # Step 6: 事务提交 — 仅写回全部成功后才标记 Stage1 完成
        bitable.mark_chapter_stage1_complete(novel_id, chapter_name)

        logger.info(
            f"✅ [MemoryEngine] chapter={chapter_name} 演化完成: "
            f"UPDATE={update_ok} INSERT={len(insert_ids)} ARCHIVE={archive_ok}"
        )
        # 防 DeepSeek QPS 超限
        if chapter_index > 0:
            time.sleep(1)

        return {
            "status": "success",
            "updated": update_ok,
            "inserted": len(insert_ids),
            "archived": archive_ok,
            "message": f"chapter={chapter_name} 演化完成",
        }
