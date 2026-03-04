from core.config import settings
from fastapi import APIRouter, BackgroundTasks, HTTPException, UploadFile, File, Form
from fastapi.responses import StreamingResponse
from typing import List, Optional
from core.models.schemas import TaskRequest, NovelSubmission, BatchNovelSubmission
from core.pipeline import PipelineOrchestrator
import logging
import asyncio
import os
import uuid
import json
import time

logger = logging.getLogger(__name__)
router = APIRouter()
pipeline = PipelineOrchestrator()

from core.api.state import StateKeeper
state_keeper = StateKeeper()

@router.post("/api/run")
async def run_task(task: TaskRequest, background_tasks: BackgroundTasks):
    prompt_list = [p for p in task.prompt.split('\n') if p.strip()]
    if not prompt_list:
        return {"status": "error", "message": "提示词不能为空"}
        
    background_tasks.add_task(pipeline.run_video_generation, task.account, prompt_list, gateway=task.gateway)
    logger.info(f"已接收 {len(prompt_list)} 个任务，加入后台队列 (账号: {task.account}, 模型: {task.gateway})")
    return {"status": "ok", "message": f"成功接收 {len(prompt_list)} 个视频生成任务"}


from core.services.video_service.defaults import SMART_PRESETS, GATEWAY_SPECS
from core.services.video_service.factory import GATEWAY_REGISTRY
from pydantic import ValidationError

@router.get("/api/system_info")
async def system_info():
    return {
        "mock_mode": settings.MOCK_MODE == "True",
        "smart_presets": SMART_PRESETS,
        "capabilities": GATEWAY_SPECS
    }

# Keeping /api/mock_status for backward compatibility
@router.get("/api/mock_status")
async def mock_status():
    return {"mock_mode": settings.MOCK_MODE == "True"}

from core.models.schemas import UpdatePresetRequest

@router.post("/api/update_presets")
async def update_presets(req: UpdatePresetRequest):
    gateway = req.gateway
    preset_name = req.preset_name
    params = req.video_params
    
    defaults_path = os.path.join(os.path.dirname(__file__), "..", "services", "video_service", "defaults.py")
    defaults_path = os.path.abspath(defaults_path)
    
    try:
        if gateway in SMART_PRESETS:
            SMART_PRESETS[gateway][preset_name] = params
            
        with open(defaults_path, "r", encoding="utf-8") as f:
            content = f.read()
            
        import re, json
        new_dict_str = json.dumps(SMART_PRESETS, indent=4, ensure_ascii=False)
        new_dict_str = new_dict_str.replace('true', 'True').replace('false', 'False').replace('null', 'None')
        
        # Regex to match SMART_PRESETS dict safely
        new_content = re.sub(r"SMART_PRESETS\s*=\s*\{.*?\}(?=\n+[A-Z_]+\s*=|\Z)", f"SMART_PRESETS = {new_dict_str}", content, flags=re.DOTALL)
        
        with open(defaults_path, "w", encoding="utf-8") as f:
            f.write(new_content)
            
        logger.info(f"✅ 成功将 {gateway} 的 {preset_name} 参数持久化为生产默认。")
        return {"status": "ok", "message": "预设参数已全链路热更新成功"}
    except Exception as e:
        logger.error(f"持久化参数失败: {e}")
        return {"status": "error", "message": str(e)}

