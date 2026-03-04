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
