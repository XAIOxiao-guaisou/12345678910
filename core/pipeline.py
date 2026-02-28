import asyncio
import logging
import time
import os
import aiohttp
from collections import defaultdict
from core.services.llm_service.deepseek_service import DeepSeekService
from core.services.db_service.feishu_bitable import FeishuBitableManager
from core.services.llm_service.memory_engine import MemoryEngine
from core.services.download_service.aria2c_service import Aria2cService

logger = logging.getLogger(__name__)

class PipelineOrchestrator:
    def __init__(self):
        self.bitable = FeishuBitableManager()
        # novel_id 粒度并发锁：同 小说 内 Stage1 串行，不同小说间真并行
        self.novel_locks: defaultdict = defaultdict(asyncio.Lock)
        # Aria2c 健康检查（异步，启动时非阻塞触发）
        asyncio.get_event_loop().create_task(Aria2cService.init()) if self._loop_running() else None

    @staticmethod
    def _loop_running() -> bool:
        """检查是否在 asyncio 事件循环内（FastAPI 环境下为 True，纯脚本为 False）"""
        try:
            loop = asyncio.get_running_loop()
            return loop is not None
        except RuntimeError:
            return False

    async def process_novel_to_feishu(
        self,
        novel_text: str,
        style_key: str = "anime",
        llm_temperature: float = 0.7,
        top_p: float = 0.9,
        chunk_size: int = 1000,
        video_params: dict = None,
        gateway: str = "wan_2_6",
        sandbox_mode: bool = True,
        novel_id: str = "",
    ):
        """
        v2.6.0: 去除了清空操作，支持 novel_id 隔离。
        Stage1 接入 MemoryEngine.evolve_memory（增量模式）。
        """
        import hashlib
        if video_params is None:
            video_params = {}

        # 生成内容标识（如用户未输入，用内容前 64 字节 hash 兼容旧行为）
        if not novel_id:
            novel_id = f"novel_{hashlib.sha1(novel_text[:200].encode()).hexdigest()[:8]}"
            logger.warning(f"⚠️ novel_id 未指定，自动生成: {novel_id}（建议在 WebUI 配置项目名）")

        logger.info(f"======== 开始小说全自动上云飞书 (novel_id={novel_id}, Style={style_key}, 沙盒={sandbox_mode}) ========")

        # ① 阶段一：记忆进化（增量模式，不再清空）
        logger.info("进入阶段一: 记忆中枢增量建档/演化...")
        try:
            # 检查是否已有现存记忆（新建展开全量流，已有则增量）
            existing = self.bitable.get_memories_by_novel(novel_id)
            if not existing:
                logger.info("🗒️ 首次建档，全量提取记忆中枢...")
                memory_data = DeepSeekService.generate_memory_context(novel_text)
                logger.info(f"✅ 记忆中枢提取完毕，获得 {len(memory_data)} 条设定。")
                self.bitable.insert_memory_records(memory_data, novel_id=novel_id)
            else:
                logger.info(f"🔄 已有 {len(existing)} 条现存记忆，由 MemoryEngine 处理增量演化")
                await MemoryEngine.evolve_memory(
                    novel_id=novel_id,
                    chapter_text=novel_text,
                    chapter_name="单文件输入",
                    bitable=self.bitable,
                )
        except Exception as e:
            logger.error(f"❌ 阶段一严重错误: {e}")
            return {"status": "error", "message": str(e)}

        # ② 获取记忆（自动过滤废弃词条，节省 DeepSeek Token）
        all_memories_dict = self.bitable.get_memories_by_novel(novel_id)
        all_memories = [
            v["fields"] for v in all_memories_dict.values()
            if v["fields"].get("status") != "废弃"
        ]

        # ③ 阶段二：分镜生成
        chunks = DeepSeekService.smart_chunk_text(novel_text, chunk_size)
        logger.info(f"📖 小说共 {len(novel_text)} 字，切分 {len(chunks)} 块执行拆解...")
        all_scenes = []

        for idx, chunk in enumerate(chunks):
            logger.info(f"🧠 [正在分析 {idx+1}/{len(chunks)} 块...]")
            relevant_memories = []
            for mem in all_memories:
                name = mem.get("name", "")
                if name and name in chunk:
                    relevant_memories.append(mem)
                elif mem.get("category", "") in ["世界观", "氛围", "基调"]:
                    relevant_memories.append(mem)

            memory_context_str = "【全局世界观与当前段落相关的记忆词条】\n"
            for rm in relevant_memories:
                memory_context_str += (
                    f"- [{rm.get('category', '设定')}] {rm.get('name', '')}:"
                    f"{rm.get('lore', '')}\n  视觉隐喻：{rm.get('visual_aura', '')}\n"
                )

            scenes = DeepSeekService.generate_scenes_for_chunk(chunk, memory_context_str)
            if scenes:
                all_scenes.extend(scenes)
                logger.info(f"✅ 第{idx+1}块完成，累积分镜: {len(all_scenes)} 个")
            time.sleep(1)

        if not all_scenes:
            logger.error("❌ 所有片段解析失败，退出。")
            return {"status": "error", "message": "两阶段管线解析失败"}

        for idx, scene in enumerate(all_scenes):
            scene["_episode"] = idx + 1

        # ④ 回填飞书
        logger.info("云端写入剧本拆解（两阶段生成）...")
        from core.protocols.render_protocol import RenderProtocol
        for scene in all_scenes:
            prompt_raw = scene.get("master_prompt", scene.get("visual_prompt", ""))
            if isinstance(prompt_raw, dict):
                prompt_raw = "\n".join(f"{k}: {v}" for k, v in prompt_raw.items())
            scene["visual_prompt"] = RenderProtocol.inject_render_config(
                str(prompt_raw), video_params, gateway
            ) if not sandbox_mode else RenderProtocol.inject_render_config(
                str(prompt_raw), {**video_params, "_sandbox_mode": True}, gateway
            )

        inserted_ids = self.bitable.insert_new_parsed_scenes(all_scenes, 1, gateway=gateway)
        prompts = [
            scene.get("visual_prompt", "")
            for scene in all_scenes if scene.get("visual_prompt")
        ]

        logger.info("🎉 小说拆解上云已圆满结束，AI 自动化正在托管！")
        return {
            "status": "success",
            "novel_id": novel_id,
            "chunks": len(chunks),
            "scenes": len(all_scenes),
            "inserted": len(inserted_ids),
            "prompts": prompts
        }

    async def process_files_batch(
        self,
        files: list,
        novel_id: str,
        style_key: str = "anime",
        gateway: str = "wan_2_6",
        video_params: dict = None,
        sandbox_mode: bool = True,
        chunk_size: int = 1000,
        on_progress=None,
    ):
        """
        v2.6.0 批量文件处理入口。
        files: [{"name": str, "content": str}, ...]（已按章节顺序排列）
        实现"同书串行、分镜并发、断点续传"三大特性。
        """
        if video_params is None:
            video_params = {}

        logger.info(f"📚 [批量流水线] 开始: novel_id={novel_id}, 文件数={len(files)}")

        # === Stage1: 同 novel_id 内串行（章节相互依赖）===
        all_scenes = []
        for chapter_idx, file_info in enumerate(files):
            chapter_name = file_info.get("name", f"chapter_{chapter_idx+1}")
            chapter_text = file_info.get("content", "")

            # 断点续传检查
            if self.bitable.check_chapter_stage1_done(novel_id, chapter_name):
                logger.info(f"⏭️ [断点续传] {chapter_name} 已完成 Stage1，跳过")
                if on_progress:
                    await on_progress("stage1", chapter_idx + 1, len(files), chapter_name, "跳过")
                continue

            # novel_id 粒度锁：同一小说的章节必须按序处理
            async with self.novel_locks[novel_id]:
                result = await MemoryEngine.evolve_memory(
                    novel_id=novel_id,
                    chapter_text=chapter_text,
                    chapter_name=chapter_name,
                    bitable=self.bitable,
                    chapter_index=chapter_idx,
                )
                if result["status"] not in ("success", "partial_error"):
                    logger.error(f"❌ Stage1 失败: {chapter_name} -> {result.get('message')}")
                if on_progress:
                    await on_progress("stage1", chapter_idx + 1, len(files), chapter_name, result["status"])

        # === Stage2: Semaphore(3) 并发分镜 ===
        all_memories_dict = self.bitable.get_memories_by_novel(novel_id)
        active_memories = [
            v["fields"] for v in all_memories_dict.values()
            if v["fields"].get("status") != "废弃"
        ]

        sem = asyncio.Semaphore(3)
        all_chunks = []
        for file_info in files:
            chunks = DeepSeekService.smart_chunk_text(file_info.get("content", ""), chunk_size)
            chapter_name = file_info.get("name", "")
            all_chunks.extend([(c, chapter_name) for c in chunks])

        async def _stage2_chunk(chunk_text, chapter_name, chunk_idx):
            async with sem:
                relevant = []
                for mem in active_memories:
                    name = mem.get("name", "")
                    if name and name in chunk_text:
                        relevant.append(mem)
                    elif mem.get("category", "") in ["世界观", "氛围", "基调"]:
                        relevant.append(mem)
                mem_ctx = "【全局世界观与当前段落相关的记忆词条】\n"
                for rm in relevant:
                    mem_ctx += f"- [{rm.get('category', '设定')}] {rm.get('name', '')}: {rm.get('lore', '')}\n"
                return await asyncio.to_thread(
                    DeepSeekService.generate_scenes_for_chunk, chunk_text, mem_ctx
                )

        logger.info(f"🎬 [Stage2] 并发生成分镜，共 {len(all_chunks)} 块，并发度=3")
        results = await asyncio.gather(
            *[_stage2_chunk(c, cn, i) for i, (c, cn) in enumerate(all_chunks)],
            return_exceptions=True
        )

        for i, res in enumerate(results):
            if isinstance(res, list) and res:
                all_scenes.extend(res)
            elif isinstance(res, Exception):
                logger.error(f"[Stage2] 块 {i} 异常: {res}")

        if not all_scenes:
            return {"status": "error", "message": "所有分批块均未返回分镜", "novel_id": novel_id}

        for idx, scene in enumerate(all_scenes):
            scene["_episode"] = idx + 1

        inserted_ids = self.bitable.insert_new_parsed_scenes(all_scenes, 1, gateway=gateway)
        logger.info(f"🎉 批量流水线完成: novel_id={novel_id}, 分镜={len(all_scenes)}, 写入={len(inserted_ids)}")
        return {
            "status": "success",
            "novel_id": novel_id,
            "files": len(files),
            "scenes": len(all_scenes),
            "inserted": len(inserted_ids),
        }
    
    async def run_video_generation(self, account: str, prompts: list, gateway: str = "seedance-1.5-pro"):
        """
        Since we moved away from Playwright and to API-driven interfaces,
        this will use the configured Model API backend based on 'gateway'.
        """
        logger.info(f"=== 开始 API 驱动视频生成流程 (Account: {account}, 模型: {gateway}) ===")
        # Dynamically load the correct class
        if "seedance" in gateway.lower() or "volcengine" in gateway.lower() or gateway == "API_MODE":
            from core.services.video_service.volcengine_service import VolcengineVideoAPI
            try:
                # In real prod this key should be an env var
                api_key = os.environ.get("VOLCENGINE_API_KEY", "693c67a0-2b84-4e7c-afcd-7a2fb8f0134c")
                video_api = VolcengineVideoAPI(api_key=api_key)
            except Exception as e:
                logger.error(f"无法初始化火山引擎 API 客户端: {e}")
                return
        elif "wan2.6" in gateway.lower() or "aliyun" in gateway.lower():
            from core.services.video_service.aliyun_service import Wan2_6VideoAPI
            try:
                # API Key will be read from OS env by default inside the class
                video_api = Wan2_6VideoAPI(model=gateway)
            except Exception as e:
                logger.error(f"无法初始化阿里百炼 API 客户端: {e}")
                return
        else:
            logger.error(f"不支持的网关模型类型: {gateway}")
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

                        # P2: 下载逻辑 — 优先 Aria2c（断点续传），降级 aiohttp
                        download_dir = os.path.abspath(
                            os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "Download")
                        )
                        # novel_id 通过任务闭包传入（如未传则为空）
                        _novel_id = getattr(self, "_current_novel_id", "")
                        try:
                            local_path = await Aria2cService.smart_download(
                                url=video_url,
                                novel_id=_novel_id,
                                episode_num=idx,
                                download_dir=download_dir,
                            )
                            logger.info(f"[Task {idx}] ✅ 视频已下载至本地: {local_path}")
                            return local_path
                        except Exception as dl_err:
                            logger.error(f"[Task {idx}] 下载全部失败: {dl_err}")
                            return video_url  # 最终降级：返回远端 URL 供人工处理
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