@router.post("/api/upload_novel")
async def upload_novel(req: NovelSubmission, background_tasks: BackgroundTasks):
    text = req.content.strip()
    account = req.account.strip()
    style = req.style
    gateway = req.gateway
    
    # Validate video_params via Pydantic Schema injection from Registry!
    try:
        registry_entry = GATEWAY_REGISTRY.get(gateway)
        if not registry_entry:
            return {"status": "error", "message": f"未知的网关模型: {gateway}"}
            
        SchemaClass = registry_entry["schema"]
        # Inject gateway into the dict so Literal validation succeeds!
        val_payload = req.video_params.copy()
        val_payload["gateway"] = gateway
        
        validated_params = SchemaClass(**val_payload)
        video_params = validated_params.dict(exclude_none=True, exclude={"gateway"})
    except ValidationError as e:
        logger.error(f"视频参数校验失败: {e}")
        # Parse field-level specifics
        errs = e.errors()
        field_name = errs[0]['loc'][-1] if errs[0].get('loc') else "Unknown Field"
        err_msg = errs[0]['msg']
        return {
            "status": "error", 
            "message": f"[网关参数受阻] 发生拒止: {field_name} - {err_msg}",
            "field_error": {"field": str(field_name), "reason": err_msg}
        }
        
    # New options
    llm_temperature = req.llm_temperature
    top_p = req.top_p
    chunk_size = req.chunk_size
    sandbox_mode = req.sandbox_mode
    
    if not text:
        return {"status": "error", "message": "文章内容为空！"}
    
    # novel_id 传入 pipeline
    novel_id = getattr(req, "novel_id", "") or ""

    async def process_and_queue():
        try:
            res = await pipeline.process_novel_to_feishu(
                novel_text=text,
                style_key=style,
                llm_temperature=llm_temperature,
                top_p=top_p,
                chunk_size=chunk_size,
                video_params=video_params,
                gateway=gateway,
                sandbox_mode=sandbox_mode,
                novel_id=novel_id,
            )
            if res.get("status") == "success" and res.get("prompts"):
                prompts = res.get("prompts", [])
                logger.info(f"✨ 拆解完成 (风格: {style})，获取到 {len(prompts)} 个分镜并写入飞书。")
                logger.info("✨ API 接口已快速释放，后台 Worker 守护进程接管分镜视频生成逻辑！")
            else:
                logger.error("❌ 拆解失败或没有获取到分镜。")
        except Exception as e:
            logger.error(f"DeepSeek 队列处理发生异常: {e}")

    asyncio.create_task(process_and_queue())
    logger.info(f"📚 已在后台开启【闪电解文】线程 (风格: {style}, 模型: {gateway}, novel_id: {novel_id})，文本长度：{len(text)}")
    return {"status": "ok", "message": f"文章已交由 DeepSeek AI 处理（模式：{style}）并在成功后自动触发视频生成！"}

@router.get("/api/logs")
async def get_logs(lines: int = 50):
    """读取最后的日志内容给前端展示"""
    LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "logs", "app.log")
    try:
        if not os.path.exists(LOG_FILE):
            return {"logs": ["暂无日志记录"]}
        with open(LOG_FILE, 'r', encoding='utf-8') as f:
            all_lines = f.readlines()
            result_logs = []
            for line in all_lines[-int(lines):]:
                line = line.strip()
                # Truncate extremely long lines (like API requests with huge tokens) so they don't wrap and fill the screen
                if len(line) > 180:
                    line = line[:180] + " ... [已截断]"
                result_logs.append(line)
            return {"logs": result_logs}
    except Exception as e:
        return {"logs": [f"无法读取日志: {e}"]}

