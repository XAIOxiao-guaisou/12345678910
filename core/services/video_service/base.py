from abc import ABC, abstractmethod

class VideoGenerationException(Exception):
    """Unified exception for video generation gateway errors."""
    pass

class BaseVideoGeneratorAPI(ABC):
    """
    v2.8.0: 视频生成网关抽象基类。
    image_url 参数从 **kwargs 升级为显式可选参数，固化 I2V 协议契约。
    - 传入 image_url -> 走 I2V（图生视频）端点
    - 省略 image_url -> 走 T2V（文生视频）端点
    """
    def __init__(self, api_key: str):
        self.api_key = api_key

    @abstractmethod
    async def submit_task(self, prompt: str, image_url: str = "", **kwargs) -> str:
        """
        提交生成任务到平台 API。
        Args:
            prompt    : 视频内容描述文字
            image_url : 图生视频首帧图 URL（非空则走 I2V 端点，空字符串则 T2V）
        Returns:
            task_id (str) 供后续状态轮询
        """
        pass

    @abstractmethod
    async def check_status(self, task_id: str) -> dict:
        """
        根据 Task ID 查询生成进度。
        返回: {"status": "processing"|"success"|"failed", "video_url": "...", "error": "..."}
        """
        pass
