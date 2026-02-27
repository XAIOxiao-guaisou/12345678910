import asyncio
import logging
import time
import os
from core.config import settings
from core.services.db_service.feishu_bitable import FeishuBitableManager
from core.services.video_service.factory import VideoServiceFactory

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("WorkerDaemon")

async def process_single_task(feishu_manager, task, download_dir):
    record_id = task["record_id"]
    prompt = task["visual_prompt"]
    gateway = task["video_model"]
    
    # Extract embedded JSON config if present
    import re, json
    config = {}
    config_match = re.search(r'\[RENDER_CONFIG\](.*?)\[/RENDER_CONFIG\]', prompt, re.DOTALL)
    if config_match:
        try:
            config = json.loads(config_match.group(1).strip())
            prompt = re.sub(r'\n*\[RENDER_CONFIG\].*?\[/RENDER_CONFIG\]', '', prompt, flags=re.DOTALL).strip()
        except:
            logger.warning(f"未能解析附加参数 JSON: {config_match.group(1)}")
            

    
    try:
        # 1. 锁定状态 -> 生成中
        feishu_manager.update_record(record_id, {"状态": ["生成中"]})
        logger.info(f"[Task {record_id}] 已锁定，准备使用网关 {gateway} 生成")
        
        # 2. 判断是否开启 Mock 模式
        if settings.MOCK_MODE:
            logger.warning(f"🧪 [Task {record_id}] MOCK_MODE 已开启，跳过真实视频生成，模拟秒级完成！")
            logger.info(f"🧪 [Task {record_id}] Target API Payload (参数透传验证):\n> Render Prompt: {prompt}\n> Render Config: {json.dumps(config, indent=2)}")
            
            await asyncio.sleep(2)  # Simulate small delay
            file_name = f"video_{record_id}_mock.mp4"
            final_path = os.path.join(download_dir, file_name)
            open(final_path, 'wb').close() # touch file
            
            logger.info(f"[Task {record_id}] Mock 本地文件创建成功: {final_path}，准备直传飞书...")
            gateway_display = f"{gateway} (MOCK 模拟)"
            cost_estimate = f"\n> **调度参数:** {json.dumps(config)}\n> **预估真实消耗:** 约 300 秒及对应模型算力"
            
        else:
            # 原有的真实提交逻辑
            video_api = VideoServiceFactory.get_service(gateway)
            task_id = await video_api.submit_task(prompt, **config)
            logger.info(f"[Task {record_id}] API 提交成功 (Config: {config})，任务 ID: {task_id}")
            
            # 3. 轮询状态
            while True:
                await asyncio.sleep(10) # 10s interval
                status_info = await video_api.check_status(task_id)
                status = status_info.get("status")
                
                if status == "success":
                    video_url = status_info.get("video_url")
                    if not video_url:
                        raise Exception("API 返回成功但未提供视频下载链接")
                    
                    # 下载到本地
                    file_name = f"video_{record_id}.mp4"
                    file_path = os.path.join(download_dir, file_name)
                    final_path = await video_api.download_video(video_url, file_path)
                    
                    logger.info(f"[Task {record_id}] 下载本地成功: {final_path}，准备直传飞书...")
                    gateway_display = gateway
                    cost_estimate = ""
                    break
                    
                elif status == "failed":
                    err = status_info.get("error", "未知生成错误")
                    raise Exception(f"视频服务生成失败: {err}")
                else:
                    logger.debug(f"[Task {record_id}] 正在生成中，请耐心等待...")
                    
        # 4. 上传到飞书并闭环
        success = feishu_manager.upload_attachment_and_update_record(record_id, final_path)
        if success:
            logger.info(f"[Task {record_id}] 飞书打通完毕！✅")
            
            # 5. 发送企业微信机器人通知
            if settings.WX_BOT_WEBHOOK:
                try:
                    import requests
                    from datetime import datetime
                    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    msg = {
                        "msgtype": "markdown",
                        "markdown": {
                            "content": f"🎉 **视频生成完成**\n"
                                       f"> **任务ID:** {record_id}\n"
                                       f"> **保存时间:** {now}\n"
                                       f"> **本地保存路径:** {final_path}\n"
                                       f"> **文件名:** {file_name}\n"
                                       f"> **通道:** {gateway_display}"
                                       f"{cost_estimate}"
                        }
                    }
                    resp = requests.post(settings.WX_BOT_WEBHOOK, json=msg, timeout=5)
                    if resp.ok and resp.json().get("errcode") == 0:
                        logger.info(f"[Task {record_id}] 企微通知发送成功！")
                    else:
                        logger.error(f"[Task {record_id}] 企微通知异常: {resp.text}")
                except Exception as e:
                    logger.error(f"[Task {record_id}] 企微通知请求失败: {e}")

            # 6. 清理磁盘空间
            try:
                os.remove(final_path)
                logger.info(f"[Task {record_id}] 清理本地缓存成功: {final_path}")
            except Exception as e:
                logger.error(f"[Task {record_id}] 清理本地缓存失败: {e}")
        else:
            raise Exception("下载成功，但上传飞书作为附件时失败")
                
    except Exception as e:
        logger.error(f"[Task {record_id}] 发生异常熔断: {e}")
        # 回填到飞书的额外字段 "异常日志"
        feishu_manager.update_record(record_id, {
            "状态": ["生成异常"], 
            "异常日志": str(e)
        })

async def worker_loop():
    feishu_manager = FeishuBitableManager()
    
    # 启动时恢复僵尸任务
    feishu_manager.reset_zombie_tasks()
    
    download_dir = os.path.join(os.getcwd(), "Download")
    os.makedirs(download_dir, exist_ok=True)
    
    logger.info("🎬 Worker Daemon 已启动，正在监听飞书【待生成】队列...")
    active_tasks = set() # Store record_ids that are currently being processed locally
    
    while True:
        try:
            pending = feishu_manager.get_pending_tasks()
            for task in pending:
                record_id = task["record_id"]
                if record_id not in active_tasks:
                    logger.info(f"✨ 发现新任务: {record_id}, 启动独立协程处理")
                    active_tasks.add(record_id)
                    
                    def make_done_callback(rid):
                        def callback(fut):
                            active_tasks.discard(rid)
                        return callback
                    
                    # 使用 create_task 极速并发执行，不阻塞主提取线程
                    t = asyncio.create_task(process_single_task(feishu_manager, task, download_dir))
                    t.add_done_callback(make_done_callback(record_id))
                    
        except Exception as e:
            logger.error(f"Worker 调度器遇到异常: {e}")
            
        await asyncio.sleep(20) # 20 秒轮询一次飞书表格

if __name__ == "__main__":
    asyncio.run(worker_loop())