@router.post("/api/clear_logs")
async def clear_logs():
    """彻底清空历史日志"""
    LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "logs", "app.log")
    try:
        if os.path.exists(LOG_FILE):
            open(LOG_FILE, 'w', encoding='utf-8').close()
        return {"status": "ok"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@router.get("/api/outputs")
async def get_outputs():
    """获取输出目录下的所有文件并按时间倒序排列"""
    app_dir = os.path.dirname(os.path.abspath(__file__))
    output_dirs = [os.path.join(app_dir, "..", "..", "output"), os.path.join(app_dir, "..", "..", "outputs"), os.path.join(app_dir, "..", "..", "Download")]
    files_info = []
    
    for d in output_dirs:
        if os.path.exists(d):
            for f in os.listdir(d):
                if f.endswith(".mp4"):
                    filepath = os.path.join(d, f)
                    try:
                        mtime = os.path.getmtime(filepath)
                        size = os.path.getsize(filepath)
                        files_info.append({
                            "name": f,
                            "path": os.path.abspath(filepath),
                            "time": mtime,
                            "size": round(float(size) / (1024 * 1024), 2) # MB
                        })
                    except:
                        pass
                        
    # Sort files by name so that "第01集..." comes before "第02集..."
    files_info.sort(key=lambda x: x["name"])
    
    import datetime
    for item in files_info:
        item["time_str"] = datetime.datetime.fromtimestamp(float(item["time"])).strftime('%m-%d %H:%M:%S')
        
    return {"files": files_info}

from core.models.schemas import RerouteRequest
from core.protocols.render_protocol import RenderProtocol
from core.services.db_service.feishu_bitable import FeishuBitableManager

@router.post("/api/reroute_sandbox")
async def reroute_sandbox(req: RerouteRequest):
    record_id = req.record_id
    feishu = FeishuBitableManager()
    fields = feishu.get_record_by_id(record_id)
    if not fields:
        return {"status": "error", "message": "无法找到该记录。"}
        
    def flatten(val):
        if isinstance(val, str): return val
        if isinstance(val, list): return "".join(seg.get("text", "") if isinstance(seg, dict) else str(seg) for seg in val)
        return str(val)
        
    visual_raw = flatten(fields.get("视觉提示词", fields.get("Visual Prompt", fields.get("视频提示词", ""))))
    if not visual_raw:
        return {"status": "error", "message": "该记录缺少视觉提示词，无法进行一键投产。"}
        
    from core.services.video_service.factory import GATEWAY_REGISTRY
    
    found_config = None
    found_gw = None
    clean_prompt = visual_raw
    
    for gw in GATEWAY_REGISTRY.keys():
        p, cfg, found = RenderProtocol.extract_render_config(visual_raw, gw)
        if found:
            clean_prompt = p
            found_config = cfg
            found_gw = gw
            break
            
    if not found_gw:
        return {"status": "error", "message": "未能在提示词中侦测到支持的网关配置，无法重新打包投产。"}
        
    if "_sandbox_mode" in found_config:
        del found_config["_sandbox_mode"]
        
    new_prompt = RenderProtocol.inject_render_config(clean_prompt, found_config, found_gw)
    field_name = "视觉提示词" if "视觉提示词" in fields else ("Visual Prompt" if "Visual Prompt" in fields else "视频提示词")
    
    try:
        feishu.update_record(record_id, {
            field_name: new_prompt,
            "状态": ["待生成"],
            "异常日志": "[System] 已一键将沙盒记录重组投产并进入队列"
        })
        return {"status": "ok", "message": f"一键投产成功！已剥离沙盒标记并重新分配至 {found_gw} 的真实生成队列。"}
    except Exception as e:
        return {"status": "error", "message": f"飞书记录更新失败: {e}"}


# =============================================================
# v2.6.0: 批量媒务接口
# =============================================================

@router.post("/api/upload_novel_batch")
async def upload_novel_batch(
    background_tasks: BackgroundTasks,
    novel_id: str = Form(...),
    files: List[UploadFile] = File(...),
    memory_lock: bool = Form(False),
    style: str = Form("anime"),
    gateway: str = Form("wan_2_6"),
    image_gateway: str = Form("aliyun"),
    sandbox_mode: bool = Form(True),
    chunk_size: int = Form(1000),
    # --- UI Config Panel Params ---
    aliyun_image_model: str = Form("wan2.6-t2i"),
    aliyun_video_model: str = Form("wan2.6-i2v-flash"),
    prompt_extend: bool = Form(True),
    auto_audio: bool = Form(True),
    tts_voice: str = Form("Cherry"),
    aspect_ratio: str = Form("16:9"),
    segmented_processing: str = Form("auto"),
):
    """
    批量多文件上传入口（multipart/form-data）。
    接受浏览器 FormData 提交的文件列表，自动转换为 FileItem 格式。
    返回 task_id 供前端订阅 SSE 流。
    """
    # 强制动态网关绑定，防止前端引用的旧版 JS 缓存发送错误的 gateway (如默认的 seedance)
    if "wan" in aliyun_video_model.lower():
        gateway = "wan_2_6"
    elif "seedance" in aliyun_video_model.lower() or "volc" in aliyun_video_model.lower():
        gateway = "seedance-1.0-pro-fast"

    # 验证 novel_id（防路径穿越）
    import re
    if not re.match(r'^[\w\u4e00-\u9fa5\-]{1,32}$', novel_id):
        raise HTTPException(status_code=422, detail="novel_id 格式非法，只允许汉字/字母/数字/下划线/连字符，长度1-32")

    # 读取上传文件内容，构建 FileItem 列表
    from core.models.schemas import FileItem
    file_items = []
    for upload_file in files:
        raw = await upload_file.read()
        try:
            content = raw.decode("utf-8")
        except UnicodeDecodeError:
            content = raw.decode("gbk", errors="replace")
        file_items.append(FileItem(name=upload_file.filename or "chapter.txt", content=content))

    if not file_items:
        raise HTTPException(status_code=422, detail="未收到任何文件")

    task_id = str(uuid.uuid4())
    initial_task = {
        "status": "queued",
        "novel_id": novel_id,
        "total_files": len(file_items),
        "stage1_done": 0,
        "stage2_done": 0,
        "scenes": 0,
        "failed_chapters": [],
        "log": [],
        "finished": False,
    }
    await state_keeper.create_task(task_id, initial_task)

    async def _run_batch():
        await asyncio.sleep(1)  # Allow HTTP response to flush before blocking event loop
        await state_keeper.update_task(task_id, {"status": "running"})

        _last_save_time = 0
        async def on_progress(stage, current, total, chapter_name, status):
            nonlocal _last_save_time
            now = time.time()
            force = status in ("error", "success") or (now - _last_save_time > 3)
            
            updates = {}
            if stage == "stage1":
                updates["stage1_done"] = current
                if status not in ("success",):
                    t = await state_keeper.get_task(task_id)
                    fc = t.get("failed_chapters", [])
                    fc.append(chapter_name)
                    updates["failed_chapters"] = fc
            if updates:
                await state_keeper.update_task(task_id, updates, force_save=False)
                
            log_entry = {
                "stage": stage,
                "chapter": chapter_name,
                "progress": f"{current}/{total}",
                "status": status,
                "ts": now
            }
            await state_keeper.append_log(task_id, log_entry, force_save=force)
            if force:
                _last_save_time = now

        try:
            result = await pipeline.process_files_batch(
                files=[f.dict() for f in file_items],
                novel_id=novel_id,
                style_key=style,
                gateway=gateway,
                image_gateway=image_gateway,
                video_params={
                    "aliyun_image_model": aliyun_image_model,
                    "aliyun_video_model": aliyun_video_model,
                    "prompt_extend": prompt_extend,
                    "auto_audio": auto_audio,
                    "tts_voice": tts_voice,
                    "aspect_ratio": aspect_ratio,
                    "segmented_processing": segmented_processing,
                },
                sandbox_mode=sandbox_mode,
                chunk_size=chunk_size,
                on_progress=on_progress,
                task_id=task_id,
                memory_lock=memory_lock,
            )
            await state_keeper.update_task(task_id, {"status": result.get("status", "done"), "scenes": result.get("scenes", 0)})
        except Exception as e:
            logger.error(f"[批量任务] {task_id} 异常: {e}")
            await state_keeper.update_task(task_id, {"status": "error", "error": str(e)})
        finally:
            await state_keeper.update_task(task_id, {"finished": True}, force_save=True)

    asyncio.create_task(_run_batch())
    logger.info(f"📚 [批量接口] 任务 {task_id} 已入队: novel_id={novel_id}, 文件数={len(file_items)}")
    return {
        "status": "ok",
        "task_id": task_id,
        "novel_id": novel_id,
        "total_files": len(file_items),
        "message": f"批量任务已入队，使用 task_id={task_id} 查询进度"
    }


@router.get("/api/batch_status/{task_id}")
async def get_batch_status(task_id: str):
    """返回批量任务当前的进度快照（键入式轮询）"""
    task = await state_keeper.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"task_id={task_id} 不存在或已过期")
    return {
        "task_id": task_id,
        "status": task["status"],
        "novel_id": task["novel_id"],
        "total_files": task["total_files"],
        "stage1_done": task["stage1_done"],
        "scenes": task["scenes"],
        "failed_chapters": task["failed_chapters"],
        "finished": task["finished"],
    }


