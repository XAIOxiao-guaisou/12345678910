# ai自动化/core/deepseek_feishu_integration.py
import time
import requests
import logging
import json
import re
import os

logger = logging.getLogger(__name__)

# ---------------------------------------------------------
# Feishu API Constants
# ---------------------------------------------------------
APP_ID = "cli_a914c526d5f8dbc6"
APP_SECRET = "S9cKhqZV9EoL28V4l6mUVhEmm0OThBEa"

# The Assets (角色风格库)
APP_TOKEN_ASSETS = "Oj00bIhGVaq1cNsZsJhcMC58ndd"
TABLE_ASSETS = "tblC6L0fO7FXP3fI"

# The Factory (素材生成表)
APP_TOKEN_FACTORY = "Cu75bLeuJarqg1s7ysscaNolnPg"
TABLE_FACTORY = "tbloUrdwqG47ZmgI"

# The Brain (剧本拆解表)
APP_TOKEN_SCRIPT = "J7OPbwEHqaJMefs1NLecTvA1n2e"
TABLE_SCRIPT = "tbluFmGLkmqPTd9S"

# ---------------------------------------------------------
# DeepSeek API Logic
# ---------------------------------------------------------
FREE_PROXY_ENDPOINTS = [
    {
        "base_url": "https://api.airforce/v1",
        "api_key": "null",
        "model_map": {"deepseek-chat": "deepseek-v3"},
        "name": "Airforce (1000次/天免登录)"
    },
    {
        "base_url": "https://api.llm7.io/v1",
        "api_key": "null",
        "model_map": {"deepseek-chat": "deepseek/deepseek-chat-v3-0324"},
        "name": "LLM7 (高频无需注册)"
    },
    {
        "base_url": "https://fresedgpt.space/v1",
        "api_key": "null",
        "model_map": {"deepseek-chat": "deepseek-v3"},
        "name": "FresedGPT (每日免费)"
    },
]

SILICONFLOW_KEY = os.environ.get("SILICONFLOW_API_KEY", "")
if SILICONFLOW_KEY:
    FREE_PROXY_ENDPOINTS.insert(0, {
        "base_url": "https://api.siliconflow.cn/v1",
        "api_key": SILICONFLOW_KEY,
        "model_map": {"deepseek-chat": "deepseek-ai/DeepSeek-V3"},
        "name": "SiliconFlow (国内优先)"
    })

DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
if DEEPSEEK_API_KEY:
    FREE_PROXY_ENDPOINTS.append({
        "base_url": "https://api.deepseek.com/v1",
        "api_key": DEEPSEEK_API_KEY,
        "model_map": {"deepseek-chat": "deepseek-chat"},
        "name": "DeepSeek Official (付费保底)"
    })

# ---------------------------------------------------------
# Style Presets
# ---------------------------------------------------------
STYLE_PRESETS = {
    "realistic": {
        "name": "电影感真人风格",
        "visual_style": "High-end cinematic, photorealistic, shot on Hasselblad H6D, 80mm, f/2.8, Rembrandt lighting, 8k resolution, quiet luxury texture, real-world physics.",
        "negative_prompt": "anime, cartoon, illustration, drawing, 2D, fake, plastic texture."
    },
    "anime": {
        "name": "高品质动漫风格",
         "visual_style": "High-quality Anime style, Makoto Shinkai style, extreme details, 8k resolution, vibrant colors, expressive character movements, highly coherent scenes, NO photorealism.",
        "negative_prompt": "photorealistic, real person, 3D render, noisy texture, blurry background."
    }
}

def call_openai_compatible_api(base_url: str, api_key: str, model: str, message: str, timeout: int = 150):
    url = f"{base_url.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "你是一位专业的顶级短剧编剧。"},
            {"role": "user", "content": message}
        ],
        "max_tokens": 4096,
        "temperature": 0.7
    }
    resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    content = data["choices"][0]["message"]["content"]
    if not content or len(content) < 5:
        raise ValueError("响应内容过短或为空")
    return content

def call_deepseek(message: str):
    """依次尝试各个免费反向代理调用 DeepSeek"""
    for proxy in FREE_PROXY_ENDPOINTS:
        proxy_model = proxy["model_map"].get("deepseek-chat", "deepseek-v3")
        logger.info(f"🔁 尝试公共服务: [{proxy['name']}] model={proxy_model}")
        try:
            content = call_openai_compatible_api(
                base_url=proxy["base_url"],
                api_key=proxy["api_key"],
                model=proxy_model,
                message=message,
                timeout=120
            )
            return content
        except Exception as e:
            logger.warning(f"⚠️ [{proxy['name']}] 异常: {e}，切换下一个...")
        time.sleep(1)
        
    raise Exception("❌ 所有 DeepSeek 公共产出节点均失效，请稍后再试或配置 SILICONFLOW_API_KEY。")

