from fastapi import APIRouter, BackgroundTasks, HTTPException
from core.models.schemas import TaskRequest, NovelSubmission
from core.pipeline import PipelineOrchestrator
import logging
import asyncio
import os

logger = logging.getLogger(__name__)
router = APIRouter()
pipeline = PipelineOrchestrator()

@router.post("/api/run")
async def run_task(task: TaskRequest, background_tasks: BackgroundTasks):
    prompt_list = [p for p in task.prompt.split('\n') if p.strip()]
    if not prompt_list:
        return {"status": "error", "message": "提示词不能为空"}
        
    background_tasks.add_task(pipeline.run_video_generation, task.account, prompt_list, gateway=task.gateway)
    logger.info(f"已接收 {len(prompt_list)} 个任务，加入后台队列 (账号: {task.account}, 模型: {task.gateway})")
    return {"status": "ok", "message": f"成功接收 {len(prompt_list)} 个视频生成任务"}

from core.config import settings

@router.get("/api/mock_status")
async def mock_status():
    return {"mock_mode": settings.MOCK_MODE}

@router.post("/api/upload_novel")
async def upload_novel(req: NovelSubmission, background_tasks: BackgroundTasks):
    text = req.content.strip()
    account = req.account.strip()
    style = req.style
    gateway = req.gateway
    
    # New options
    llm_temperature = req.llm_temperature
    top_p = req.top_p
    chunk_size = req.chunk_size
    if not text:
        return {"status": "error", "message": "文章内容为空！"}
    
    async def process_and_queue():
        try:
            # use asyncio.to_thread because process_novel_to_feishu has blocking requests and sleeps
            res = await asyncio.to_thread(
                pipeline.process_novel_to_feishu, 
                text, 
                style_key=style,
                llm_temperature=llm_temperature,
                top_p=top_p,
                chunk_size=chunk_size
            )
            if res.get("status") == "success" and res.get("prompts"):
                prompts = res.get("prompts", [])
                logger.info(f"✨ 拆解完成 (风格: {style})，获取到 {len(prompts)} 个分镜并写入飞书。")
                logger.info(f"✨ API 接口已快速释放，后台 Worker 守护进程接管分镜视频生成逻辑！")
                # 核心改动：不再由 FastAPI 亲历亲为地直接生成视频
                # await pipeline.run_video_generation(account, prompts, gateway=gateway)
            else:
                logger.error("❌ 拆解失败或没有获取到分镜。")
        except Exception as e:
            logger.error(f"DeepSeek 队列处理发生异常: {e}")

    background_tasks.add_task(process_and_queue)
    logger.info(f"📚 已在后台开启【闪电解文】线程 (风格: {style}, 模型: {gateway})，文本长度：{len(text)}")
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
