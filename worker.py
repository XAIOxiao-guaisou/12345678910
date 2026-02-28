import asyncio
import logging
import time
import os
from core.config import settings
from core.services.db_service.feishu_bitable import FeishuBitableManager
from core.services.video_service.factory import VideoServiceFactory
from core.protocols.render_protocol import RenderProtocol

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("WorkerDaemon")

async def process_single_task(feishu_manager, task, download_dir):
    record_id = task["record_id"]
    prompt = task["visual_prompt"]
    gateway = task["video_model"]
    
    # Extract embedded JSON config using RenderProtocol for the specific gateway
    prompt, config, tag_found = RenderProtocol.extract_render_config(prompt, gateway)
    
    from core.services.video_service.defaults import SMART_PRESETS
    
    # [Phase 6] Gateway Trust Priority:
    # Level 1: Physical Feishu column (set at task creation, immutable to LLM edits)
    # Level 2: Text tag auto-detection (scan all gateways if Level 1 produced no tag match)
    # Level 3: Safety fallback to standard preset
    
    if tag_found:
        logger.info(f"[Task {record_id}] ✅ 网关标签识别成功 (Level 1 物理列 + 文本标签匹配): gateway={gateway}")
    
    # Level 2: Try smart detection across all known gateways if the current one's tag wasn't found
    if not tag_found:
        from core.services.video_service.factory import GATEWAY_REGISTRY
        for gw in GATEWAY_REGISTRY.keys():
            if gw == gateway: continue
            alt_prompt, alt_config, alt_found = RenderProtocol.extract_render_config(task["visual_prompt"], gw)
            if alt_found:
                logger.info(f"🔄 [Task {record_id}] Level 2 智能网关对齐：从提示词标签中侵测到网关应为 '{gw}'，覆盖物理列默认 '{gateway}'")
                gateway = gw
                prompt = alt_prompt
                config = alt_config
                tag_found = True
                break

    if not tag_found:
        logger.warning(f"🚨 [Task {record_id}] Level 3 Safety Fallback: 第1/2级均无效，网关为 '{gateway}'，使用标准预设预防漂移")
        config = SMART_PRESETS.get(gateway, {}).get("standard", {})
    
    try:
        # 1. 锁定状态 -> 生成中
        feishu_manager.update_record(record_id, {"状态": ["生成中"]})
        logger.info(f"[Task {record_id}] 已锁定，准备使用网关 {gateway} 生成")
        
        # 2. 统一接口调度 (Adapter Pattern)
        # 获取服务（如果在沙盒模式下，这会安全地返回我们的 MockVideoService）
        video_api = VideoServiceFactory.get_service(gateway, config)
        
        # 抛出真实或虚拟的任务
        task_id = await video_api.submit_task(prompt, **config)
        logger.info(f"[Task {record_id}] API 提交成功 (Config Keys: {list(config.keys())})，任务 ID: {task_id}")
        
        # 3. 统一轮询状态
        while True:
            await asyncio.sleep(10) # 10s interval
            status_info = await video_api.check_status(task_id)
            status = status_info.get("status")
            
            if status == "success":
                video_url = status_info.get("video_url")
                if not video_url:
                    raise Exception("API 返回成功但未提供视频下载链接")
                
                # 下载到本地（Mock 服务会直接生成虚拟空文件并返回路径）
                file_name = f"video_{record_id}.mp4"
                file_path = os.path.join(download_dir, file_name)
                final_path = await video_api.download_video(video_url, file_path)
                
                logger.info(f"[Task {record_id}] 下载本地成功: {final_path}，准备直传飞书...")
                
                # 判断当前是否处于Mock沙盒生命周期中
                mock_data = status_info.get("mock_data")
                cost_estimate = ""
                gateway_display = gateway
                
                if mock_data:
                    gateway_display = f"{gateway} (MOCK 模拟)"
                    # 算力预估计算
                    token_count = int(len(prompt) * 1.5)
                    llm_cost = (token_count / 1000) * 0.002
                    resolution = config.get("resolution", "720p")
                    res_cost_map = {"480p": 0.8, "720p": 1.5, "1080p": 3.0} if gateway == "wan_2_6" else {"480p": 0.4, "720p": 0.7, "1080p": 1.5}
                    per_sec_cost = res_cost_map.get(resolution, 1.0)
                    video_sec = 5 # 假设视频生成 5 秒
                    video_cost = per_sec_cost * video_sec
                    total_est = llm_cost + video_cost
                    import json
                    cfg_str = json.dumps(config, ensure_ascii=False)
                    cost_estimate = (f"\n> **调度参数:** {cfg_str}\n"
                                     f"> **预估计费:** 视频 {video_sec}秒 ({resolution}), LLM 约 {token_count} Tokens.\n"
                                     f"> **预估金额:** 约 ¥{total_est:.4f} (注：此为模拟金额，不计入实耗)")
                    
                    logger.info(f"🧪 [Task {record_id}] 沙盒模式跳过真实飞书附件上传，已模拟更新状态。")
                    
                    # 补充用户需求: 视觉隔离沙盒数据
                    feishu_manager.update_record(record_id, {"状态": ["已完成(Mock)", "待检查"], "异常日志": "[SANDBOX_TEST] 模拟计费与流程测试完成"})
                    success = True
                else:
                    success = feishu_manager.upload_attachment_and_update_record(record_id, final_path)
                
                break
                
            elif status == "failed":
                err = status_info.get("error", "未知生成错误")
                raise Exception(f"视频服务生成失败: {err}")
            else:
                logger.debug(f"[Task {record_id}] 正在生成中，请耐心等待...")
                
        if success:
            logger.info(f"[Task {record_id}] 飞书打通完毕/Mock已记录！✅")
            
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
        err_prefix = "[SANDBOX_TEST] 模拟生成异常: " if config.get("_sandbox_mode") else ""
        feishu_manager.update_record(record_id, {
            "状态": ["生成异常"], 
            "异常日志": err_prefix + str(e)
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
