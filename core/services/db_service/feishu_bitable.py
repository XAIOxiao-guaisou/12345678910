import time
import requests
import logging
import json
import os
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# ---------------------------------------------------------
# Feishu API Constants
# ---------------------------------------------------------
APP_ID = os.environ.get("FEISHU_APP_ID", "cli_a914c526d5f8dbc6")
APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "")

# The Assets (角色风格库)
APP_TOKEN_ASSETS = os.environ.get("FEISHU_APP_TOKEN_ASSETS", "Oj00bIhGVaq1cNsZsJhcMC58ndd")
TABLE_ASSETS = os.environ.get("FEISHU_TABLE_ASSETS", "tblC6L0fO7FXP3fI")

# The Factory (素材生成表)
APP_TOKEN_FACTORY = os.environ.get("FEISHU_APP_TOKEN_FACTORY", "Cu75bLeuJarqg1s7ysscaNolnPg")
TABLE_FACTORY = os.environ.get("FEISHU_TABLE_FACTORY", "tbloUrdwqG47ZmgI")

# The Brain (剧本拆解表)
APP_TOKEN_SCRIPT = os.environ.get("FEISHU_APP_TOKEN_SCRIPT", "J7OPbwEHqaJMefs1NLecTvA1n2e")
TABLE_SCRIPT = os.environ.get("FEISHU_TABLE_SCRIPT", "tbluFmGLkmqPTd9S")

# The Memory Hub (记忆中枢表)
APP_TOKEN_MEMORY = os.environ.get("FEISHU_APP_TOKEN_MEMORY", "XfWibZ0RjaPD1psTFwscKP1VnUb")
TABLE_MEMORY = os.environ.get("FEISHU_TABLE_MEMORY", "tblcFydnJuwD8cIy")

# -------------------------------------------------------
# Required fields per table (for startup validation)
# -------------------------------------------------------
REQUIRED_MEMORY_FIELDS = [
    "类别", "词条名", "深层设定逻辑", "视觉氛围与美学隐喻",
    "所属小说ID", "entity_id", "version", "last_update_chapter",
    "status", "历史变更记录", "引用分镜ID", "环境标签"
]
REQUIRED_SCRIPT_FIELDS = [
    "集数/场次", "视觉提示词", "状态", "所属模型/网关",
    "章节处理状态", "所属章节文件名", "任务ID", "关联记忆实体", "环境标签"
]

