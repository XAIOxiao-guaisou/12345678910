import time
import requests
import logging
import json
import os

logger = logging.getLogger(__name__)

from core.config import settings

# ---------------------------------------------------------
# Feishu API Constants
# ---------------------------------------------------------
APP_ID = settings.FEISHU_APP_ID
APP_SECRET = settings.FEISHU_APP_SECRET

# The Assets (角色风格库)
APP_TOKEN_ASSETS = settings.FEISHU_APP_TOKEN_ASSETS
TABLE_ASSETS = settings.FEISHU_TABLE_ASSETS

# The Factory (素材生成表)
APP_TOKEN_FACTORY = settings.FEISHU_APP_TOKEN_FACTORY
TABLE_FACTORY = settings.FEISHU_TABLE_FACTORY

# The Brain (剧本拆解表)
APP_TOKEN_SCRIPT = settings.FEISHU_APP_TOKEN_SCRIPT
TABLE_SCRIPT = settings.FEISHU_TABLE_SCRIPT

# The Memory Hub (记忆中枢表)
APP_TOKEN_MEMORY = settings.FEISHU_APP_TOKEN_MEMORY
TABLE_MEMORY = settings.FEISHU_TABLE_MEMORY

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
        search_url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records/search"
        batch_delete_url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records/batch_delete"

        try:
            all_ids = []
            page_token = None
            # 1. 收集所有记录 ID
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

            logger.info(f"🧹 [{label}] 查找到 {total} 条记录，准备批量删除...")

            # 2. 按 500 条一批进行批量删除 (安全且极速)
            deleted = 0
            batch_size = 500
            for i in range(0, total, batch_size):
                batch_ids = all_ids[i:i + batch_size]
                payload = {"records": batch_ids}
                r = requests.post(batch_delete_url, headers=self._get_headers(), json=payload)
                if r.ok and r.json().get("code") == 0:
                    deleted += len(batch_ids)
                else:
                    logger.error(f"批量删除失败: {r.text}")

            logger.info(f"🧹 [{label}] 成功删除 {deleted} 条记录。")
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
                "状态": ["待生成"],
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
                resp = getattr(e, 'response', None)
                if resp is not None:
                    err_msg += f" | Response: {getattr(resp, 'text', '')}"
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

    def update_record(self, record_id: str, fields: dict):
        url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{APP_TOKEN_SCRIPT}/tables/{TABLE_SCRIPT}/records/{record_id}"
        payload = {"fields": fields}
        try:
            resp = requests.put(url, headers=self._get_headers(), json=payload)
            resp.raise_for_status()
            data = resp.json()
            if data.get("code") == 0:
                return True
            else:
                logger.error(f"更新记录失败: {data}")
                return False
        except Exception as e:
            logger.error(f"更新记录发生异常: {e}")
            return False

    def get_pending_tasks(self):
        url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{APP_TOKEN_SCRIPT}/tables/{TABLE_SCRIPT}/records/search"
        payload = {
            "filter": {
                "conjunction": "and",
                "conditions": [
                    {
                        "field_name": "状态",
                        "operator": "contains",
                        "value": ["待生成"]
                    }
                ]
            }
        }
        try:
            resp = requests.post(url, headers=self._get_headers(), json=payload)
            resp.raise_for_status()
            data = resp.json()
            if data.get("code") != 0:
                logger.error(f"获取待生成任务失败: {data}")
                return []
            
            items = data.get("data", {}).get("items", [])
            tasks = []
            for item in items:
                fields = item.get("fields", {})
                
                # Helper to unpack lists or single items
                def unpack(v):
                    if isinstance(v, list) and len(v) > 0:
                        if isinstance(v[0], dict):
                            return v[0].get("text", "")
                        return v[0]
                    return v

                tasks.append({
                    "record_id": item.get("record_id"),
                    "visual_prompt": unpack(fields.get("视觉提示词", "")),
                    # The Gateway field can be specified dynamically later; assuming "seedance-1.5-pro" temporarily
                    "video_model": unpack(fields.get("网关模型", "seedance-1.5-pro")) 
                })
            return tasks
        except Exception as e:
            logger.error(f"获取待生成任务异常: {e}")
            return []

    def get_processing_tasks(self):
        url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{APP_TOKEN_SCRIPT}/tables/{TABLE_SCRIPT}/records/search"
        payload = {
            "filter": {
                "conjunction": "and",
                "conditions": [{"field_name": "状态", "operator": "contains", "value": ["生成中"]}]
            }
        }
        try:
            resp = requests.post(url, headers=self._get_headers(), json=payload)
            resp.raise_for_status()
            data = resp.json().get("data", {})
            return [r.get("record_id") for r in data.get("items", [])]
        except Exception:
            return []

    def reset_zombie_tasks(self):
        zombie_ids = self.get_processing_tasks()
        if not zombie_ids:
            return
        logger.info(f"发现 {len(zombie_ids)} 个僵尸任务，正在重置为待生成...")
        for rid in zombie_ids:
            self.update_record(rid, {"状态": ["待生成"], "异常日志": "进程意外中断，状态已自愈重置"})

    def upload_media(self, file_path: str, parent_type="bitable_file", parent_node=""):
        if not os.path.exists(file_path):
            logger.error(f"文件不存在: {file_path}")
            return None
            
        file_size = os.path.getsize(file_path)
        file_name = os.path.basename(file_path)
        
        # Determine upload method based on size (20MB threshold)
        if file_size < 20 * 1024 * 1024:
            return self._upload_all(file_path, file_name, file_size, parent_type, parent_node)
        else:
            return self._upload_chunked(file_path, file_name, file_size, parent_type, parent_node)

    def _upload_all(self, file_path, file_name, file_size, parent_type, parent_node):
        url = "https://open.feishu.cn/open-apis/drive/v1/medias/upload_all"
        headers = {"Authorization": f"Bearer {self._get_token()}"}
        
        data = {
            "file_name": file_name,
            "parent_type": parent_type,
            "parent_node": parent_node or APP_TOKEN_SCRIPT,
            "size": str(file_size)
        }
        
        try:
            with open(file_path, "rb") as f:
                files = {"file": (file_name, f)}
                resp = requests.post(url, headers=headers, data=data, files=files)
                resp.raise_for_status()
                res_data = resp.json()
                if res_data.get("code") == 0:
                    return res_data.get("data", {}).get("file_token")
                else:
                    logger.error(f"普通上传失败: {res_data}")
                    return None
        except Exception as e:
            logger.error(f"上传异常: {e}")
            return None

    def _upload_chunked(self, file_path, file_name, file_size, parent_type, parent_node):
        headers = {"Authorization": f"Bearer {self._get_token()}", "Content-Type": "application/json"}
        parent_node = parent_node or APP_TOKEN_SCRIPT
        
        # 1. Prepare
        prepare_url = "https://open.feishu.cn/open-apis/drive/v1/medias/upload_prepare"
        payload = {
            "file_name": file_name,
            "parent_type": parent_type,
            "parent_node": parent_node,
            "size": file_size
        }
        resp = requests.post(prepare_url, headers=headers, json=payload)
        res_data = resp.json()
        if res_data.get("code") != 0:
            logger.error(f"分片上传准备失败: {res_data}")
            return None
        
        upload_id = res_data.get("data", {}).get("upload_id")
        # STRICT 4MB Rule imposed by Feishu
        block_size = res_data.get("data", {}).get("block_size", 4194304) 
        if block_size != 4194304:
            logger.warning(f"Feishu requested a block size of {block_size}, but applying standard 4MB chunk limit.")
            block_size = 4194304
        
        # 2. Upload parts
        part_url = "https://open.feishu.cn/open-apis/drive/v1/medias/upload_part"
        part_headers = {"Authorization": f"Bearer {self._get_token()}"}
        
        try:
            with open(file_path, "rb") as f:
                seq = 0
                while True:
                    chunk = f.read(block_size)
                    if not chunk:
                        break
                    
                    data = {
                        "upload_id": upload_id,
                        "seq": str(seq),
                        "size": str(len(chunk))
                    }
                    files = {"file": chunk}
                    part_resp = requests.post(part_url, headers=part_headers, data=data, files=files)
                    part_res_data = part_resp.json()
                    if part_res_data.get("code") != 0:
                        logger.error(f"分片上传块 {seq} 失败: {part_res_data}")
                        return None
                    seq += 1
                    
            # 3. Finish
            finish_url = "https://open.feishu.cn/open-apis/drive/v1/medias/upload_finish"
            finish_payload = {
                "upload_id": upload_id,
                "block_num": seq
            }
            finish_resp = requests.post(finish_url, headers=headers, json=finish_payload)
            finish_res_data = finish_resp.json()
            if finish_res_data.get("code") == 0:
                return finish_res_data.get("data", {}).get("file_token")
            else:
                logger.error(f"分片上传结束失败: {finish_res_data}")
                return None
                
        except Exception as e:
            logger.error(f"分片上传异常: {e}")
            return None

    def upload_attachment_and_update_record(self, record_id: str, file_path: str):
        file_token = self.upload_media(file_path)
        if file_token:
            # 飞书多维表格附件字段格式是一组由 file_token 构成的对象数组
            fields = {
                "视频文件": [{"file_token": file_token}],
                "状态": "已完成",
                "异常日志": "" # 清空报错
            }
            return self.update_record(record_id, fields)
        return False
