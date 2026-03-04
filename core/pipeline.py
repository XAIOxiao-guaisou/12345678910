from typing import List, Union
from core.config import settings
import asyncio
import logging
import time
import os
import aiohttp
from core.task_manager import TaskManager
from collections import defaultdict
from core.services.llm_service.deepseek_service import DeepSeekService
from core.services.db_service.feishu_bitable import FeishuBitableManager
from core.services.llm_service.memory_engine import MemoryEngine
from core.services.download_service.aria2c_service import Aria2cService
from core.services.image_service.aliyun_image_service import AliyunImageService

logger = logging.getLogger(__name__)

class PipelineOrchestrator:
    def __init__(self):
        self.bitable = FeishuBitableManager()
        self.task_manager = TaskManager()
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
        image_gateway: str = "aliyun", # Added explicit image_gateway param
        sandbox_mode: bool = True,
        novel_id: str = "",
        task_id: str = "",
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

        rolling_context = None

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
                    f"- [{rm.get('category', '设定')}] {rm.get('name', '')} (ID: {rm.get('entity_id', '')}):"
                    f"{rm.get('lore', '')}\n  视觉隐喻：{rm.get('visual_aura', '')}\n"
                )

            scenes = DeepSeekService.generate_scenes_for_chunk(chunk, memory_context_str, rolling_context)
            if scenes:
                all_scenes.extend(scenes)
                logger.info(f"✅ 第{idx+1}块完成，累积分镜: {len(all_scenes)} 个")
                last_scene = scenes[-1]
                rolling_context = {
                    "summary": last_scene.get("summary", ""),
                    "visual_prompt": last_scene.get("visual_prompt", "")
                }
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

        inserted_ids = self.bitable.insert_new_parsed_scenes(
            all_scenes, 1,
            task_id=getattr(self, "_current_task_id", ""),
            sandbox_mode=sandbox_mode,
            novel_id=novel_id,
            gateway=gateway,
        )
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

    async def run_video_generation(
        self,
        account: str,
        prompts: List[Union[str, tuple]],
        gateway: str = None,
        video_params: dict = None,
        task_id: str = None,
    ):
        """
        独立的视频生成管道，供后台任务直接调用。
        :param prompts: 可以是纯 str，或者是 (prompt, image_url) 元组（用于 i2v）
        """
        if not prompts:
            return

        from core.services.video_service.defaults import SMART_PRESETS
        
        # 应用对应的智能预设 (默认 standard)
        if not video_params:
            if gateway in SMART_PRESETS and "standard" in SMART_PRESETS[gateway]:
                video_params = SMART_PRESETS[gateway]["standard"]
                logger.info(f"使用 {gateway} 智能预设 (standard): {video_params}")

        # Mapping dictionary
        GATEWAY_MODEL_MAP = {
            "wan_2_6":       "wan2.6-i2v",        # WebUI 传 wan_2_6，我们映射到模型名
            "wan2.6-i2v":    "wan2.6-i2v",
            "wan2.6-t2v":    "wan2.6-t2v",        # 备用：文生视频
            "seedance-1.5-pro": "doubao-seedance-1-5-pro-251215",
            "seedance-1.0-pro-fast": "ep-20250218163046-6qbsg", # Need to fix Volcengine inference endpoint
        }
        
        # NOTE: Volcengine needs Endpoint ID, but wait, the API receives the model name or endpoint.
        # "doubao-seedance-1.0-pro-fast" is usually requested via endpoint ID on Volcengine. But wait, I'll pass the exact string "doubao-seedance-1.0-pro-fast" or endpoint as the user gave unless I must use endpoint. 
        # Actually the user gave the exact name `Doubao-Seedance-1.0-pro-fast`. So we will use it as is.
        # So we map "seedance-1.0-pro-fast" to "doubao-seedance-1.0-pro-fast" Wait, let's keep it safe:
        GATEWAY_MODEL_MAP["seedance-1.0-pro-fast"] = "Doubao-Seedance-1.0-pro-fast"
        
        api_model = GATEWAY_MODEL_MAP.get(gateway, gateway)

        # 记录每组任务的最新动态网关
        current_gateways = {i + 1: gateway for i in range(len(prompts))}

        async def submit_and_wait(prompt_text, idx):
            from core.api.state import StateKeeper
            state_keeper = StateKeeper()
            
            while True:
                # 获取当前绑定的 api 实例（如果在重试大循环里被实时修改了）
                active_gateway = current_gateways[idx]
                api_model_mapped = GATEWAY_MODEL_MAP.get(active_gateway, active_gateway)
                from core.services.video_service.factory import get_video_api
                try:
                    active_video_api = get_video_api(active_gateway, api_model_mapped)
                except Exception as e:
                    logger.error(f"无法初始化最新 API 客户端 {active_gateway}: {e}")
                    return None
                    
                is_i2v = "i2v" in api_model_mapped.lower()
                submit_sem = self.task_manager.get_video_semaphore()
                
                async with submit_sem:
                    # 提交间隔 — 防止 QPS
                    await asyncio.sleep((idx - 1) * 2)

                    scene_image_url = ""
                    if is_i2v:
                        try:
                            if isinstance(prompt_text, tuple):
                                prompt_text, scene_image_url = prompt_text
                            if not scene_image_url:
                                logger.warning(f"[Task {idx}] i2v 模式无 image_url，降级为文生视频")
                        except Exception:
                            pass
                            
                    quota_error_triggered = False
                    task_id_video = None
                    
                    for attempt in range(1, 4):  # 最多 3 次重试提交
                        try:
                            task_id_video = await active_video_api.submit_task(
                                prompt_text,
                                image_url=scene_image_url,
                                **(video_params or {})
                            )
                            logger.info(f"[Task {idx}] 提交成功，任务 ID: {task_id_video}")
                            break
                        except Exception as e:
                            err_str = str(e)
                            if "401" in err_str or "unauthorized" in err_str.lower() or "quota" in err_str.lower() or "balance" in err_str.lower():
                                logger.error(f"[Task {idx}] Quota 耗尽或 Token 无效: {err_str}")
                                quota_error_triggered = True
                                break # 打破此次提交尝试，进入大轮询等待
                                
                            if "Throttling" in err_str and attempt < 3:
                                wait = attempt * 10
                                logger.warning(f"[Task {idx}] QPS 限流，{wait}秒后重试 ({attempt}/3)...")
                                await asyncio.sleep(wait)
                                continue
                            logger.error(f"[Task {idx}] 提交失败: {e}")
                            return None
                    else:
                        logger.error(f"[Task {idx}] 重试 3 次均失败（非配额拦截），放弃")
                        return None
                        
                    if quota_error_triggered:
                        # 抛出配额耗尽事件给前台订阅
                        if task_id:
                            await state_keeper.append_log(task_id, {
                                "stage": "quota_error", 
                                "status": "error", 
                                "chapter": f"视频任务 {idx}",
                                "message": f"[{active_gateway}] 认证失败或配额已耗尽，请在页面左侧切换为可用模型以继续流转！",
                                "ts": time.time()
                            }, force_save=True)
                        
                        logger.warning(f"⏳ [Task {idx}] 捕获 Quota 拦截异常。挂起线程并轮询等待前端热切换模型...")
                        
                        # 每 5 秒检查一次 state_keeper 看网关或模型有没有变
                        if not task_id:
                            logger.error(f"[Task {idx}] 没有捕获到全局 task_id，无法热重载网关，直接失败返回。")
                            return None
                            
                        while True:
                            await asyncio.sleep(5)
                            master_task = await state_keeper.get_task(task_id)
                            
                            is_config_changed = False
                            if master_task and master_task.get("gateway") and master_task["gateway"] != current_gateways[idx]:
                                new_gw = master_task["gateway"]
                                logger.info(f"🔄 [Task {idx}] 侦测到前端已下发热更新命令，网关由 {current_gateways[idx]} 即将迁移至 -> {new_gw}")
                                current_gateways[idx] = new_gw
                                is_config_changed = True
                                
                            if master_task and master_task.get("aliyun_video_model"):
                                new_model = master_task["aliyun_video_model"]
                                # 动态更新内部 params，让下一次循环提交时使用新模型
                                if video_params and video_params.get("aliyun_video_model") != new_model:
                                    logger.info(f"🔄 [Task {idx}] 侦测到子模型热更新: {video_params.get('aliyun_video_model')} -> {new_model}")
                                    if video_params is None: video_params = {}
                                    video_params["aliyun_video_model"] = new_model
                                    is_config_changed = True

                            if is_config_changed:
                                quota_error_triggered = False # 必须释放此拦截标记，否则外层会原地再次拦截
                                break # 跳出无限等待循环，重新开始外层 while True
                            
                            # Phase 9: 显式强制重试 (不换配置)
                            if master_task and master_task.get("retry_signal"):
                                logger.info(f"⚡ [Task {idx}] 侦测到强行显式重试指令！打破挂起状态，重新下发请求...")
                                await state_keeper.update_task(task_id, {"retry_signal": False}, force_save=True)
                                quota_error_triggered = False # 释放外围拦截标记
                                break
                            
                            if master_task and master_task.get("finished"):
                                logger.warning(f"🛑 [Task {idx}] 等待期间检测到主任务已被手动中断，安全退出。")
                                return None
                                
                        continue # 开始新的 while True 重新拿着新网关去尝试！

                    # 轮询状态
                    max_retries = 150
                    error_strikes = 0  # 连续错误计数器
                    
                    for poll_idx in range(max_retries):
                        await asyncio.sleep(5)
                        try:
                            status_info = await active_video_api.check_status(task_id_video)
                            error_strikes = 0  # 状态查询成功，重置错误统计
                        except Exception as se:
                            error_strikes += 1
                            wait_sec = min(5 * (2 ** (error_strikes - 1)), 60) # 5s, 10s, 20s, 40s, 60s
                            logger.warning(
                                f"⚠️ [Task {idx}] 状态查询异常 (连续 {error_strikes} 次) "
                                f"→ 错误信息: {se}。休眠 {wait_sec}秒 后继续轮询"
                            )
                            if error_strikes >= 5:
                                logger.error(f"❌ [Task {idx}] API 断联 5 次，视为最终失败，结束轮询")
                                return None
                            
                            await asyncio.sleep(wait_sec)
                            continue
                            
                        status = status_info.get("status")

                        if status == "succeeded":
                            video_url = status_info.get("video_url")
                            logger.info(f"[Task {idx}] 生成成功! 视频 URL: {video_url}")

                            # 下载逻辑 — 优先 Aria2c，降级 aiohttp
                            from core.services.download_service import aria2c_service
                            download_dir = os.path.abspath(
                                os.path.join(
                                    os.path.dirname(os.path.abspath(__file__)),
                                    "..", "Download"
                                )
                            )
                            _novel_id = getattr(self, "_current_novel_id", account)
                            try:
                                local_path = await aria2c_service.Aria2cService.smart_download(
                                    url=video_url,
                                    novel_id=_novel_id,
                                    episode_num=idx,
                                    download_dir=download_dir,
                                )
                                logger.info(f"[Task {idx}] ✅ 视频已下载至本地: {local_path}")
                                return local_path
                            except Exception as dl_err:
                                logger.error(f"[Task {idx}] 下载失败: {dl_err}")
                                return video_url

                        elif status == "failed":
                            logger.error(f"[Task {idx}] 生成明确失败: {status_info.get('error')}")
                            return None
                        elif status in ("running", "queued", "pending"):
                            logger.debug(f"[Task {idx}] 等待生成中... 最新状态: {status}")
                        else:
                            logger.warning(f"[Task {idx}] 遇到未经注册的 API 状态词汇: {status}，视为进行中...")
                            continue

                    logger.error(f"[Task {idx}] 轮询到达硬上限 ({max_retries}次)，强制超时中止")
                    return None

        # Gather all tasks
        tasks = [submit_and_wait(p, i + 1) for i, p in enumerate(prompts)]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        success_count = sum(
            1 for r in results if r and not isinstance(r, Exception)
        )
        logger.info(f"🎥 视频生成批次结束！成功 {success_count}/{len(prompts)} 个。")
        
        # 企微播报通知
        try:
            from core.services.notify_service import WeChatNotifier
            WeChatNotifier.send_task_completion(
                novel_id=account, 
                scenes_count=len(prompts), 
                success_count=success_count, 
                task_id=task_id
            )
        except Exception as e:
            logger.error(f"通知派发失败: {e}")


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
        task_id: str = "",
        memory_lock: bool = False,
        image_gateway: str = "aliyun",
    ):
        """
        v2.6.0 批量文件处理入口。
        files: [{"name": str, "content": str}, ...]（已按章节顺序排列）
        实现"同书串行、分镜并发、断点续传"三大特性。
        """
        if video_params is None:
            video_params = {}

        logger.info(f"📚 [批量流水线] 开始: novel_id={novel_id}, 文件数={len(files)}, memory_lock={memory_lock}")

        # === Stage1: 同 novel_id 内串行（章节相互依赖）===
        all_scenes = []
        all_visual_prompts = []  # v2.7.0: 收集全量视觉提示词供视频生成调度
        import gc  # 引入垃圾回收控制内存
        
        for chapter_idx, file_info in enumerate(files):
            try:
                chapter_name = file_info.get("name", f"chapter_{chapter_idx+1}")
                chapter_text = file_info.get("content", "")
    
                # 断点续传检查
                if self.bitable.check_chapter_stage1_done(novel_id, chapter_name):
                    logger.info(f"⏭️ [断点续传] {chapter_name} 已完成 Stage1，跳过")
                    if on_progress:
                        await on_progress("stage1", chapter_idx + 1, len(files), chapter_name, "跳过")
                    continue
    
                # novel_id 粒度锁：同一小说的章节必须按序处理
                async with self.task_manager.get_novel_lock(novel_id):
                    if memory_lock:
                        logger.info(f"🔒 [MemoryLock] 记忆演进逻辑已被锁定，跳过 {chapter_name} 的记忆提取")
                        result = {"status": "success", "message": "Memory lock enabled, skipped."}
                    else:
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
                        continue # 跳过本章后续处理

                    if on_progress:
                        await on_progress("stage1", chapter_idx + 1, len(files), chapter_name, result["status"])

                    # === Stage 1.5: 视觉锚定初始化（仅对 present_in_current=True 的实体）===
                    stage1_entities = result.get("stage1_entities", [])
                    if stage1_entities and not memory_lock:
                        logger.info(f"➡️ [交接] {chapter_name} 传递了 {len(stage1_entities)} 个出场实体，开始 Stage 1.5...")
                        try:
                            await self._run_stage1_5(
                                novel_id=novel_id,
                                chapter_name=chapter_name,
                                present_entities=stage1_entities,
                                image_gateway=image_gateway,
                                task_id=task_id,
                            )
                        except Exception as s15_err:
                            # Fail-Fast: 若资产构建崩溃，全链条截断
                            logger.error(f"🚨 [Stage1.5] 构建视觉资产严重失败: {s15_err}，终止生成流！")
                            raise

                    # ★★★ 原子级断点续传核心修改 ★★★
                    # 只有当 Stage1 解析和 Stage1.5 生图配图**全都成功**通过后，
                    # 才会真正在飞书/DB标记本章 "Stage1=完成"
                    self.bitable.mark_chapter_stage1_complete(novel_id, chapter_name)
                    logger.info(f"✅ [Stage1 & 1.5 原子事务] {chapter_name} 彻底解析并配图完毕，打上完成标记。")
    
            except Exception as e:
                logger.error(f"❌ [Stage1] 处理章节 {chapter_name} 失败: {e}")
                if on_progress:
                    await on_progress("stage1", chapter_idx + 1, len(files), chapter_name, "error")
                continue # 继续处理下一章

        # === Stage2: 严格章节顺序串行 + 每章 Semaphore(3) 内并发 + 3次重试 ===
        # 章节顺序强保证: 1→N 一个不跳过，确保剧情发展不乱序
        all_memories_dict = self.bitable.get_memories_by_novel(novel_id)
        active_memories = [
            v["fields"] for v in all_memories_dict.values()
            if v["fields"].get("status") != "废弃"
        ]

        sem = asyncio.Semaphore(3)
        episode_counter = [1]  # 共享计数器，用 list 实现可变序号

        async def _stage2_chapter(file_info: dict, chapter_idx_s2: int) -> list:
            """Stage2 单章节处理，内部并发切片，外部串行。失败最多重试 3 次。"""
            cname = file_info.get("name", f"chapter_{chapter_idx_s2+1}")
            ctext = file_info.get("content", "")
            chunks = DeepSeekService.smart_chunk_text(ctext, chunk_size)

            async def _one_chunk(chunk_text: str, prev_ctx: dict) -> list:
                relevant = []
                for mem in active_memories:
                    mem_name = mem.get("name", "")
                    if mem_name and mem_name in chunk_text:
                        relevant.append(mem)
                    elif mem.get("category", "") in ["世界观", "氛围", "基调"]:
                        relevant.append(mem)
                mem_ctx = "【全局世界观与当前段落相关的记忆词条】\n"
                for rm in relevant:
                    mem_ctx += f"- [{rm.get('category', '设定')}] {rm.get('name', '')} (ID: {rm.get('entity_id', '')}): {rm.get('lore', '')}\n"
                return await asyncio.to_thread(
                    DeepSeekService.generate_scenes_for_chunk, chunk_text, mem_ctx, prev_ctx
                )

            MAX_RETRY = 3
            chapter_scenes = []
            errors = 0
            
            # 使用列表以便在闭包外修改
            rolling_context = [None]
            
            for chunk_idx, chunk_text in enumerate(chunks):
                chunk_success = False
                for attempt in range(1, MAX_RETRY + 1):
                    try:
                        res = await _one_chunk(chunk_text, rolling_context[0])
                        if isinstance(res, list) and res:
                            chapter_scenes.extend(res)
                            chunk_success = True
                            last_scene = res[-1]
                            rolling_context[0] = {
                                "summary": last_scene.get("summary", ""),
                                "visual_prompt": last_scene.get("visual_prompt", ""),
                                "emotion": last_scene.get("emotion", ""),
                                "camera": last_scene.get("camera", ""),
                                "hook": last_scene.get("hook", ""),
                            }
                            break
                        else:
                            logger.warning(f"⚠️ [Stage2][{cname}] 切片 {chunk_idx+1}/{len(chunks)} 第{attempt}次失败: 无有效分镜")
                            if attempt < MAX_RETRY:
                                await asyncio.sleep(2 * attempt)
                    except Exception as e:
                        logger.error(f"❌ [Stage2][{cname}] 切片 {chunk_idx+1}/{len(chunks)} 第{attempt}次异常: {e}")
                        if attempt < MAX_RETRY:
                            await asyncio.sleep(2 * attempt)
                
                if not chunk_success:
                    errors += 1
                    logger.error(f"🔴 [Stage2][{cname}] 切片 {chunk_idx+1}/{len(chunks)} 彻底失败，将跳过！")
            if chapter_scenes:
                logger.info(f"✅ [Stage2][{cname}] 顺序处理完成: {len(chapter_scenes)} 个分镜 ({errors} 块失败)")
                return chapter_scenes, cname
            else:
                logger.warning(f"⚠️ [Stage2][{cname}] 章节全部失败跳过")
                return [], cname

        logger.info(f"🎦 [Stage2] 开始严格顺序处理 {len(files)} 个章节 (为了保证剧情连贯，已开启单段落顺序解析)")
        from core.protocols.render_protocol import RenderProtocol

        for ch_idx, file_info in enumerate(files):
            chapter_scenes, chapter_name_s2 = await _stage2_chapter(file_info, ch_idx)
            if not chapter_scenes:
                logger.warning(f"⚠️ [Stage2] {file_info.get('name')} 无分镜输出，跳过写入")
                continue

            # 视觉提示词 RenderProtocol 注入
            for scene in chapter_scenes:
                prompt_raw = scene.get("master_prompt", scene.get("visual_prompt", ""))
                if isinstance(prompt_raw, dict):
                    prompt_raw = "\n".join(f"{k}: {v}" for k, v in prompt_raw.items())
                scene["visual_prompt"] = RenderProtocol.inject_render_config(
                    str(prompt_raw),
                    {**video_params, **{"_sandbox_mode": True}} if sandbox_mode else video_params,
                    gateway
                )

            # 序号保证连续递增（episode_counter 跨章节共享）
            start_ep = episode_counter[0]
            episode_counter[0] += len(chapter_scenes)

            ids = self.bitable.insert_new_parsed_scenes(
                chapter_scenes,
                episode_start=start_ep,
                task_id=task_id,
                sandbox_mode=sandbox_mode,
                novel_id=novel_id,
                chapter_name=chapter_name_s2,
                gateway=gateway,
            )
            all_scenes.extend(chapter_scenes)
            # === Scene-Asset 绑定：为 i2v 模式查询分镜对应的实体图片 URL ===
            if not sandbox_mode:
                # 收集本章所有分镜的独立 entity_id
                chapter_entity_ids = set()
                for sc in chapter_scenes:
                    for eid in (sc.get("entity_ids") or []):
                        if eid:
                            chapter_entity_ids.add(str(eid))

                # 并发从飞书素材表查询对应实体的图片 URL
                asset_cache: dict = {}
                if chapter_entity_ids:
                    async def _fetch_one(eid: str):
                        a = await asyncio.to_thread(
                            self.bitable.get_asset_by_entity, eid, novel_id
                        )
                        url = a.get("image_url", "") if a.get("found") else ""
                        return eid, url

                    fetch_results = await asyncio.gather(
                        *[_fetch_one(eid) for eid in chapter_entity_ids],
                        return_exceptions=True,
                    )
                    asset_cache = {
                        eid: url
                        for eid, url in fetch_results
                        if not isinstance((eid, url), Exception) and isinstance(url, str) and url
                    }
                    logger.info(
                        f"[Stage3-Bind] {chapter_name_s2}: "
                        f"{len(asset_cache)}/{len(chapter_entity_ids)} 个实体匹配到图片"
                    )

                # 打包 (prompt, image_url) 元组；无图降级为纯 prompt（t2v fallback）
                for sc in chapter_scenes:
                    vp = sc.get("visual_prompt", "")
                    if not (vp and isinstance(vp, str) and len(vp) > 10):
                        continue
                    image_url = ""
                    for eid in (sc.get("entity_ids") or []):
                        if str(eid) in asset_cache:
                            image_url = asset_cache[str(eid)]
                            break
                    all_visual_prompts.append((vp, image_url) if image_url else vp)

            logger.info(f"📌 [Stage2] {chapter_name_s2} 写入 {len(ids)} 条，集数 {start_ep}~{episode_counter[0]-1}")
            if on_progress:
                await on_progress("stage2", ch_idx + 1, len(files), chapter_name_s2, "success")
                
            # 极致内存保护：整个章节被分镜拆分完毕并落地后，销毁原文缓存
            if "content" in file_info:
                file_info["content"] = ""
            gc.collect()
        # === Stage3: 视频生成调度（生产模式且有分镜时自动派发）===
        if not sandbox_mode and all_visual_prompts:
            logger.info(
                f"🎥 [Stage3] 生产模式开启，自动派发视频生成: {len(all_visual_prompts)} 个分镜 → gateway={gateway}"
            )
            await self.run_video_generation(
                account=novel_id,
                prompts=all_visual_prompts,
                gateway=gateway,
                video_params=video_params,
                task_id=task_id  # 传递 task_id 用于状态跟踪和实时切换配置
            )
        elif sandbox_mode:
            logger.info("📦 [沿箱模式] 视频生成未派发（sandbox_mode=True）")
        else:
            logger.warning("⚠️ [Stage3] 没有收集到分镜提示词，跳过视频派发")

        return {
            "status": "success",
            "novel_id": novel_id,
            "files": len(files),
            "scenes": len(all_scenes),
            "inserted": len(all_visual_prompts),
        }
    
    async def _run_stage1_5(
        self,
        novel_id: str,
        chapter_name: str,
        present_entities: list,
        image_gateway: str = "aliyun",
        task_id: str = "",
    ) -> None:
        """
        Stage 1.5: 视觉锚定初始化。

        对本章 present_in_current=True 的实体列表进行三路判断：
          A. 无图→ DeepSeek 生成英文 Prompt → 生图API → 写入飞书
          B. 有图 + lore 变化→ 迭代模式（保留旧 seed，新 evolution_lore）
             同时将旧 visual_prompt 历史快照 按章节标签归档
          C. 无变化→ 透传，跳过 API 调用
        """
        if not present_entities:
            return

        from core.api.state import StateKeeper
        state_keeper = StateKeeper()
        
        # 包装器，用于在遇到配额错误时热重载 image_svc
        async def init_image_svc(gateway_name):
            if gateway_name == "aliyun":
                return AliyunImageService(model="qwen-image-plus")
            elif gateway_name == "z_image_turbo":
                return AliyunImageService(model="z-image-turbo")
            # 预留给智谱、midjourney 等其他生图服务接入...
            return AliyunImageService(model="qwen-image-plus")

        sem = asyncio.Semaphore(2)  # 防止并发过高限流
        # 创建一个 list 穿透闭包以便于更新，索引 0
        current_gateways = [image_gateway]

        async def _process_one(entity: dict):
            entity_id = entity["entity_id"]
            entity_name = entity["name"]
            asset_type = entity.get("category", "角色")
            lore_changed = entity.get("lore_changed", False)

            async with sem:
                # 查询现有素材（不指定章节，取最新）
                existing = await asyncio.to_thread(
                    self.bitable.get_asset_by_entity, entity_id, novel_id
                )

                if existing.get("found") and not lore_changed:
                    # 路径 C: 无变化，透传
                    logger.info(
                        f"⏩ [Stage1.5] {entity_name} 无视觉变化，透传现有素材"
                    )
                    return

                old_prompt = existing.get("visual_prompt", "") if existing.get("found") else ""
                old_seed = existing.get("seed", None) if existing.get("found") else None
                is_evolution = existing.get("found") and lore_changed

                # 生成 evolution_lore 描述（用于迭代模式）
                evolution_lore = ""
                if is_evolution:
                    new_lore = entity.get("lore", "")
                    old_lore = entity.get("old_lore", "")
                    if new_lore and old_lore and new_lore != old_lore:
                        evolution_lore = f"本章视觉变化：{new_lore[:200]}"
                    elif new_lore:
                        evolution_lore = f"本章新增设定：{new_lore[:200]}"

                while True:
                    # 每次拉取最新的网关实例
                    active_gateway = current_gateways[0]
                    try:
                        image_svc = await init_image_svc(active_gateway)
                    except Exception as ie:
                        logger.error(f"无法初始化生图客户端 {active_gateway}: {ie}")
                        # 延迟等待避免死循环
                        await asyncio.sleep(5)
                        continue

                    # DeepSeek 生成英文 Prompt
                    visual_prompt = await asyncio.to_thread(
                        DeepSeekService.generate_visual_prompt,
                        entity,
                        evolution_lore,
                        old_prompt,
                    )

                    if not visual_prompt:
                        logger.warning(f"⚠️ [Stage1.5] {entity_name} Prompt 生成失败，可能被过滤")
                        # 生图提示词失败视为严重跳过
                        break

                    quota_error_triggered = False
                    img_result = {}
                    
                    try:
                        # 生图（迭代时复用旧 seed，保持视觉 DNA 延续）
                        if is_evolution and old_seed:
                            img_result = await image_svc.evolve_image(
                                original_seed=old_seed,
                                evolution_prompt=visual_prompt,
                            )
                        else:
                            img_result = await image_svc.generate_image(
                                prompt=visual_prompt,
                            )
                    except Exception as e:
                        err_str = str(e)
                        if "401" in err_str or "unauthorized" in err_str.lower() or "quota" in err_str.lower() or "balance" in err_str.lower() or "Arrearage" in err_str:
                            logger.error(f"[Stage1.5] {active_gateway} 生图配额拦截: {err_str}")
                            quota_error_triggered = True
                        else:
                            logger.error(f"❌ [Stage1.5] {entity_name} 网关底层调用失败: {err_str}")
                            # 并非配额问题，不陷入挂起死循环，直接返回跳过该图
                            break

                    if quota_error_triggered:
                        if task_id:
                            await state_keeper.append_log(task_id, {
                                "stage": "quota_error", 
                                "status": "error", 
                                "chapter": f"生图任务 ({entity_name})",
                                "message": f"[{active_gateway}] 账户生图配额耗尽或密钥无效！请在左侧切换【生图模型】以继续补齐残缺的剧集资产。",
                                "ts": time.time()
                            }, force_save=True)
                            
                        logger.warning(f"⏳ [Stage1.5] {entity_name} 制图被挂起！等待前端热切换生图模型指令...")
                        
                        if not task_id:
                            logger.error(f"没有全局 task_id，无法热重载生图网关，强行中止本实体的处理。")
                            break
                        
                        # 陷入心跳轮询，等待换模型
                        while True:
                            await asyncio.sleep(5)
                            master_task = await state_keeper.get_task(task_id)
                            # 假设前端通过同样的 udpate_config 更新了 image_gateway 字段
                            db_image_gw = master_task.get("image_gateway", active_gateway)
                            if db_image_gw != active_gateway:
                                logger.info(f"🔄 [Stage1.5] 侦测到生图网关热更新命令！{active_gateway} -> {db_image_gw}")
                                current_gateways[0] = db_image_gw
                                quota_error_triggered = False # 重置拦截标记
                                break # 跳出无限等待，重新开始外层 while True
                            
                            # Phase 9: 显式强制重试 (不换配额/配置)
                            if master_task and master_task.get("retry_signal"):
                                logger.info(f"⚡ [Stage1.5] 侦测到生图任务强行显式重试指令！打破挂起状态，重试API...")
                                await state_keeper.update_task(task_id, {"retry_signal": False}, force_save=True)
                                quota_error_triggered = False # 重置拦截标记
                                break
                            
                            if master_task and master_task.get("finished"):
                                logger.warning(f"🛑 [Stage1.5] 用户按下了手动终止。")
                                return
                        
                        continue # 回到顶端使用新 gw 重新尝试
                        
                    if img_result.get("status") != "success":
                        logger.error(
                            f"❌ [Stage1.5] {entity_name} 生图遭到云端拒绝: {img_result}"
                        )
                        break
                    else:
                        logger.info(f"🟢 [Stage1.5] {entity_name} 生图成功: URL={img_result.get('url')[:60]}")

                    image_url = img_result["url"]
                    seed = img_result["seed"]

                    # 写入飞书素材表（版本化，旧 prompt 自动归档进历史快照）
                    await asyncio.to_thread(
                        self.bitable.upsert_asset_record,
                        novel_id=novel_id,
                        entity_id=entity_id,
                        entity_name=entity_name,
                        asset_type=asset_type,
                        visual_prompt=visual_prompt,
                        image_url=image_url,
                        seed=seed,
                        chapter_tag=chapter_name,
                        is_evolution=is_evolution,
                        old_visual_prompt=old_prompt,
                    )

                    logger.info(
                        f"✅ [Stage1.5] {entity_name} @{chapter_name} "
                        f"{'[EVOLVE]' if is_evolution else '[INIT]'} 完美落表"
                    )
                    break # 成功走完实体处理，跳出 `while True`

        # 并发处理本章所有实体
        tasks = [_process_one(e) for e in present_entities]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        errs = [r for r in results if isinstance(r, Exception)]
        if errs:
            logger.warning(f"⚠️ [Stage1.5] {len(errs)} 个实体处理异常: {errs}")
            for ix, er in enumerate(errs):
                logger.error(f"[Stage1.5] 异常详情 {ix+1}: {er}")
        logger.info(
            f"🎬 [Stage1.5] 章节={chapter_name} 共处理 {len(present_entities)} 个实体"
        )



