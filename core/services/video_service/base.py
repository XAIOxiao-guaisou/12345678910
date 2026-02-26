from abc import ABC, abstractmethod

class BaseVideoGeneratorAPI(ABC):
    def __init__(self, api_key: str):
        self.api_key = api_key

    @abstractmethod
    async def submit_task(self, prompt: str, **kwargs) -> str:
        """
        提交生成任务到平台 API
        返回: Task ID (字符串)
        """
        pass

    @abstractmethod
    async def check_status(self, task_id: str) -> dict:
        """
        根据 Task ID 查询生成进度
        返回: {"status": "processing"|"success"|"failed", "video_url": "...", "error": "..."}
        """
        pass
