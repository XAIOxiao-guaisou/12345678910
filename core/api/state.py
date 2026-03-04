import os
import json
import asyncio
import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)

class StateKeeper:
    """Singleton for safely managing batch task state with asyncio locks and atomic writes."""
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(StateKeeper, cls).__new__(cls)
            cls._instance._batch_tasks = {}
            cls._instance._lock = asyncio.Lock()
            asyncio.run_coroutine_threadsafe(cls._instance._load_from_disk(), asyncio.get_event_loop()) if cls._loop_running() else None
        return cls._instance

    @staticmethod
    def _loop_running() -> bool:
        try:
            loop = asyncio.get_running_loop()
            return loop is not None
        except RuntimeError:
            return False
            
    def _get_filepath(self) -> str:
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "logs", "batch_tasks.json")

    async def _load_from_disk(self):
        async with self._lock:
            filepath = self._get_filepath()
            if os.path.exists(filepath):
                try:
                    with open(filepath, "r", encoding="utf-8") as f:
                        self._batch_tasks = json.load(f)
                    logger.info(f"✅ 从快照成功恢复了 {len(self._batch_tasks)} 个批量任务记录")
                    
                    # 启动时清洗幽灵任务：如果在关机前没跑完，由于内存被清空，统统视作死亡
                    ghost_cleared = 0
                    for tid, t in self._batch_tasks.items():
                        if not t.get("finished", False):
                            t["finished"] = True
                            t["status"] = "error"
                            t["error"] = "进程异常重启/内存丢失，该任务的挂起状态被强制终止，请在新任务中重新投递文本（断点续传会自动跳过已生成的图片）"
                            ghost_cleared += 1
                    
                    if ghost_cleared > 0:
                        logger.warning(f"🧹 启动自检: 发现 {ghost_cleared} 个中断脱机的幽灵任务，已被强行重置为 error 状态以防死锁。")
                        await self._save_to_disk_async() # 持久化这个更正
                        
                except Exception as e:
                    logger.error(f"⚠️ 读取 batch_tasks.json 失败: {e}")
                    self._batch_tasks = {}

    async def _save_to_disk_async(self):
        filepath = self._get_filepath()
        tmp_path = filepath + ".tmp"
        try:
            os.makedirs(os.path.dirname(filepath), exist_ok=True)
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(self._batch_tasks, f, ensure_ascii=False)
            os.replace(tmp_path, filepath)
        except Exception as e:
            logger.error(f"保存 batch_tasks.json 失败: {e}")

    async def get_task(self, task_id: str) -> Dict[str, Any]:
        async with self._lock:
            task = self._batch_tasks.get(task_id)
            return task.copy() if task else None

    async def get_all_active_tasks(self) -> Dict[str, Any]:
        async with self._lock:
            return {k: v.copy() for k, v in self._batch_tasks.items() if not v.get("finished", False)}
            
    async def get_all_tasks(self) -> Dict[str, Any]:
        async with self._lock:
            return {k: v.copy() for k, v in self._batch_tasks.items()}
            
    async def create_task(self, task_id: str, initial_data: Dict[str, Any]):
        async with self._lock:
            self._batch_tasks[task_id] = initial_data
            await self._save_to_disk_async()

    async def update_task(self, task_id: str, updates: Dict[str, Any], force_save: bool = False):
        async with self._lock:
            if task_id in self._batch_tasks:
                self._batch_tasks[task_id].update(updates)
                if force_save:
                    await self._save_to_disk_async()
                    
    async def append_log(self, task_id: str, log_entry: Dict[str, Any], force_save: bool = False):
        async with self._lock:
            if task_id in self._batch_tasks:
                if "log" not in self._batch_tasks[task_id]:
                    self._batch_tasks[task_id]["log"] = []
                self._batch_tasks[task_id]["log"].append(log_entry)
                if len(self._batch_tasks[task_id]["log"]) > 200:
                    self._batch_tasks[task_id]["log"] = self._batch_tasks[task_id]["log"][-200:]
                
                if force_save:
                    await self._save_to_disk_async()

    async def stop_task(self, task_id: str):
        async with self._lock:
            if task_id in self._batch_tasks:
                self._batch_tasks[task_id]["finished"] = True
                self._batch_tasks[task_id]["status"] = "stopped"
                self._batch_tasks[task_id]["cancelled"] = True
                await self._save_to_disk_async()

    async def stop_all_tasks(self) -> list:
        stopped = []
        async with self._lock:
            for tid, t in self._batch_tasks.items():
                if not t.get("finished", False):
                    t["finished"] = True
                    t["status"] = "stopped"
                    t["cancelled"] = True
                    stopped.append(tid)
            if stopped:
                await self._save_to_disk_async()
        return stopped
