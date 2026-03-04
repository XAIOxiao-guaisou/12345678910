import asyncio
from typing import DefaultDict
from collections import defaultdict

class TaskManager:
    """Manages locks and queues for the entire Aiduanju Pipeline"""
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(TaskManager, cls).__new__(cls)
            cls._instance.novel_locks = defaultdict(asyncio.Lock)
            cls._instance.video_submission_sem = asyncio.Semaphore(3)
        return cls._instance
        
    def get_novel_lock(self, novel_id: str) -> asyncio.Lock:
        return self.novel_locks[novel_id]
        
    def get_video_semaphore(self) -> asyncio.Semaphore:
        return self.video_submission_sem

    async def release_deadlocks(self, hours_threshold: float = 2.0):
        """
        v3.0.0-PRO: 定期或管理员手动调用，释放超过阈值的“冻结”分镜。
        增加了远端排队状态双重验证，防止重复引发二次扣费。
        """
        import logging
        from core.services.db_service.feishu_bitable import FeishuBitableManager
        from core.services.video_service.factory import get_video_api
        
        logger = logging.getLogger(__name__)
        bitable = FeishuBitableManager()
        
        stuck_tasks = bitable.get_stuck_frozen_tasks(hours_threshold)
        if not stuck_tasks:
            logger.info("✔️ 当前没有处于冻结死锁状态的任务记录。")
            return
            
        logger.warning(f"🚨 发现 {len(stuck_tasks)} 个处于冻结状态超过 {hours_threshold} 小时的任务，开始执行远端双重校验...")
        
        for task in stuck_tasks:
            rec_id = task["record_id"]
            vid_task_id = task.get("task_id", "")
            gateway = task.get("gateway", "wan_2_6")
            
            should_unlock = True
            
            # 双重验证：尝试核对远端状态
            if vid_task_id:
                try:
                    video_api = get_video_api(gateway)
                    status_info = await video_api.check_status(vid_task_id)
                    remote_status = status_info.get("status", "")
                    # 如果远端仍在排队/生成中，坚决不释放锁
                    if remote_status in ["running", "queued", "pending"]:
                        logger.warning(f"⚠️ 记录 {rec_id} 的远端网关任务 {vid_task_id} {remote_status}，跳过释放！")
                        should_unlock = False
                except Exception as e:
                    # 如果查询直接报错（例如任务过期查不到），认为远端已失效，安全释放
                    logger.info(f"ℹ️ 记录 {rec_id} 远端校验失败 ({e})，视为失效，放行释放。")
            
            if should_unlock:
                bitable.force_unlock_task(rec_id)