@router.get("/api/progress_stream/{task_id}")
async def progress_stream(task_id: str):
    """
    SSE 实时进度流。
    如果遭遇服务端宕机，内存_batch_tasks丢失，则通过 AQL 查询飞书实现进度基线回捞。
    """
    from fastapi.responses import StreamingResponse
    import asyncio
    task = await state_keeper.get_task(task_id)

    # === [增强设计] 内存丢失时的飞书回捞机制 ===
    if not task:
        logger.warning(f"⚠️ [SSE] 内存未找到 task_id={task_id}，尝试从飞书回捞历史进度...")
        try:
            from core.services.db_service.feishu_bitable import FeishuBitableManager
            feishu = FeishuBitableManager()
            recovered_records = feishu.get_records_by_task_id(task_id)
            if recovered_records:
                logger.info(f"✅ 从飞书恢复了 {len(recovered_records)} 个分镜进度")
                task = {
                    "status": "done",
                    "novel_id": "recovered_task",
                    "total_files": -1,
                    "stage1_done": -1,
                    "stage2_done": -1,
                    "scenes": len(recovered_records),
                    "failed_chapters": [],
                    "log": [{"stage": "recovered", "status": "success", "ts": time.time()}],
                    "finished": True,
                }
            else:
               raise HTTPException(status_code=404, detail=f"task_id={task_id} 内存与飞书均未找到记录")
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"🔗 从飞书恢复进度失败: {e}")
            raise HTTPException(status_code=500, detail="尝试从飞书回捞状态时引发异常")

    async def event_generator():
        last_log_idx = 0
        last_heartbeat = time.time()
        HEARTBEAT_INTERVAL = 15  # 秒

        while True:
            now = time.time()
            current_task = await state_keeper.get_task(task_id) or task
            logs = current_task.get("log", [])

            # 推送新日志条目
            new_logs = logs[last_log_idx:]
            for entry in new_logs:
                data = json.dumps(entry, ensure_ascii=False)
                yield f"data: {data}\n\n"
                last_heartbeat = now  # 有数据发送，重置心跳计时
            last_log_idx += len(new_logs)

            # 心跳包：超过 15s 未发送任何数据就放心跳注释
            if (now - last_heartbeat) >= HEARTBEAT_INTERVAL:
                yield ": keep-alive\n\n"
                last_heartbeat = now

            # 任务完成，发送最终状态并关闭流
            if current_task.get("finished"):
                final = {
                    "event": "done",
                    "status": current_task.get("status"),
                    "scenes": current_task.get("scenes", 0),
                    "failed_chapters": current_task.get("failed_chapters", []),
                }
                yield f"data: {json.dumps(final, ensure_ascii=False)}\n\n"
                break

            await asyncio.sleep(1)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # 防止 Nginx 缓充
        }
    )

