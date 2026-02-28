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
    "所属小说ID", "entity_id", "version", "last_update_chapter", "status"
]
REQUIRED_SCRIPT_FIELDS = [
    "集数/场次", "视觉提示词", "状态", "所属模型/网关", "章节处理状态", "所属章节文件名"
]

class FeishuBitableManager:
    def __init__(self):
        self.tenant_access_token = None
        self.token_expire_time = 0
        self._check_required_fields()
        
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
    # P0: 字段自校验 + 自动创建缺失字段
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
        field_type_map = {"version": 2}  # 数字类型
        single_select_fields = {"status", "章节处理状态"}

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
    def mark_chapter_stage1_complete(self, novel_id: str, chapter_name: str) -> bool:
        """
        在【剧本拆解表】中查找该 novel_id + chapter_name 的记录，
        将「章节处理状态」标记为「Stage1完成」。
        仅在 batch_update + batch_create 确认成功后调用，实现事务一致性。
        """
        search_url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{APP_TOKEN_SCRIPT}/tables/{TABLE_SCRIPT}/records/search"
        base_url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{APP_TOKEN_SCRIPT}/tables/{TABLE_SCRIPT}/records"
        try:
            payload = {
                "filter": {
                    "conjunction": "and",
                    "conditions": [
                        {"field_name": "所属小说ID", "operator": "is", "value": [novel_id]},
                        {"field_name": "所属章节文件名", "operator": "is", "value": [chapter_name]},
                    ]
                }
            }
            resp = requests.post(search_url, headers=self._get_headers(), json=payload)
            resp.raise_for_status()
            items = resp.json().get("data", {}).get("items", [])
            if not items:
                # 尚无该章节记录，创建占位标记
                create_resp = requests.post(
                    f"{base_url}",
                    headers=self._get_headers(),
                    json={"fields": {
                        "所属小说ID": novel_id,
                        "所属章节文件名": chapter_name,
                        "章节处理状态": "Stage1完成",
                    }}
                )
                create_resp.raise_for_status()
                logger.info(f"📌 [事务标记] 新建章节占位: novel={novel_id}, chapter={chapter_name}")
                return True
            # 更新已有记录
            record_id = items[0]["record_id"]
            upd_resp = requests.put(
                f"{base_url}/{record_id}",
                headers=self._get_headers(),
                json={"fields": {"章节处理状态": "Stage1完成"}}
            )
            upd_resp.raise_for_status()
            logger.info(f"✅ [事务标记] Stage1完成 标记成功: novel={novel_id}, chapter={chapter_name}")
            return True
        except Exception as e:
            logger.error(f"mark_chapter_stage1_complete 异常: {e}")
            return False

    def check_chapter_stage1_done(self, novel_id: str, chapter_name: str) -> bool:
        """检查该章节是否已完成 Stage1（用于断点续传跳过判断）"""
        search_url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{APP_TOKEN_SCRIPT}/tables/{TABLE_SCRIPT}/records/search"
        try:
            payload = {
                "filter": {
                    "conjunction": "and",
                    "conditions": [
                        {"field_name": "所属小说ID", "operator": "is", "value": [novel_id]},
                        {"field_name": "所属章节文件名", "operator": "is", "value": [chapter_name]},
                        {"field_name": "章节处理状态", "operator": "is", "value": ["Stage1完成"]},
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

    def insert_new_parsed_scenes(self, scenes_array, episode_start=1):
        url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{APP_TOKEN_SCRIPT}/tables/{TABLE_SCRIPT}/records/batch_create"
        records = []
        for i, scene in enumerate(scenes_array):
            content_desc = scene.get("summary", "")
            if "visual_logic" in scene:
                logic = scene["visual_logic"]
                visual_logic_text = f"【0-5s】{logic.get('shot_1_0_5s', '')}\n【5-10s】{logic.get('shot_2_5_10s', '')}\n【10-15s】{logic.get('shot_3_10_15s', '')}"
            else:
                visual_logic_text = scene.get("scene_desc", "")
                
            def flatten_prompt(prompt_data):
                if isinstance(prompt_data, dict):
                    return "\n".join([f"{k}: {v}" for k, v in prompt_data.items()])
                elif isinstance(prompt_data, list):
                    return "\n".join([str(item) for item in prompt_data])
                return str(prompt_data)

            visual_raw = scene.get("master_prompt", scene.get("visual_prompt", ""))
            visual_prompt = flatten_prompt(visual_raw)
            
            audio_raw = scene.get("audio_plan", scene.get("audio_prompt", ""))
            audio_prompt = flatten_prompt(audio_raw)
            
            records.append({"fields": {
                "集数/场次": episode_start + i,
                "小说原文（内容）": scene.get("novel_text", f"Scene {scene.get('scene_num', i+1)}"), 
                "状态": ["拆解中"],
                "场景描述": f"{content_desc}\n\n镜头逻辑:\n{visual_logic_text}",
                "视觉提示词": visual_prompt,
                "音频提示词": audio_prompt
            }})
        
        created_ids = []
        batch_size = 490
        for batch_start in range(0, len(records), batch_size):
            batch = records[batch_start: batch_start + batch_size]
            payload = {"records": batch}
            try:
                resp = requests.post(url, headers=self._get_headers(), json=payload)
                resp.raise_for_status()
                data = resp.json()
                if data.get("code") != 0:
                    raise Exception(f"Feishu API Error: {json.dumps(data, ensure_ascii=False)}")
                new_ids = [r.get("record_id") for r in data.get("data", {}).get("records", [])]
                if not new_ids:
                    raise Exception(f"No records returned by Feishu: {json.dumps(data, ensure_ascii=False)}")
                created_ids.extend(new_ids)
                logger.info(f"📋 第 {batch_start//batch_size+1} 批写入飞书完成 ({len(new_ids)} 条).")
            except Exception as e:
                err_msg = str(e)
                if hasattr(e, 'response') and e.response is not None:
                    err_msg += f" | Response: {e.response.text}"
                logger.error(f"Error bulk inserting batch: {err_msg}")
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
