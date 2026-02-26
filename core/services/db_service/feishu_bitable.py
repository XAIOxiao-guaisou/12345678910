import time
import requests
import logging
import json
import os

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

class FeishuBitableManager:
    def __init__(self):
        self.tenant_access_token = None
        self.token_expire_time = 0
        
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

    def insert_memory_records(self, memory_array):
        """写入阶段一生成的记忆中枢词条"""
        if not memory_array: return []
        url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{APP_TOKEN_MEMORY}/tables/{TABLE_MEMORY}/records/batch_create"
        records = []
        for mem in memory_array:
            records.append({"fields": {
                "类别": mem.get("category", "设定"),
                "词条名": mem.get("name", "未命名"),
                "深层设定逻辑": mem.get("lore", ""),
                "视觉氛围与美学隐喻": mem.get("visual_aura", "")
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
                    logger.error(f"Feishu Memory Insert Error: {json.dumps(data, ensure_ascii=False)}")
                    continue
                new_ids = [r.get("record_id") for r in data.get("data", {}).get("records", [])]
                created_ids.extend(new_ids)
                logger.info(f"🧠 第 {batch_start//batch_size+1} 批记忆词条写入飞书完成 ({len(new_ids)} 条).")
            except Exception as e:
                logger.error(f"Error bulk inserting memory batch: {e}")
        return created_ids

    def get_all_memories(self):
        """读取所有的记忆词条"""
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
                    
                    def flatten(val):
                        if isinstance(val, str): return val
                        if isinstance(val, list): return "".join(seg.get("text", "") if isinstance(seg, dict) else str(seg) for seg in val)
                        return str(val)

                    all_memories.append({
                        "category": flatten(fields.get("类别", "")),
                        "name": flatten(fields.get("词条名", "")),
                        "lore": flatten(fields.get("深层设定逻辑", "")),
                        "visual_aura": flatten(fields.get("视觉氛围与美学隐喻", ""))
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