def extract_json_from_deepseek(text):
    if not text: return None
    text = text.strip()
    
    def try_parse(s):
        try: return json.loads(s)
        except: return None

    # Code block format
    match = re.search(r'```(?:json)?\s*([\[\{].*?[\]\}])\s*```', text, re.DOTALL)
    if match:
        res = try_parse(match.group(1))
        if res: return res

    # Raw search
    match = re.search(r'([\[\{].*)', text, re.DOTALL)
    if not match: return None
    raw_json = match.group(1).strip()
    res = try_parse(raw_json)
    if res: return res
    
    # Repair missing bracket
    if raw_json.startswith('['):
        if not raw_json.endswith(']'):
            last_brace = raw_json.rfind('}')
            if last_brace != -1:
                repaired = raw_json[:last_brace+1] + ']'
                res = try_parse(repaired)
                if res:
                    logger.warning(f"✅ 修复了被截断的 JSON，成功挽救 {len(res)} 条分镜。")
                    return res
    return None

def smart_chunk_text(text, chunk_size=1200):
    text = text.strip()
    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        if end >= len(text):
            chunk = text[start:].strip()
            if chunk: chunks.append(chunk)
            break
        for sep in ['\n\n', '\n', '。', '！', '？']:
            split_at = text.rfind(sep, start, end)
            if split_at > start:
                end = split_at + len(sep)
                break
        chunk = text[start:end].strip()
        if chunk: chunks.append(chunk)
        start = end
    return chunks

# ---------------------------------------------------------
# Feishu Bitable Logic
# ---------------------------------------------------------
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

    def insert_new_parsed_scenes(self, scenes_array, episode_start=1):
        url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{APP_TOKEN_SCRIPT}/tables/{TABLE_SCRIPT}/records/batch_create"
        records = []
        for i, scene in enumerate(scenes_array):
            # 将新的 JSON 结构映射到飞书的字段中
            content_desc = scene.get("summary", "")
            if "visual_logic" in scene:
                logic = scene["visual_logic"]
                visual_logic_text = f"【0-5s】{logic.get('shot_1_0_5s', '')}\n【5-10s】{logic.get('shot_2_5_10s', '')}\n【10-15s】{logic.get('shot_3_10_15s', '')}"
            else:
                visual_logic_text = scene.get("scene_desc", "")
                
            visual_prompt = scene.get("master_prompt", scene.get("visual_prompt", ""))
            audio_prompt = scene.get("audio_plan", scene.get("audio_prompt", ""))
            
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


# ---------------------------------------------------------
# High-Level Extraction Workflow
# ---------------------------------------------------------
def extract_characters(text):
    sample = text[:2000]
    prompt = f"""
分析以下小说文本，提取主要角色信息。只返回纯JSON数组：
[
  {{ "name": "角色姓名", "gender": "性别", "appearance": "外貌特征30字内" }}
]
小说原文：
{sample}
"""
    logger.info("DeepSeek 提取角色信息...")
    result_text = call_deepseek(prompt)
    parsed = extract_json_from_deepseek(result_text)
    return parsed if (isinstance(parsed, list) and len(parsed) > 0 and "name" in parsed[0]) else []

