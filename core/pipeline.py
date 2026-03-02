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
from core.services.image_service.aliyun_image_service import AliyunImageService

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

            # 无论是否 memory_lock 都必须做 Stage1_done 标记，以便断点续传后续
            if result["status"] in ("success", "partial_error"):
                self.bitable.mark_chapter_stage1_complete(novel_id, chapter_name)

                # === Stage 1.5: 视觉锚定初始化（仅对 present_in_current=True 的实体）===
                stage1_entities = result.get("stage1_entities", [])
                if stage1_entities and not memory_lock:
                    await self._run_stage1_5(
                        novel_id=novel_id,
                        chapter_name=chapter_name,
                        present_entities=stage1_entities,
                    )

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
            # === Scene-Asset \u7ed1\u5b9a\uff1a\u4e3a i2v \u6a21\u5f0f\u67e5\u8be2\u5206\u955c\u5bf9\u5e94\u7684\u5b9e\u4f53\u56fe\u7247 URL ===
            if not sandbox_mode:
                # \u6536\u96c6\u672c\u7ae0\u6240\u6709\u5206\u955c\u7684\u72ec\u7acb entity_id
                chapter_entity_ids = set()
                for sc in chapter_scenes:
                    for eid in (sc.get("entity_ids") or []):
                        if eid:
                            chapter_entity_ids.add(str(eid))

                # \u5e76\u53d1\u4ece\u98de\u4e66\u7d20\u6750\u8868\u67e5\u8be2\u5bf9\u5e94\u5b9e\u4f53\u7684\u56fe\u7247 URL
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
                        f"\ud83d\uddbc\ufe0f [Stage3-Bind] {chapter_name_s2}: "
                        f"{len(asset_cache)}/{len(chapter_entity_ids)} \u4e2a\u5b9e\u4f53\u5339\u914d\u5230\u56fe\u7247"
                    )

                # \u6253\u5305 (prompt, image_url) \u5143\u7ec4\uff1b\u65e0\u56fe\u964d\u7ea7\u4e3a\u7eaf prompt\uff08t2v fallback\uff09
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
        # === Stage3: 视频生成调度（生产模式且有分镜时自动派发）===
        if not sandbox_mode and all_visual_prompts:
            logger.info(
                f"🎥 [Stage3] 生产模式开启，自动派发视频生成: {len(all_visual_prompts)} 个分镜 → gateway={gateway}"
            )
            asyncio.create_task(
                self.run_video_generation(
                    account=novel_id,
                    prompts=all_visual_prompts,
                    gateway=gateway,
                    video_params=video_params
                )
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
    ) -> None:
        """
        Stage 1.5: 视觉锚定初始化。

        对本章 present_in_current=True 的实体列表进行三路判断：
          A. 无图→ DeepSeek 生成英文 Prompt → Pollinations 生图 → 写入飞书
          B. 有图 + lore 变化→ 迭代模式（保留旧 seed，新 evolution_lore）
             同时将旧 visual_prompt 历史快照 按章节标签归档
          C. 无变化→ 透传，跳过 API 调用
        """
        if not present_entities:
            return

        image_svc = AliyunImageService()   # qwen-image-plus via DashScope
        logger.info("🎨 [Stage1.5] 使用阿里云 qwen-image-plus 生图")
        sem = asyncio.Semaphore(2)  # 防止并发过高限流

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

                # DeepSeek 生成英文 Prompt
                visual_prompt = await asyncio.to_thread(
                    DeepSeekService.generate_visual_prompt,
                    entity,
                    evolution_lore,
                    old_prompt,
                )

                if not visual_prompt:
                    logger.warning(f"⚠️ [Stage1.5] {entity_name} Prompt 生成失败，跳过")
                    return

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

                if img_result.get("status") != "success":
                    logger.error(
                        f"❌ [Stage1.5] {entity_name} 生图失败: {img_result}"
                    )
                    return
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
                    f"{'[EVOLVE]' if is_evolution else '[INIT]'} 完成"
                )

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

    async def run_video_generation(self, account: str, prompts: list, gateway: str = "seedance-1.5-pro", video_params: dict = None):
        """
        Since we moved away from Playwright and to API-driven interfaces,
        this will use the configured Model API backend based on 'gateway'.
        """
        logger.info(f"=== 开始 API 驱动视频生成流程 (Account: {account}, 模型: {gateway}) ===")

        # 网关标识字符串 → DashScope 实际 model 名称映射
        GATEWAY_MODEL_MAP = {
            "wan_2_6":       "wan2.6-i2v",        # 阿里云 i2v（图生视频）— 默认
            "wan2.6":        "wan2.6-i2v",
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

        # Dynamically load the correct class
        if "seedance" in gateway.lower() or "volcengine" in gateway.lower() or gateway == "API_MODE":
            from core.services.video_service.volcengine_service import VolcengineVideoAPI
            api_key = os.environ.get("VOLCENGINE_API_KEY", "")
            if not api_key:
                logger.error("❌ [VideoGen] VOLCENGINE_API_KEY 未配置，无法初始化火山引擎")
                return
            try:
                video_api = VolcengineVideoAPI(api_key=api_key, model_id=api_model)
            except Exception as e:
                logger.error(f"无法初始化火山引擎 API 客户端: {e}")
                return
        elif "wan2.6" in gateway.lower() or "wan_2_" in gateway.lower() or "aliyun" in gateway.lower():
            from core.services.video_service.aliyun_service import Wan2_6VideoAPI
            aliyun_key = os.environ.get("ALIYUN_API_KEY", "") or os.environ.get("DASHSCOPE_API_KEY", "")
            if not aliyun_key:
                logger.error("❌ [VideoGen] ALIYUN_API_KEY 未配置，无法初始化阿里遗子 API")
                return
            try:
                video_api = Wan2_6VideoAPI(api_key=aliyun_key, model=api_model)
            except Exception as e:
                logger.error(f"无法初始化阿里百炼 API 客户端: {e}")
                return
        else:
            logger.error(f"不支持的网关模型类型: {gateway}")
            return
            
        logger.info(f"收到 {len(prompts)} 个分镜，开始限流并行视频生成 (Semaphore=3, 间隔 2s, 模型={api_model})...")
        is_i2v = "i2v" in api_model.lower()
        if is_i2v:
            logger.info("🖼️ [Stage3] i2v 模式: 将从飞书素材表召回对应分镜的图片 URL")

        # 并发控制：Semaphore(3) 限制同时提交数
        submit_sem = asyncio.Semaphore(3)

        async def submit_and_wait(prompt_text, idx):
            async with submit_sem:
                # 提交间隔 — 防止 QPS 这突破
                await asyncio.sleep((idx - 1) * 2)

                # i2v 模式：从飞书素材表查对应分镜的图片 URL
                scene_image_url = ""
                if is_i2v:
                    try:
                        # 尝试从分镜内嵌元数据提取 image_url
                        if isinstance(prompt_text, tuple):
                            prompt_text, scene_image_url = prompt_text
                        if not scene_image_url:
                            logger.warning(
                                f"[Task {idx}] i2v 模式无 image_url，降级为文生视频"
                            )
                    except Exception:
                        pass
                for attempt in range(1, 4):  # 最多 3 次重试
                    try:
                        task_id = await video_api.submit_task(
                            prompt_text,
                            image_url=scene_image_url,  # i2v 传入，t2v/seedance 忽略
                            **(video_params or {})
                        )
                        logger.info(f"[Task {idx}] 提交成功，任务 ID: {task_id}")
                        break
                    except Exception as e:
                        err_str = str(e)
                        if "Throttling" in err_str and attempt < 3:
                            wait = attempt * 10
                            logger.warning(
                                f"[Task {idx}] QPS 限流，{wait}秒后重试 "
                                f"({attempt}/3)..."
                            )
                            await asyncio.sleep(wait)
                            continue
                        logger.error(f"[Task {idx}] 提交失败: {e}")
                        return None
                else:
                    logger.error(f"[Task {idx}] 重试 3 次均失败，放弃")
                    return None

                # 轮询状态
                max_retries = 150
                error_strikes = 0  # 连续错误计数器

                for poll_idx in range(max_retries):
                    await asyncio.sleep(5)
                    try:
                        status_info = await video_api.check_status(task_id)
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
                        download_dir = os.path.abspath(
                            os.path.join(
                                os.path.dirname(os.path.abspath(__file__)),
                                "..", "Download"
                            )
                        )
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

        tasks = [submit_and_wait(p, i + 1) for i, p in enumerate(prompts)]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        success_count = sum(
            1 for r in results if r and not isinstance(r, Exception)
        )
        logger.info(f"� 视频生成批次结束！成功 {success_count}/{len(prompts)} 个。")