@router.get("/api/tasks/active")
async def get_active_task():
    """返回当前最近一个未完成的 Batch Task，便于页面刷新后自动重连进度条。"""
    active_tasks = await state_keeper.get_all_active_tasks()
    if not active_tasks:
        return {"status": "none"}
    
    # 获取最后插入的活跃任务
    latest_task_id = list(active_tasks.keys())[-1]
    task = active_tasks[latest_task_id]
    return {
        "status": "active",
        "task_id": latest_task_id,
        "novel_id": task.get("novel_id", ""),
        "total_files": task.get("total_files", 0)
    }

@router.post("/api/tasks/stop/{task_id}")
async def stop_task(task_id: str):
    """
    将指定任务标记为已停止（finished=True, status=stopped）。
    后台 asyncio 任务在下一个章节检查点时会读取此标记并提前退出。
    """
    task = await state_keeper.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
    await state_keeper.stop_task(task_id)
    logger.info(f"🛑 任务 {task_id} 已被手动停止")
    return {"status": "stopped", "task_id": task_id}

@router.post("/api/tasks/stop_all")
async def stop_all_tasks():
    """停止所有当前活跃任务（批量停止）。"""
    stopped = await state_keeper.stop_all_tasks()
    logger.info(f"🛑 已停止 {len(stopped)} 个活跃任务: {stopped}")
    return {"status": "ok", "stopped": stopped}

from pydantic import BaseModel
from typing import Optional
class UpdateTaskConfigRequest(BaseModel):
    gateway: Optional[str] = None
    image_gateway: Optional[str] = None
    aliyun_video_model: Optional[str] = None

@router.post("/api/tasks/update_config/{task_id}")
async def update_task_config(task_id: str, req: UpdateTaskConfigRequest):
    """
    实时更新正在运行的任务的配置（如切换视频生成或图片生成网关、以及更换具体模型名）。
    """
    task = await state_keeper.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.get("finished"):
        return {"status": "error", "message": "该任务所在的后台进程已经终止或死亡，无法再接受配置更新！请重新提交文本。"}
        
    updates = {}
    if req.gateway is not None:
        updates["gateway"] = req.gateway
    if req.image_gateway is not None:
        updates["image_gateway"] = req.image_gateway
    if req.aliyun_video_model is not None:
        updates["aliyun_video_model"] = req.aliyun_video_model
        
    if updates:
        # 为了让 pipeline 也能读到单独针对 video_params 的注入，这块其实直接更新到外层就行
        # 稍后 pipeline.py 会自动读取外层的 aliyun_video_model
        await state_keeper.update_task(task_id, updates, force_save=True)
        logger.info(f"🔄 任务 {task_id} 配置已更新: {updates}")
    return {"status": "ok", "message": "Config updated"}

@router.post("/api/tasks/retry/{task_id}")
async def retry_task(task_id: str):
    """
    接收前端一键重试的信号，给任务打上 retry_signal 使得挂起的任务强行跳回尝试循环
    """
    task = await state_keeper.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.get("finished"):
        return {"status": "error", "message": "关联的底层 Python 进程已由于服务器关机意外死亡！一键重试无法对空气生效。请携带相同的 [项目 ID] 重新提交原文件，系统会自动断点续传。"}
        
    await state_keeper.update_task(task_id, {"retry_signal": True}, force_save=True)
    logger.info(f"⚡ 任务 {task_id} 收到前端显式重试信号 (retry_signal=True)")
    return {"status": "ok", "message": "Retry signal submitted"}

@router.get("/api/system/health")
async def get_system_health():
    """获取系统关键服务的健康状态，主要是 Aria2c"""
    from core.services.download_service import aria2c_service
    aria2_health = await aria2c_service.is_healthy()
    return {
        "aria2c_healthy": aria2_health
    }