def generate_scenes_for_chunk(chunk_text, style_ref, style_key="anime"):
    preset = STYLE_PRESETS.get(style_key, STYLE_PRESETS["anime"])

    prompt = f"""# Role
你是一名资深的 AI 短剧导演，擅长将长篇小说转化为高审美、叙事连贯的 15s 短剧脚本。

# Goals
将输入的【小说文本】拆解为一系列连贯的 15s 视频指令。
确保同一章节内的视觉风格极其连贯！必须确保动作的起承转合自然：{preset['name']}。

# Constraints
1. **时长逻辑**：每个 Scene 预估 15s。要求将每个 Scene 拆解为 3 个连续的【子镜头】。
2. **角色一致性**：
   - [人类]：需固定特征（如：{style_ref}）。
   - [非人实体/动物/怪兽]：需明确物种特征。
3. **风格要求**：{preset['visual_style']}
4. **负面约束**：{preset['negative_prompt']}
5. **输出格式**：严格返回纯 JSON 数组，不带 markdown 代码块标记，用于外部系统解析。

# Global Visual Style
- Camera: Shot on Hasselblad H6D, 80mm, f/2.8.
- Aesthetics: {preset['visual_style']}

# Input Text
\"\"\"
{chunk_text}
\"\"\"

# Output Format (JSON Array ONLY)
[
  {{
    "scene_num": "01",
    "summary": "简述剧情逻辑",
    "visual_logic": {{
      "shot_1_0_5s": "起幅：[环境描述 + 角色入场方式]",
      "shot_2_5_10s": "推移：[核心动作 + 情绪变化]",
      "shot_3_10_15s": "落幅：[定格或转场暗示]"
    }},
    "entities": [
      {{"name": "角色A", "type": "怪兽/人类", "visual_anchor": "固定特征描述"}}
    ],
    "master_prompt": "{preset['visual_style']}, [合并上方子镜头核心画面的英文描述, 强调动作和环境的连续性], --ar 16:9",
    "audio_plan": "背景音：[具体描述]; 旁白内容：[文字]"
  }}
]
"""
    for _ in range(3):
        try:
            result_text = call_deepseek(prompt)
            logger.info(f"DeepSeek Raw Output (Length: {len(result_text)}):\n{result_text[:500]}...")
            parsed = extract_json_from_deepseek(result_text)
            if isinstance(parsed, list) and len(parsed) > 0 and ("master_prompt" in parsed[0] or "visual_prompt" in parsed[0]):
                return parsed
            else:
                logger.warning(f"Failed to parse or missing keys. Parsed type: {type(parsed)}")
        except Exception as e:
            logger.error(f"Error calling deepseek: {e}")
            pass
        time.sleep(2)
    return []

def process_novel_to_feishu(novel_text: str, style_key: str = "anime"):
    logger.info(f"======== 开始小说全自动上云飞书 (DeepSeek, Style: {style_key}) ========")
    bitable = FeishuBitableManager()
    
    # 1. 彻底清空三张表
    logger.info("清理历史积累表数据...")
    bitable.purge_all_records()
    bitable.purge_factory_records()
    bitable.purge_assets_records()
    
    # 2. 提取角色
    characters = extract_characters(novel_text)
    main_char_name = "主角"
    main_char_appearance = ""
    if characters:
        c = characters[0]
        main_char_name = c["name"]
        main_char_appearance = c.get("appearance", "")
        for char in characters:
            bitable.upsert_character_in_assets(char["name"], char.get("appearance", ""))
    
    style_ref = bitable.get_style_reference(main_char_name)
    if not style_ref or "Hasselblad" not in style_ref:
        style_ref = f"Hasselblad H6D-100c, 80mm, f/2.8. {main_char_appearance}"

    # 3. 分块拆解
    chunks = smart_chunk_text(novel_text, 1200)
    logger.info(f"📖 小说共 {len(novel_text)} 字，切分 {len(chunks)} 块执行拆解...")
    all_scenes = []
    
    for idx, chunk in enumerate(chunks):
        logger.info(f"🧠 [正在分析 {idx+1}/{len(chunks)} 块...]")
        scenes = generate_scenes_for_chunk(chunk, style_ref, style_key=style_key)
        if scenes:
             all_scenes.extend(scenes)
             logger.info(f"✅ 第{idx+1}块完成，累积分镜: {len(all_scenes)} 个")
        time.sleep(1)
        
    if not all_scenes:
        logger.error("❌ 所有片段解析失败，退出。")
        return {"status": "error", "message": "解析失败"}
        
    for idx, scene in enumerate(all_scenes):
        scene["_episode"] = idx + 1
        
    # 4. 回填飞书
    logger.info("云端写入剧本拆解及素材库...")
    inserted_ids = bitable.insert_new_parsed_scenes(all_scenes, 1)
    bitable.create_factory_stubs(inserted_ids, character=main_char_name)

    prompts = [scene.get("master_prompt", scene.get("visual_prompt", "")) for scene in all_scenes if scene.get("master_prompt") or scene.get("visual_prompt")]

    logger.info("🎉 小说拆解上云已圆满结束，AI 自动化正在托管！")
    return {
        "status": "success",
        "chunks": len(chunks),
        "scenes": len(all_scenes),
        "inserted": len(inserted_ids),
        "prompts": prompts
    }