class FeishuBitableManager:
    def __init__(self):
        self.tenant_access_token = None
        self.token_expire_time = 0
        self._check_required_fields()
        self.purge_redundant_fields()  # 自动清理废弃字段（幂等）

    def _get_token(self):
        if time.time() < self.token_expire_time:
            return self.tenant_access_token
            
        url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
        payload = {"app_id": APP_ID, "app_secret": APP_SECRET}
        resp = requests.post(url, json=payload)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            raise Exception(f"Failed to get token: {data.get('msg')}")
            
        self.tenant_access_token = data.get("tenant_access_token")
        self.token_expire_time = time.time() + data.get("expire") - 600
        return self.tenant_access_token

    def _get_headers(self):
        return {
            "Authorization": f"Bearer {self._get_token()}",
            "Content-Type": "application/json"
        }

    def _purge_table(self, app_token, table_id, label="表格"):
        from concurrent.futures import ThreadPoolExecutor, as_completed
        search_url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records/search"
        base_url   = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records"

        try:
            all_ids = []
            page_token = None
            while True:
                payload = {"page_size": 500}
                if page_token: payload["page_token"] = page_token
                resp = requests.post(search_url, headers=self._get_headers(), json=payload)
                resp.raise_for_status()
                data = resp.json().get("data", {})
                all_ids.extend(r["record_id"] for r in data.get("items", []))
                page_token = data.get("page_token")
                if not data.get("has_more"): break

            total = len(all_ids)
            if total == 0:
                logger.info(f"🧹 [{label}] 已是空表。")
                return 0

            logger.info(f"🧹 [{label}] 删除 {total} 条记录...")

            def _del_one(rid):
                r = requests.delete(f"{base_url}/{rid}", headers=self._get_headers())
                try:
                    body = r.json()
                    return rid, body.get("code", -1), body.get("data", {}).get("deleted", False)
                except Exception:
                    return rid, -1, False

            deleted = 0
            with ThreadPoolExecutor(max_workers=5) as pool:
                futures = {pool.submit(_del_one, rid): rid for rid in all_ids}
                for ft in as_completed(futures):
                    rid, code, is_del = ft.result()
                    if code == 0 and is_del: deleted += 1

            return deleted
        except Exception as e:
            logger.error(f"_purge_table [{label}] 异常: {e}")
            return 0

    def purge_all_records(self): return self._purge_table(APP_TOKEN_SCRIPT, TABLE_SCRIPT, "剧本拆解表")
    def purge_factory_records(self): return self._purge_table(APP_TOKEN_FACTORY, TABLE_FACTORY, "素材生成表")
    def purge_assets_records(self): return self._purge_table(APP_TOKEN_ASSETS, TABLE_ASSETS, "角色风格库")
    def purge_memory_records(self): return self._purge_table(APP_TOKEN_MEMORY, TABLE_MEMORY, "记忆中枢")

    # =======================================================
    # 空白行检测（优先填入而非追加）
    # =======================================================
    def _get_blank_record_ids(self, app_token: str, table_id: str,
                               key_field: str = "集数/场次") -> list:
        """
        扫描表中「key_field」为空的行，返回 record_id 列表。
        用于「空白行优先填入」策略：避免表中已有空行被跳过。
        """
        search_url = (
            f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}"
            f"/tables/{table_id}/records/search"
        )
        blank_ids = []
        page_token = None
        try:
            while True:
                payload = {
                    "page_size": 500,
                    "filter": {
                        "conjunction": "and",
                        "conditions": [{"field_name": key_field, "operator": "isEmpty"}]
                    }
                }
                if page_token:
                    payload["page_token"] = page_token
                resp = requests.post(search_url, headers=self._get_headers(), json=payload)
                resp.raise_for_status()
                data = resp.json().get("data", {})
                blank_ids.extend(r["record_id"] for r in data.get("items", []))
                page_token = data.get("page_token")
                if not data.get("has_more"):
                    break
        except Exception as e:
            logger.warning(f"_get_blank_record_ids 扫描失败（降级为纯创建模式）: {e}")
        logger.info(f"📋 [空白行扫描] 发现 {len(blank_ids)} 个可填入行")
        return blank_ids

    # =======================================================
    # 分镜状态推进（解决永久停留「拆解中」）
    # =======================================================
    def update_scenes_status(self, record_ids: list, status: str = "已拆解") -> int:
        """
        批量将剧本分镜的「状态」字段从「拆解中」推进到目标状态。
        在 insert_new_parsed_scenes 写入成功后自动调用，
        也可在 Stage2 流程完成后再次调用推进到「Stage2完成」。
        """
        if not record_ids:
            return 0
        base_url = (
            f"https://open.feishu.cn/open-apis/bitable/v1/apps/{APP_TOKEN_SCRIPT}"
            f"/tables/{TABLE_SCRIPT}/records"
        )
        updates = [
            {"record_id": rid, "fields": {"状态": [status]}}
            for rid in record_ids
        ]
        ok = 0
        for start in range(0, len(updates), 100):
            batch = updates[start:start + 100]
            try:
                resp = requests.post(
                    f"{base_url}/batch_update",
                    headers=self._get_headers(),
                    json={"records": batch}
                )
                resp.raise_for_status()
                body = resp.json()
                if body.get("code") == 0:
                    ok += len(body.get("data", {}).get("records", []))
                else:
                    logger.error(f"update_scenes_status 错误: {body.get('msg')}")
            except Exception as e:
                logger.error(f"update_scenes_status 批次 {start // 100 + 1} 异常: {e}")
        logger.info(f"✅ [状态推进] {ok}/{len(record_ids)} 条分镜 → '{status}'")
        return ok

    # =======================================================
    # 记忆词条 ↔ 分镜记录 双向关联
    # =======================================================
    def link_memory_to_scenes(self, entity_id_to_script_rids: dict) -> int:
        """
        将分镜 record_id 追加写入对应记忆词条的「引用分镜ID」字段。
        entity_id_to_script_rids: {entity_id: [script_record_id, ...]}
        实现记忆中枢 → 剧本的正向关联（两表互通）。
        """
        if not entity_id_to_script_rids:
            return 0
        search_url = (
            f"https://open.feishu.cn/open-apis/bitable/v1/apps/{APP_TOKEN_MEMORY}"
            f"/tables/{TABLE_MEMORY}/records/search"
        )
        updates = []
        for entity_id, script_rids in entity_id_to_script_rids.items():
            if not script_rids:
                continue
            try:
                resp = requests.post(search_url, headers=self._get_headers(), json={
                    "filter": {"conjunction": "and", "conditions": [
                        {"field_name": "entity_id", "operator": "is", "value": [entity_id]}
                    ]}
                })
                resp.raise_for_status()
                items = resp.json().get("data", {}).get("items", [])
                if not items:
                    continue
                mem_rec_id = items[0]["record_id"]
                existing_refs = self._flatten(items[0].get("fields", {}).get("引用分镜ID", ""))
                new_part = ",".join(script_rids)
                merged = f"{existing_refs},{new_part}".strip(",") if existing_refs else new_part
                updates.append((mem_rec_id, {"引用分镜ID": merged}))
            except Exception as e:
                logger.error(f"link_memory_to_scenes entity_id={entity_id}: {e}")
        if updates:
            return self.batch_update_memories(updates)
        return 0


    # =======================================================
    def _list_table_fields(self, app_token, table_id):
        """获取指定表的所有字段名列表"""
        url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/fields"
        try:
            resp = requests.get(url, headers=self._get_headers())
            resp.raise_for_status()
            data = resp.json().get("data", {})
            return [f["field_name"] for f in data.get("items", [])]
        except Exception as e:
            logger.error(f"_list_table_fields 失败: {e}")
            return []

    def _list_table_fields_with_ids(self, app_token: str, table_id: str) -> dict:
        """返回 {field_name: field_id} 映射，用于字段删除操作。"""
        url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/fields"
        result = {}
        try:
            resp = requests.get(url, headers=self._get_headers())
            resp.raise_for_status()
            for f in resp.json().get("data", {}).get("items", []):
                result[f["field_name"]] = f["field_id"]
        except Exception as e:
            logger.error(f"_list_table_fields_with_ids 失败: {e}")
        return result

    def _delete_field(self, app_token: str, table_id: str, field_id: str, label: str = "") -> bool:
        """删除飞书多维表格中的指定字段（安全白名单机制保护）。"""
        url = (f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}"
               f"/tables/{table_id}/fields/{field_id}")
        try:
            resp = requests.delete(url, headers=self._get_headers())
            resp.raise_for_status()
            body = resp.json()
            if body.get("code") == 0:
                logger.info(f"🗑️ 字段删除成功: [{label}]")
                return True
            logger.warning(f"字段删除响应异常: [{label}] {body.get('msg')}")
            return False
        except Exception as e:
            logger.error(f"_delete_field [{label}] 异常: {e}")
            return False

    def purge_redundant_fields(self) -> dict:
        """
        程序化清理已确认废弃的字段（白名单机制，仅删除下列明确无写入路径的字段）。
          剧本拆解表: 音频提示词（Stage2 已不再写入，字段恒为空）
        Returns: {table_label: [deleted_field_names]}
        """
        REDUNDANT = {
            "剧本拆解表": (APP_TOKEN_SCRIPT, TABLE_SCRIPT, ["音频提示词"]),
        }
        report = {}
        for label, (app_token, table_id, fields_to_del) in REDUNDANT.items():
            field_map = self._list_table_fields_with_ids(app_token, table_id)
            deleted = []
            for fname in fields_to_del:
                fid = field_map.get(fname)
                if not fid:
                    logger.info(f"🔍 [{label}] 字段 '{fname}' 不存在（可能已删除），跳过")
                    continue
                if self._delete_field(app_token, table_id, fid, label=f"{label}.{fname}"):
                    deleted.append(fname)
            report[label] = deleted
            logger.info(f"🧹 [{label}] 废弃字段清理: {deleted}")
        return report

    def _create_field(self, app_token, table_id, field_name, field_type=1):
        """
        在飞书表中创建字段。
        field_type: 1=文本, 2=数字, 3=单选, 4=多选
        """
        url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/fields"
        payload = {"field_name": field_name, "type": field_type}
        try:
            resp = requests.post(url, headers=self._get_headers(), json=payload)
            resp.raise_for_status()
            body = resp.json()
            if body.get("code") == 0:
                logger.info(f"✅ 字段 [{field_name}] 创建成功")
                return True
            else:
                logger.warning(f"⚠️ 字段 [{field_name}] 创建响应: {body.get('msg')}")
                return False
        except Exception as e:
            logger.error(f"_create_field [{field_name}] 异常: {e}")
            return False

    def _check_required_fields(self):
        """
        启动时校验必要字段是否存在，缺失字段自动尝试创建。
        若创建失败则打 CRITICAL 警报，但不阻断启动。
        """
        checks = [
            (APP_TOKEN_MEMORY, TABLE_MEMORY, "记忆中枢", REQUIRED_MEMORY_FIELDS),
            (APP_TOKEN_SCRIPT, TABLE_SCRIPT, "剧本拆解表", REQUIRED_SCRIPT_FIELDS),
        ]
        # 字段类型映射
        field_type_map = {"version": 2}  # 2 = 数字类型
        single_select_fields = {"status", "章节处理状态", "状态", "环境标签"}

        for app_token, table_id, label, required in checks:
            try:
                existing = self._list_table_fields(app_token, table_id)
                missing = [f for f in required if f not in existing]
                if not missing:
                    logger.info(f"✅ [{label}] 字段校验通过，共 {len(existing)} 个字段")
                    continue
                logger.warning(f"🟡 [{label}] 缺失字段: {missing}，尝试自动创建...")
                still_missing = []
                for field_name in missing:
                    ftype = field_type_map.get(field_name, 3 if field_name in single_select_fields else 1)
                    ok = self._create_field(app_token, table_id, field_name, ftype)
                    if not ok:
                        still_missing.append(field_name)
                if still_missing:
                    logger.critical(
                        f"🔴 [{label}] 以下字段自动创建失败，请手动在飞书表中添加（文本类型）：{still_missing}\n"
                        f"   精准RAG 和 断点续传功能将在该表字段补全前受限。"
                    )
            except Exception as e:
                logger.error(f"_check_required_fields [{label}] 异常: {e}")

    # =======================================================
    # P0: 全本记忆读取（完整 has_more 翻页，防逻辑回滚）
    # =======================================================
    def get_memories_by_novel(self, novel_id: str) -> dict:
        """
        按 novel_id 过滤召回该小说全部记忆词条。
        返回 {entity_id: {"record_id": ..., "fields": {...}}} Dict。
        使用完整 while has_more 分页，保证 > 500 条长篇场景不漏读。
        """
        search_url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{APP_TOKEN_MEMORY}/tables/{TABLE_MEMORY}/records/search"
        result = {}
        page_token = None
        total_fetched = 0

        while True:
            payload = {
                "page_size": 500,
                "filter": {
                    "conjunction": "and",
                    "conditions": [{
                        "field_name": "所属小说ID",
                        "operator": "is",
                        "value": [novel_id]
                    }]
                }
            }
            if page_token:
                payload["page_token"] = page_token
            try:
                resp = requests.post(search_url, headers=self._get_headers(), json=payload)
                resp.raise_for_status()
                data = resp.json().get("data", {})
                items = data.get("items", [])
                for r in items:
                    fields = r.get("fields", {})
                    eid = self._flatten(fields.get("entity_id", ""))
                    if not eid:
                        # 兼容旧数据：entity_id 为空时用 sha1(novel_id+词条名)
                        import hashlib
                        raw_name = self._flatten(fields.get("词条名", r["record_id"]))
                        eid = f"e_{hashlib.sha1(f'{novel_id}:{raw_name}'.encode()).hexdigest()[:8]}"
                    result[eid] = {
                        "record_id": r["record_id"],
                        "fields": {
                            "category": self._flatten(fields.get("类别", "")),
                            "name": self._flatten(fields.get("词条名", "")),
                            "lore": self._flatten(fields.get("深层设定逻辑", "")),
                            "visual_aura": self._flatten(fields.get("视觉氛围与美学隐喻", "")),
                            "status": self._flatten(fields.get("status", "活跃")),
                            "version": fields.get("version", 1),
                            "entity_id": eid,
                        }
                    }
                total_fetched += len(items)
                page_token = data.get("page_token")
                if not data.get("has_more"):
                    break
            except Exception as e:
                logger.error(f"get_memories_by_novel 翻页异常 (novel_id={novel_id}): {e}")
                break

        logger.info(f"📚 [记忆召回] novel_id={novel_id}，共读取 {total_fetched} 条词条")
        return result

    # =======================================================
    # P2: 精准 entity_id 召回（Stage2 RAG 专用，防止 context 稀释）
    # =======================================================
    def get_memories_by_entity_ids(self, entity_ids: list) -> list:
        """
        按 entity_id 列表精准批量召回记忆词条。
        用于 Stage2 生成时，根据 scene 的 entity_ids 字段精确拉取
        视觉约束（visual_aura），避免模糊关键词匹配导致的 context 稀释。

        Args:
            entity_ids: 目标 entity_id 字符串列表
        Returns:
            [{"entity_id": str, "name": str, "visual_aura": str, "lore": str}, ...]
        """
        if not entity_ids:
            return []
        search_url = (
            f"https://open.feishu.cn/open-apis/bitable/v1/apps/{APP_TOKEN_MEMORY}"
            f"/tables/{TABLE_MEMORY}/records/search"
        )
        result = []
        # 飞书 filter 限制条件数量，分批处理（每批 ≤ 20 个）
        batch_size = 20
        for start in range(0, len(entity_ids), batch_size):
            batch = entity_ids[start: start + batch_size]
            conditions = [
                {"field_name": "entity_id", "operator": "is", "value": [eid]}
                for eid in batch
            ]
            payload = {
                "filter": {
                    "conjunction": "or",
                    "conditions": conditions
                }
            }
            try:
                resp = requests.post(search_url, headers=self._get_headers(), json=payload)
                resp.raise_for_status()
                items = resp.json().get("data", {}).get("items", [])
                for r in items:
                    fields = r.get("fields", {})
                    # 跳过废弃词条
                    if self._flatten(fields.get("status", "")) == "废弃":
                        continue
                    result.append({
                        "entity_id":   self._flatten(fields.get("entity_id", "")),
                        "category":    self._flatten(fields.get("类别", "")),
                        "name":        self._flatten(fields.get("词条名", "")),
                        "lore":        self._flatten(fields.get("深层设定逻辑", "")),
                        "visual_aura": self._flatten(fields.get("视觉氛围与美学隐喻", "")),
                    })
            except Exception as e:
                logger.error(f"get_memories_by_entity_ids 第 {start//batch_size+1} 批异常: {e}")
        logger.info(f"🎯 [精准召回] 请求 {len(entity_ids)} 个 entity_id，召回 {len(result)} 条（已过滤废弃）")
        return result

    # =======================================================
    # P0: 批量更新记忆词条（含 archive 逻辑，version 自增）
    # =======================================================
    def batch_update_memories(self, updates: list) -> int:
        """
        批量更新飞书记忆词条。updates 格式：
        [(record_id, field_dict), ...]
        其中 field_dict 需符合飞书字段名。
        飞书限制 100 条/批，自动分批。
        """
        if not updates:
            return 0
        base_url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{APP_TOKEN_MEMORY}/tables/{TABLE_MEMORY}/records"
        total_ok = 0
        batch_size = 100
        for start in range(0, len(updates), batch_size):
            batch = updates[start: start + batch_size]
            records = [{"record_id": rid, "fields": fields} for rid, fields in batch]
            url = f"{base_url}/batch_update"
            try:
                resp = requests.post(url, headers=self._get_headers(), json={"records": records})
                resp.raise_for_status()
                body = resp.json()
                if body.get("code") == 0:
                    n = len(body.get("data", {}).get("records", []))
                    total_ok += n
                    logger.info(f"✏️ 批量更新记忆词条 {start//batch_size+1} 批完成 ({n} 条)")
                else:
                    logger.error(f"batch_update_memories 错误: {body.get('msg')}")
            except Exception as e:
                logger.error(f"batch_update_memories 第 {start//batch_size+1} 批异常: {e}")
        return total_ok

    def batch_create_memories(self, inserts: list) -> list:
        """
        批量创建飞书记忆词条。inserts 格式：
        [field_dict, ...]
        返回新创建的 record_id 列表。
        飞书限制 100 条/批（insert 端限制与 batch_create 端一致），自动分批。
        """
        if not inserts:
            return []
        url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{APP_TOKEN_MEMORY}/tables/{TABLE_MEMORY}/records/batch_create"
        created_ids = []
        batch_size = 100
        for start in range(0, len(inserts), batch_size):
            batch = inserts[start: start + batch_size]
            try:
                resp = requests.post(url, headers=self._get_headers(), json={"records": [{"fields": f} for f in batch]})
                resp.raise_for_status()
                body = resp.json()
                if body.get("code") == 0:
                    ids = [r["record_id"] for r in body.get("data", {}).get("records", [])]
                    created_ids.extend(ids)
                    logger.info(f"➕ 批量创建记忆词条 {start//batch_size+1} 批完成 ({len(ids)} 条)")
                else:
                    logger.error(f"batch_create_memories 错误: {body.get('msg')}")
            except Exception as e:
                logger.error(f"batch_create_memories 第 {start//batch_size+1} 批异常: {e}")
        return created_ids

    # =======================================================
    # P0: 事务性 Stage1 完成标记（写回飞书成功后才标记）
    # =======================================================
    def mark_chapter_stage1_complete(self, novel_id: str, chapter_name: str,
                                      sandbox_mode: bool = False) -> bool:
        """
        在【剧本拆解表】中标记该章节 Stage1 已完成。
        sandbox_mode: True → 标记为「sandbox」环境，与生产标记完全隔离。
        仅在 batch_update + batch_create 确认成功后调用，实现事务一致性。
        """
        search_url = (f"https://open.feishu.cn/open-apis/bitable/v1/apps/{APP_TOKEN_SCRIPT}"
                      f"/tables/{TABLE_SCRIPT}/records/search")
        base_url = (f"https://open.feishu.cn/open-apis/bitable/v1/apps/{APP_TOKEN_SCRIPT}"
                    f"/tables/{TABLE_SCRIPT}/records")
        env_label = "sandbox" if sandbox_mode else "production"
        status_tag = "Stage1完成[沙盒]" if sandbox_mode else "Stage1完成"
        try:
            payload = {
                "filter": {
                    "conjunction": "and",
                    "conditions": [
                        {"field_name": "所属小说ID", "operator": "is", "value": [novel_id]},
                        {"field_name": "所属章节文件名", "operator": "is", "value": [chapter_name]},
                        {"field_name": "环境标签", "operator": "is", "value": [env_label]},
                    ]
                }
            }
            resp = requests.post(search_url, headers=self._get_headers(), json=payload)
            resp.raise_for_status()
            items = resp.json().get("data", {}).get("items", [])
            fields_to_write = {
                "所属小说ID": novel_id,
                "所属章节文件名": chapter_name,
                "章节处理状态": status_tag,
                "环境标签": env_label,
            }
            if not items:
                create_resp = requests.post(base_url, headers=self._get_headers(),
                                            json={"fields": fields_to_write})
                create_resp.raise_for_status()
                logger.info(f"📌 [事务标记] 新建占位[{env_label}]: novel={novel_id}, chapter={chapter_name}")
                return True
            record_id = items[0]["record_id"]
            upd_resp = requests.put(f"{base_url}/{record_id}", headers=self._get_headers(),
                                    json={"fields": {"章节处理状态": status_tag}})
            upd_resp.raise_for_status()
            logger.info(f"✅ [事务标记][{env_label}] Stage1完成: novel={novel_id}, chapter={chapter_name}")
            return True
        except Exception as e:
            logger.error(f"mark_chapter_stage1_complete 异常: {e}")
            return False

    def check_chapter_stage1_done(self, novel_id: str, chapter_name: str,
                                   sandbox_mode: bool = False) -> bool:
        """
        检查该章节是否已完成 Stage1（用于断点续传跳过判断）。
        sandbox_mode: True → 仅起漫 sandbox 运行标记，不与生产记录混淆。
        """
        search_url = (f"https://open.feishu.cn/open-apis/bitable/v1/apps/{APP_TOKEN_SCRIPT}"
                      f"/tables/{TABLE_SCRIPT}/records/search")
        env_label = "sandbox" if sandbox_mode else "production"
        status_tag = "Stage1完成[沙盒]" if sandbox_mode else "Stage1完成"
        try:
            payload = {
                "filter": {
                    "conjunction": "and",
                    "conditions": [
                        {"field_name": "所属小说ID", "operator": "is", "value": [novel_id]},
                        {"field_name": "所属章节文件名", "operator": "is", "value": [chapter_name]},
                        {"field_name": "章节处理状态", "operator": "is", "value": [status_tag]},
                        {"field_name": "环境标签", "operator": "is", "value": [env_label]},
                    ]
                }
            }
            resp = requests.post(search_url, headers=self._get_headers(), json=payload)
            resp.raise_for_status()
            items = resp.json().get("data", {}).get("items", [])
            return len(items) > 0
        except Exception as e:
            logger.error(f"check_chapter_stage1_done 异常: {e}")
            return False

    @staticmethod
    def _flatten(val) -> str:
        """统一 flatten 飞书富文本字段"""
        if isinstance(val, str): return val
        if isinstance(val, list): return "".join(seg.get("text", "") if isinstance(seg, dict) else str(seg) for seg in val)
        if val is None: return ""
        return str(val)

    def upsert_character_in_assets(self, character_name, appearance="", hasselblad="Hasselblad H6D-100c, 80mm, f/2.8"):
        search_url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{APP_TOKEN_ASSETS}/tables/{TABLE_ASSETS}/records/search"
        create_url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{APP_TOKEN_ASSETS}/tables/{TABLE_ASSETS}/records"
        fields = {
            "角色名": character_name,
            "外貌特征描述": appearance or f"{character_name}，气质出众。",
            "哈苏预设参数": hasselblad
        }
        try:
            resp = requests.post(search_url, headers=self._get_headers(), json={
                "filter": {"conjunction": "and", "conditions": [{"field_name": "角色名", "operator": "is", "value": [character_name]}]}
            })
            resp.raise_for_status()
            items = resp.json().get("data", {}).get("items", [])
            if items:
                rec_id = items[0]["record_id"]
                if appearance:
                    upd_url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{APP_TOKEN_ASSETS}/tables/{TABLE_ASSETS}/records/{rec_id}"
                    requests.put(upd_url, headers=self._get_headers(), json={"fields": fields})
                return rec_id
            
            cr = requests.post(create_url, headers=self._get_headers(), json={"fields": fields})
            if cr.ok:
                return cr.json().get("data", {}).get("record", {}).get("record_id")
        except Exception as e:
            logger.error(f" upsert_character_in_assets 失效: {e}")
        return None

    def get_style_reference(self, character_name):
        url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{APP_TOKEN_ASSETS}/tables/{TABLE_ASSETS}/records/search"
        payload = {"filter": {"conjunction": "and", "conditions": [{"field_name": "角色名", "operator": "is", "value": [character_name]}]}}
        try:
            resp = requests.post(url, headers=self._get_headers(), json=payload)
            resp.raise_for_status()
            records = resp.json().get("data", {}).get("items", [])
            if not records:
                return "Hasselblad H6D-100c, 80mm, f/2.8."
                
            fields = records[0].get("fields", {})
            desc = fields.get("外貌特征描述", "")
            hasselblad = fields.get("哈苏预设参数", "")
            
            def flatten(val):
                if isinstance(val, str): return val
                if isinstance(val, list): return "".join(seg.get("text", "") if isinstance(seg, dict) else str(seg) for seg in val)
                return str(val)
                
            return f"{flatten(hasselblad)}. {flatten(desc)}"
        except Exception as e:
            return "Hasselblad H6D-100c, 80mm, f/2.8."

    def insert_memory_records(self, memory_array, novel_id: str = ""):
        """
        写入阶段一全量生成的记忆词条（初始建档使用）。
        v2.6.0: 新增 novel_id / entity_id / version / status 字段写入。
        增量更新请使用 MemoryEngine.evolve_memory + batch_create_memories。
        """
        if not memory_array: return []
        import hashlib
        url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{APP_TOKEN_MEMORY}/tables/{TABLE_MEMORY}/records/batch_create"
        records = []
        for mem in memory_array:
            name = mem.get("name", "未命名")
            eid = f"e_{hashlib.sha1(f'{novel_id}:{name}'.encode()).hexdigest()[:8]}" if novel_id else ""
            field = {
                "类别": mem.get("category", "设定"),
                "词条名": name,
                "深层设定逻辑": mem.get("lore", ""),
                "视觉氛围与美学隐喻": mem.get("visual_aura", ""),
                "status": "活跃",
                "version": 1,
            }
            if novel_id:
                field["所属小说ID"] = novel_id
            if eid:
                field["entity_id"] = eid
            records.append({"fields": field})

        created_ids = []
        batch_size = 100  # 飞书 batch_create 建议 ≤ 100
        for batch_start in range(0, len(records), batch_size):
            batch = records[batch_start: batch_start + batch_size]
            try:
                resp = requests.post(url, headers=self._get_headers(), json={"records": batch})
                resp.raise_for_status()
                data = resp.json()
                if data.get("code") != 0:
                    logger.error(f"Feishu Memory Insert Error: {json.dumps(data, ensure_ascii=False)}")
                    continue
                new_ids = [r.get("record_id") for r in data.get("data", {}).get("records", [])]
                created_ids.extend(new_ids)
                logger.info(f"🧠 第 {batch_start//batch_size+1} 批记忆词条写入飞书完成 ({len(new_ids)} 条).")
            except Exception as e:
                logger.error(f"Error bulk inserting memory batch: {e}")
        return created_ids

    def get_all_memories(self, novel_id: str = ""):
        """
        读取记忆词条（可选按 novel_id 过滤）。
        v2.6.0 推荐使用 get_memories_by_novel(novel_id) 获取结构化 Dict。
        此方法保留用于兼容 pipeline.py 旧调用路径。
        """
        if novel_id:
            mem_dict = self.get_memories_by_novel(novel_id)
            return [v["fields"] for v in mem_dict.values()]

        search_url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{APP_TOKEN_MEMORY}/tables/{TABLE_MEMORY}/records/search"
        all_memories = []
        page_token = None
        try:
            while True:
                payload = {"page_size": 500}
                if page_token: payload["page_token"] = page_token
                resp = requests.post(search_url, headers=self._get_headers(), json=payload)
                resp.raise_for_status()
                data = resp.json().get("data", {})
                for r in data.get("items", []):
                    fields = r.get("fields", {})
                    all_memories.append({
                        "category": self._flatten(fields.get("类别", "")),
                        "name": self._flatten(fields.get("词条名", "")),
                        "lore": self._flatten(fields.get("深层设定逻辑", "")),
                        "visual_aura": self._flatten(fields.get("视觉氛围与美学隐喻", "")),
                        "status": self._flatten(fields.get("status", "活跃")),
                        "entity_id": self._flatten(fields.get("entity_id", "")),
                    })
                page_token = data.get("page_token")
                if not data.get("has_more"): break
            return all_memories
        except Exception as e:
            logger.error(f"Error fetching memories: {e}")
            return []

    def insert_new_parsed_scenes(
        self,
        scenes_array,
        episode_start=1,
        task_id: str = "",
        sandbox_mode: bool = False,
        novel_id: str = "",
        chapter_name: str = "",
        gateway: str = "",
    ) -> list:
        """
        写入分镜到【剧本拆解表】— v2.7 重构版。
        变更：
          · 空白行优先填入（不再跳过现有空行）
          · sandbox/production 环境标签隔离
          · 自动推进状态 拆解中 → 已拆解（解决永久停留问题）
          · 写入「关联记忆实体」字段（scene.entity_ids → 逗号字符串）
          · 移除「音频提示词」写入（该字段已废弃）
          · 修正「gateway」参数（之前为无效关键字参数）
        """
        create_url = (
            f"https://open.feishu.cn/open-apis/bitable/v1/apps/{APP_TOKEN_SCRIPT}"
            f"/tables/{TABLE_SCRIPT}/records/batch_create"
        )
        update_url = (
            f"https://open.feishu.cn/open-apis/bitable/v1/apps/{APP_TOKEN_SCRIPT}"
            f"/tables/{TABLE_SCRIPT}/records/batch_update"
        )
        env_label = "sandbox" if sandbox_mode else "production"

        def flatten_prompt(prompt_data):
            if isinstance(prompt_data, dict):
                return "\n".join([f"{k}: {v}" for k, v in prompt_data.items()])
            elif isinstance(prompt_data, list):
                return "\n".join([str(item) for item in prompt_data])
            return str(prompt_data)

        records = []
        for i, scene in enumerate(scenes_array):
            content_desc = scene.get("summary", "")
            if "visual_logic" in scene:
                logic = scene["visual_logic"]
                visual_logic_text = (
                    f"【0-5s】{logic.get('shot_1_0_5s', '')}\n"
                    f"【5-10s】{logic.get('shot_2_5_10s', '')}\n"
                    f"【10-15s】{logic.get('shot_3_10_15s', '')}"
                )
            else:
                visual_logic_text = scene.get("scene_desc", "")

            visual_raw = scene.get("master_prompt", scene.get("visual_prompt", ""))
            visual_prompt = flatten_prompt(visual_raw)

            scene_fields = {
                "集数/场次": episode_start + i,
                "小说原文（内容）": scene.get("novel_text", f"Scene {scene.get('scene_num', i+1)}"),
                "状态": ["拆解中"],
                "场景描述": f"{content_desc}\n\n镜头逻辑:\n{visual_logic_text}",
                "视觉提示词": visual_prompt,
                "环境标签": env_label,
            }
            if task_id:
                scene_fields["任务ID"] = task_id
            if novel_id:
                scene_fields["所属小说ID"] = novel_id
            if chapter_name:
                scene_fields["所属章节文件名"] = chapter_name
            if gateway:
                scene_fields["所属模型/网关"] = gateway

            # 关联记忆实体（scene 若携带 entity_ids 则写入）
            entity_ids = scene.get("entity_ids", [])
            if entity_ids:
                if isinstance(entity_ids, list):
                    scene_fields["关联记忆实体"] = ",".join(str(e) for e in entity_ids)
                else:
                    scene_fields["关联记忆实体"] = str(entity_ids)

            records.append({"fields": scene_fields})

        # ── Phase 1: 空白行优先填入 ──────────────────────────────
        blank_ids = self._get_blank_record_ids(APP_TOKEN_SCRIPT, TABLE_SCRIPT, "集数/场次")
        created_ids = []

        if blank_ids and records:
            fill_count = min(len(blank_ids), len(records))
            fill_payload = [
                {"record_id": blank_ids[j], "fields": records[j]["fields"]}
                for j in range(fill_count)
            ]
            for start in range(0, len(fill_payload), 100):
                batch = fill_payload[start:start + 100]
                try:
                    resp = requests.post(update_url, headers=self._get_headers(),
                                         json={"records": batch})
                    resp.raise_for_status()
                    body = resp.json()
                    if body.get("code") == 0:
                        ids = [r["record_id"] for r in body.get("data", {}).get("records", [])]
                        created_ids.extend(ids)
                        logger.info(f"📝 [空白行填入] 第{start//100+1}批: {len(ids)} 条")
                    else:
                        logger.error(f"空白行填入错误: {body.get('msg')}")
                except Exception as e:
                    logger.error(f"空白行填入批次异常: {e}")
            records = records[fill_count:]  # 剩余走 batch_create

        # ── Phase 2: 剩余记录 batch_create ──────────────────────
        batch_size = 490
        for batch_start in range(0, len(records), batch_size):
            batch = records[batch_start: batch_start + batch_size]
            try:
                resp = requests.post(create_url, headers=self._get_headers(),
                                     json={"records": batch})
                resp.raise_for_status()
                data = resp.json()
                if data.get("code") != 0:
                    raise Exception(f"Feishu API Error: {json.dumps(data, ensure_ascii=False)}")
                new_ids = [r.get("record_id") for r in data.get("data", {}).get("records", [])]
                if not new_ids:
                    raise Exception(f"No records returned: {json.dumps(data, ensure_ascii=False)}")
                created_ids.extend(new_ids)
                logger.info(f"📋 第{batch_start//batch_size+1}批写入飞书完成 ({len(new_ids)} 条)")
            except Exception as e:
                err_msg = str(e)
                if hasattr(e, "response") and e.response is not None:
                    err_msg += f" | Response: {e.response.text}"
                logger.error(f"insert_new_parsed_scenes 批次异常: {err_msg}")

        # ── Phase 3: 状态推进 拆解中 → 已拆解 ─────────────────────
        if created_ids:
            self.update_scenes_status(created_ids, "已拆解")

        return created_ids



    def create_factory_stubs(self, script_record_ids, character="陈伶"):
        if not script_record_ids: return 0
        url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{APP_TOKEN_FACTORY}/tables/{TABLE_FACTORY}/records/batch_create"
        records = [{"fields": {"关联剧本": [rid], "角色 ID": character}} for rid in script_record_ids]
        
        batch_size = 490
        total = 0
        for batch_start in range(0, len(records), batch_size):
            batch = records[batch_start: batch_start + batch_size]
            try:
                resp = requests.post(url, headers=self._get_headers(), json={"records": batch})
                if resp.ok:
                    total += len(resp.json().get("data", {}).get("records", []))
            except Exception: pass
        return total

    def get_records_by_task_id(self, task_id: str) -> list:
        """
        通过 任务ID 精准召回该批次下的所有生成的剧本记录。
        ⚠️ 这依赖于【剧本拆解表】中“任务ID”字段被设置为“文本”类型，并开启了属性检索(Indexing)。
        """
        if not task_id: return []
        search_url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{APP_TOKEN_SCRIPT}/tables/{TABLE_SCRIPT}/records/search"
        all_items = []
        page_token = None
        
        while True:
            payload = {
                "page_size": 500,
                "filter": {
                    "conjunction": "and",
                    "conditions": [
                        {"field_name": "任务ID", "operator": "is", "value": [task_id]}
                    ]
                }
            }
            if page_token:
                payload["page_token"] = page_token
                
            try:
                resp = requests.post(search_url, headers=self._get_headers(), json=payload)
                resp.raise_for_status()
                data = resp.json().get("data", {})
                items = data.get("items", [])
                all_items.extend(items)
                
                page_token = data.get("page_token")
                if not data.get("has_more"):
                    break
            except Exception as e:
                logger.error(f"get_records_by_task_id 异常 ({task_id}): {e}")
                break
                
        logger.info(f"🔎 [任务回溯] 任务 {task_id} 共找回 {len(all_items)} 个分镜")
        return all_items
