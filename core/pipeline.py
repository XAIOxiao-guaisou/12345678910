import asyncio
import logging
from core.services.llm_service.deepseek_service import DeepSeekService
from core.services.db_service.feishu_bitable import FeishuBitableManager
import time
import os

logger = logging.getLogger(__name__)

class PipelineOrchestrator:
    def __init__(self):
        self.bitable = FeishuBitableManager()

    def process_novel_to_feishu(self, novel_text: str, style_key: str = "anime", llm_temperature: float = 0.7, top_p: float = 1.0, chunk_size: int = 1200, video_params: dict = None):
        logger.info(f"======== 开始小说全自动上云飞书 (DeepSeek, Style: {style_key}) ========")
        
        # 1. 彻底清空工作表
        logger.info("清理历史积累表数据...")
        self.bitable.purge_all_records()
        self.bitable.purge_factory_records()
        self.bitable.purge_memory_records()
        
        # 2. 阶段一：记忆灌注与空间构建 (建档)
        logger.info("进入两阶段架构：阶段一 (构建记忆中枢)...")
        try:
            memory_data = DeepSeekService.generate_memory_context(novel_text)
            logger.info(f"✅ 记忆中枢提取完毕，获得 {len(memory_data)} 条设定。")
            self.bitable.insert_memory_records(memory_data)
        except Exception as e:
            logger.error(f"❌ 阶段一严重错误: {e}")
            return {"status": "error", "message": str(e)}

        # Fetch all memories (simulate real decoupling)
        all_memories = self.bitable.get_all_memories()

        # 2.5 提取 Schema
        from core.config import settings
        table_schema = self.bitable.get_table_schema(settings.FEISHU_APP_TOKEN_SCRIPT, settings.FEISHU_TABLE_SCRIPT)
        schema_context_str = f"【Bitable 写入目标表字段 Schema】: {table_schema}"

        # 3. 阶段二：自主执导与分镜 (拆解)
        chunks = DeepSeekService.smart_chunk_text(novel_text, chunk_size)
        logger.info(f"📖 小说共 {len(novel_text)} 字，切分 {len(chunks)} 块执行拆解...")
        all_scenes = []
        
        temporal_summary_memory = ""
        temporal_visual_memory = ""
        
        for idx, chunk in enumerate(chunks):
            logger.info(f"🧠 [正在分析 {idx+1}/{len(chunks)} 块...]")
            
            # 关键词匹配机制：提取当前 chunk_text 中出现过的记忆词条（包含代名关联匹配）
            relevant_memories = []
            for mem in all_memories:
                name = mem.get("name", "")
                aliases = mem.get("aliases", [])
                cat = mem.get("category", "")
                # 如果是宏观的世界观/氛围设定，或者【核心角色/角色】，强制带入（全局注入）
                if cat in ["世界观", "氛围", "基调", "角色", "核心角色", "主要角色", "核心地标"]:
                    relevant_memories.append(mem)
                # 否则要求名字或代称在文本中明确出现
                elif name and name in chunk:
                    relevant_memories.append(mem)
                elif any(alias in chunk for alias in aliases if alias):
                    relevant_memories.append(mem)
            
            # 构建记忆文段
            memory_context_str = f"{schema_context_str}\n\n【全局世界观与当前段落高度相关的记忆资产库（实体预扫描）】\n"
            for rm in relevant_memories:
                alias_str = f"（代称/别名：{', '.join(rm.get('aliases', []))}）" if rm.get('aliases') else ""
                memory_context_str += f"- [实体: {rm.get('entity_id', '未注册')}] [{rm.get('category', '设定')}] {rm.get('name', '')} {alias_str}：\n  物理与运转规律：{rm.get('lore', '')}\n  视觉约束：{rm.get('visual_constraints', '')}\n  实体依赖：{rm.get('dependencies', [])}\n\n"

            if temporal_summary_memory or temporal_visual_memory:
                memory_context_str += "\n【短期连续性记忆（承接上文）】\n"
                if temporal_summary_memory:
                    memory_context_str += f"- 前情提要: {temporal_summary_memory}\n"
                if temporal_visual_memory:
                    memory_context_str += f"- 上一首分镜视觉: {temporal_visual_memory}\n"

            scenes = DeepSeekService.generate_scenes_for_chunk(
                chunk, 
                memory_context_str,
                temperature=llm_temperature,
                top_p=top_p
            )
            if scenes:
                 all_scenes.extend(scenes)
                 logger.info(f"✅ 第{idx+1}块完成，累积分镜: {len(all_scenes)} 个")
                 last_scene = scenes[-1]
                 temporal_summary_memory = last_scene.get("summary", "")
                 temporal_visual_memory = last_scene.get("visual_prompt", "")
            time.sleep(1)
            
        if not all_scenes:
            logger.error("❌ 所有片段解析失败，退出。")
            return {"status": "error", "message": "两阶段管线解析失败"}
            
        for idx, scene in enumerate(all_scenes):
            scene["_episode"] = idx + 1
            if video_params:
                import json
                config_str = f"\n\n[RENDER_CONFIG]\n{json.dumps(video_params, ensure_ascii=False)}\n[/RENDER_CONFIG]"
                if scene.get("master_prompt"):
                    scene["master_prompt"] += config_str
                elif scene.get("visual_prompt"):
                    scene["visual_prompt"] += config_str
                else:
                    scene["visual_prompt"] = config_str
            
        # 4. 回填飞书
        logger.info("云端写入剧本拆解（两阶段生成）...")
        inserted_ids = self.bitable.insert_new_parsed_scenes(all_scenes, 1)

        # 5. 阶段三：一致性审计与自闭环 (Stage 3)
        logger.info("执行阶段三：全量内容的一致性审计 (Stage 3)...")
        import json
        audit_result = DeepSeekService.consistency_audit(json.dumps(all_scenes, ensure_ascii=False), memory_context_str)
        fixes = audit_result.get("fixes", [])
        if fixes:
            logger.info(f"审计发现 {len(fixes)} 处断层或主观形容词，正在执行自动修正 (Patching Bitable)...")
            if len(inserted_ids) == len(all_scenes):
                scene_num_to_record_id = {scene.get("scene_num", i+1): inserted_ids[i] for i, scene in enumerate(all_scenes)}
                for fix in fixes:
                    s_num = fix.get("scene_num")
                    updated_prompt = fix.get("updated_visual_prompt")
                    reason = fix.get("reason", "")
                    if s_num in scene_num_to_record_id and updated_prompt:
                        rid = scene_num_to_record_id[s_num]
                        logger.info(f"修正场景 {s_num}: {reason}")
                        field_name = "视觉提示词" if "视觉提示词" in table_schema else ("Visual Prompt" if "Visual Prompt" in table_schema else "视频提示词")
                        self.bitable.update_record(rid, {field_name: updated_prompt, "异常日志": f"[Stage 3 审计修正] {reason}"})
            else:
                logger.warning("插入记录数不匹配，跳过精准修正。")
        else:
            logger.info("✅ 审计通过，镜头物理逻辑完备连贯。")

        prompts = [scene.get("master_prompt", scene.get("visual_prompt", "")) for scene in all_scenes if scene.get("master_prompt") or scene.get("visual_prompt")]

        logger.info("🎉 小说拆解上云已圆满结束，AI 自动化正在托管！")
        return {
            "status": "success",
            "chunks": len(chunks),
            "scenes": len(all_scenes),
            "inserted": len(inserted_ids),
            "prompts": prompts
        }
    
    async def run_video_generation(self, account: str, prompts: list, gateway: str = "seedance-1.5-pro"):
        """
        Since we moved away from Playwright and to API-driven interfaces,
        this will use the configured Model API backend based on 'gateway'.
        """
        logger.info(f"=== 开始 API 驱动视频生成流程 (Account: {account}, 模型: {gateway}) ===")
        from core.services.video_service.factory import VideoServiceFactory
        try:
            video_api = VideoServiceFactory.get_service(gateway)
        except Exception as e:
            logger.error(f"无法初始化 API 客户端: {e}")
            return
            
        logger.info(f"收到 {len(prompts)} 个分镜，待接入 API 并行生成...")
        
        # We can fire them all asynchronously
        async def submit_and_wait(prompt_text, idx):
            try:
                task_id = await video_api.submit_task(prompt_text)
                logger.info(f"[Task {idx}] 提交成功，任务 ID: {task_id}")
                
                # Poll loop
                max_retries = 60
                for _ in range(max_retries):
                    await asyncio.sleep(5)
                    status_info = await video_api.check_status(task_id)
                    status = status_info.get("status")
                    
                    if status == "succeeded":
                        video_url = status_info.get("video_url")
                        logger.info(f"[Task {idx}] 生成成功! 视频 URL: {video_url}")
                        
                        # 下载视频到本地 Download 文件夹
                        download_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "Download")
                        if not os.path.exists(download_dir):
                            os.makedirs(download_dir)
                            
                        # Use a zero-padded index for clean sorting (e.g., 01, 02)
                        padded_idx = str(idx).zfill(2)
                        file_name = f"第{padded_idx}集_task_{task_id[:6]}.mp4"
                        file_path = os.path.join(download_dir, file_name)
                        
                        logger.info(f"[Task {idx}] 请求服务层处理下载逻辑...")
                        final_path_or_url = await video_api.download_video(video_url, file_path)
                        if final_path_or_url == file_path:
                            logger.info(f"[Task {idx}] ✅ 视频成功落盘: {file_name}")
                        return final_path_or_url
                    elif status == "failed":
                        err = status_info.get("error")
                        logger.error(f"[Task {idx}] 生成失败: {err}")
                        return None
                    elif status in ["running", "queued"]:
                        logger.info(f"[Task {idx}] 等待生成中... 状态: {status}")
                        continue
                    else:
                        logger.warning(f"[Task {idx}] 未知状态: {status}")
                        return None
                        
                logger.error(f"[Task {idx}] 轮询超时")
                return None
            except Exception as e:
                logger.error(f"[Task {idx}] 发生异常: {e}")
                return None
                
        tasks = [submit_and_wait(p, i+1) for i, p in enumerate(prompts)]
        results = await asyncio.gather(*tasks)
        
        success_count = sum(1 for r in results if r)
        logger.info(f"🎉 视频生成批次结束！成功 {success_count}/{len(prompts)} 个。")
