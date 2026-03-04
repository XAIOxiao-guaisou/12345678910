"""
memory_engine.py — v2.6.5 记忆进化引擎 (MemoryEngine)

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
    def local_diff(existing: dict, new_entries: list,
                   chapter_name: str = "", chapter_index: int = 0) -> dict:
        """
        在内存中执行增量差分，不发出任何网络请求。

        v2.7: 新增 chapter_name 参数，对每个 UPDATE 词条追加
              「历史变更记录」[ch_X] 摘要条目和
              「last_update_chapter」追加模式（分号隔开历史）。

        Args:
            existing: get_memories_by_novel() 返回的 Dict
            new_entries: DeepSeek delta_extract 返回的列表
            chapter_name: 当前处理章节名（用于历史字段）
        """
        result = {"update": [], "insert": [], "archive": []}
        _present_entities: list = []  # v2.7.0: 收集本章实射实体

        for entry in new_entries:
            action = entry.get("action", "INSERT").upper()
            entity_id = entry.get("entity_id", "")
            novel_id = entry.get("novel_id", "")
            name = entry.get("name", "")

            # entity_id 兼底生成（与 feishu_bitable 规则一致）
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
                    # 检查人类干预锁：如果飞书中状态被修改为"锁定"，则放弃 AI 的 UPDATE 提议
                    if rec["fields"].get("status") == "锁定":
                        logger.info(f"[local_diff] 🔒 UPDATE 跳过: 词条 '{name}' 已被人工锁定")
                        continue

                    is_present = entry.get("present_in_current", True)
                    
                    if is_present:
                        # 只有正式出场，才自增 Version
                        old_version = rec["fields"].get("version", 1)
                        feishu_fields["version"] = old_version + 1
                        
                        feishu_fields["last_seen_chapter"] = chapter_name
                        feishu_fields["last_seen_chapter_idx"] = chapter_index
                        
                        # 原子化追加章节轨迹
                        old_trace = rec["fields"].get("章节轨迹", "")
                        if isinstance(old_trace, list):
                            old_trace = "".join(seg.get("text", "") if isinstance(seg, dict) else str(seg) for seg in old_trace)
                        trace_list = [t.strip() for t in str(old_trace).split(",") if t.strip()] if old_trace else []
                        if chapter_name and chapter_name not in trace_list:
                            trace_set = set(trace_list)
                            trace_set.add(chapter_name)
                            # 为了保持唯一性与顺序性，虽然 set 破坏了原始顺序，但因为我们是递增追加的，通常按插入即可。为了鲁棒，可以直接追在末尾。
                            # 这里简单的去重+时序保持
                            new_trace_list = []
                            for t in trace_list:
                                if t not in new_trace_list: new_trace_list.append(t)
                            if chapter_name not in new_trace_list:
                                new_trace_list.append(chapter_name)
                            feishu_fields["章节轨迹"] = ",".join(new_trace_list)

                        # v2.7.0: 登记本章实射实体（供 Stage 1.5）
                        _present_entities.append({
                            "entity_id": entity_id,
                            "name": name,
                            "category": entry.get("category", rec["fields"].get("category", "")),
                            "lore": entry.get("lore", ""),
                            "visual_aura": entry.get("visual_aura", rec["fields"].get("visual_aura", "")),
                            "lore_changed": bool(entry.get("lore", "").strip()),  # 是否有设定变化
                            "old_lore": rec["fields"].get("lore", ""),
                        })
                        
                    else:
                        # 仅探测心跳，不用加 version
                        old_version = rec["fields"].get("version", 1)  # 保持原样以供后续 log 用
                        feishu_fields["version"] = old_version
                        feishu_fields["status"] = "活跃"

                    if entry.get("last_known_state"):
                        feishu_fields["last_known_state"] = entry["last_known_state"]
                    if entry.get("implicit_carry"):
                        feishu_fields["implicit_carry"] = entry["implicit_carry"]

                    # last_update_chapter: 追加模式（保留完整历史）
                    if chapter_name:
                        old_chap = rec["fields"].get("last_update_chapter", "")
                        if isinstance(old_chap, list):
                            old_chap = "".join(seg.get("text", "") if isinstance(seg, dict) else str(seg) for seg in old_chap)
                        existing_chapters = [c.strip() for c in old_chap.split(";") if c.strip()] if old_chap else []
                        if chapter_name not in existing_chapters:
                            existing_chapters.append(chapter_name)
                        feishu_fields["last_update_chapter"] = ";".join(existing_chapters)

                    # 历史变更记录: 追加本次变化摘要
                    if chapter_name:
                        old_log = rec["fields"].get("历史变更记录", "")
                        if isinstance(old_log, list):
                            old_log = "".join(seg.get("text", "") if isinstance(seg, dict) else str(seg) for seg in old_log)
                        lore_snippet = (entry.get("lore", "") or "")[:60].replace("\n", " ")
                        new_entry = f"[{chapter_name}] v{old_version+1}: {lore_snippet}"
                        feishu_fields["历史变更记录"] = (old_log + "; " + new_entry).strip("; ") if old_log else new_entry

                    result["update"].append((rec["record_id"], feishu_fields))
                    logger.debug(f"[local_diff] UPDATE: {name} (v{old_version} → v{old_version+1})")
                else:
                    # entity_id 在现有库未找到，降级为 INSERT
                    logger.warning(f"[local_diff] UPDATE 目标 {entity_id} 不存在，降级为 INSERT")
                    feishu_fields["version"] = 1
                    feishu_fields["last_seen_chapter"] = chapter_name
                    feishu_fields["last_seen_chapter_idx"] = chapter_index
                    if chapter_name:
                        feishu_fields["章节轨迹"] = chapter_name
                    if entry.get("last_known_state"):
                        feishu_fields["last_known_state"] = entry["last_known_state"]
                    if chapter_name:
                        feishu_fields["last_update_chapter"] = chapter_name
                        feishu_fields["历史变更记录"] = f"[{chapter_name}] v1: 新建"
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
                    # v2.6.5: 补全 last_seen / 章节轨迹（重名路径此前漏写，导致 trace 断链）
                    feishu_fields["last_seen_chapter"] = chapter_name
                    feishu_fields["last_seen_chapter_idx"] = chapter_index
                    if chapter_name:
                        # 章节轨迹: 原子追加，严格去重
                        old_trace = name_match["fields"].get("章节轨迹", "")
                        if isinstance(old_trace, list):
                            old_trace = "".join(seg.get("text", "") if isinstance(seg, dict) else str(seg) for seg in old_trace)
                        trace_list = [t.strip() for t in str(old_trace).split(",") if t.strip()] if old_trace else []
                        if chapter_name not in trace_list:
                            trace_list.append(chapter_name)
                        feishu_fields["章节轨迹"] = ",".join(trace_list)
                        # last_update_chapter
                        old_chap = name_match["fields"].get("last_update_chapter", "")
                        if isinstance(old_chap, list):
                            old_chap = "".join(seg.get("text", "") if isinstance(seg, dict) else str(seg) for seg in old_chap)
                        existing_chapters = [c.strip() for c in old_chap.split(";") if c.strip()] if old_chap else []
                        if chapter_name not in existing_chapters:
                            existing_chapters.append(chapter_name)
                        feishu_fields["last_update_chapter"] = ";".join(existing_chapters)
                        # 历史变更记录
                        old_log = name_match["fields"].get("历史变更记录", "")
                        if isinstance(old_log, list):
                            old_log = "".join(seg.get("text", "") if isinstance(seg, dict) else str(seg) for seg in old_log)
                        new_entry_log = f"[{chapter_name}] v{old_version+1}: 重名INSERT转UPDATE"
                        feishu_fields["历史变更记录"] = (old_log + "; " + new_entry_log).strip("; ") if old_log else new_entry_log
                    result["update"].append((name_match["record_id"], feishu_fields))
                else:
                    feishu_fields["version"] = 1
                    feishu_fields["last_seen_chapter"] = chapter_name
                    feishu_fields["last_seen_chapter_idx"] = chapter_index
                    if chapter_name:
                        feishu_fields["章节轨迹"] = chapter_name
                    if entry.get("last_known_state"):
                        feishu_fields["last_known_state"] = entry["last_known_state"]
                    if chapter_name:
                        feishu_fields["last_update_chapter"] = chapter_name
                        feishu_fields["历史变更记录"] = f"[{chapter_name}] v1: 新建"
                    result["insert"].append(feishu_fields)
                    # v2.7.0: 新建实体也算本章实射（首次建档需要生成初始形象）
                    _present_entities.append({
                        "entity_id": entity_id,
                        "name": name,
                        "category": entry.get("category", ""),
                        "lore": entry.get("lore", ""),
                        "visual_aura": entry.get("visual_aura", ""),
                        "lore_changed": True,  # 新建总算当作变化
                        "old_lore": "",
                    })

        logger.info(
            f"[local_diff] 差分结果: UPDATE={len(result['update'])} "
            f"INSERT={len(result['insert'])} ARCHIVE={len(result['archive'])}"
        )
        # v2.7.0: 附带本章 present_in_current=True 的实体信息（供 Stage 1.5 消费）
        result["present_entities"] = _present_entities
        return result

    # ------------------------------------------------------------------
    # 摘要生成 (供 DeepSeek delta_extract 参考，节省 Token)
    # ------------------------------------------------------------------
    @staticmethod
    def build_existing_summary(existing: dict, chapter_index: int = 0, max_entries: int = 60) -> str:
        """
        记忆衰减摘要：若实体连续 10 章未出现(chapter_index - last_seen_chapter_idx >= 10)，
        截断其 Lore 仅保留状态快照，节省 Token 也维持灵魂关联。
        """
        active = [
            v["fields"] for v in existing.values()
            if v["fields"].get("status") != "废弃"
        ]
        if not active:
            return "（当前小说记忆库为空，请进行完整初始建档）"

        if len(active) > max_entries:
            logger.warning(f"[MemoryEngine] 记忆词条 {len(active)} 条，截断至 {max_entries} 条供 Delta 参考")
            active = active[:max_entries]

        lines = ["【全量活跃实体池（当前库内已有活跃实体）：】"]
        for m in active:
            status_tag = f"[{m.get('status', '活跃')}]"
            
            # 衰减判断
            last_idx = m.get("last_seen_chapter_idx", chapter_index)
            try:
                last_idx = int(float(last_idx))
            except:
                last_idx = chapter_index

            is_decayed = (chapter_index - last_idx) >= 10
            
            if is_decayed:
                snapshot_state = m.get("last_known_state", "") or m.get("implicit_carry", "")
                short_lore = f"（休眠衰减快照）{snapshot_state}" if snapshot_state else f"（休眠中）{m.get('lore', '')[:30]}..."
                lines.append(
                    f"- {status_tag} [{m.get('category', '')}] "
                    f"{m.get('name', '')} (entity_id: {m.get('entity_id', '')}) | 最后出场: {m.get('last_seen_chapter', '?')} | "
                    f"当前精简快照: {short_lore}"
                )
            else:
                trace = str(m.get("章节轨迹", ""))
                trace_snippet = f"（轨迹：{trace}）" if trace else ""
                lines.append(
                    f"- {status_tag} [{m.get('category', '')}] "
                    f"{m.get('name', '')} {trace_snippet} (id: {m.get('entity_id', '')}) | "
                    f"上一次活跃: {m.get('last_seen_chapter', '?')} | "
                    f"视觉延续性约束: 必须在分镜中延续「{m.get('visual_aura', '')[:150].replace(chr(10), ' ')}」的美学特征 | "
                    f"逻辑快照: {m.get('last_known_state', '') or m.get('lore', '')[:80].replace(chr(10), ' ')}"
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

        # Step 2: 生成摘要（自动过滤废弃词条，应用衰减逻辑）
        existing_summary = MemoryEngine.build_existing_summary(existing, chapter_index)

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

        # Step 4: 本地 Diff（零网络开销）—传入 chapter_name 和 chapter_index 以追踪心跳
        diff = MemoryEngine.local_diff(existing, new_entries, chapter_name=chapter_name, chapter_index=chapter_index)

        # Step 5: 事务性写回飞书
        update_ok = 0
        insert_ids = []
        archive_ok = 0
        write_error = False

        if diff["update"]:
            update_ok = bitable.batch_update_memories(diff["update"])
            if update_ok == 0 and diff["update"]:
                logger.error("[MemoryEngine] batch_update_memories 全部失败")
                write_error = True

        if diff["insert"] and not write_error:
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
            # v2.7.0: 供 Stage 1.5 消费的本章实射实体列表
            "stage1_entities": diff.get("present_entities", []),
        }
