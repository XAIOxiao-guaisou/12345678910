"""
core/services/video_service/aliyun_service.py
v3.0.0: 阿里云 DashScope 视频生成全量覆盖

支持模型（有免费配额）：
┌──────────────────────┬────────┬──────────┬──────────────────────────────────────────┐
│  模型 ID              │  类型  │ 免费额度  │  特性                                    │
├──────────────────────┼────────┼──────────┼──────────────────────────────────────────┤
│ wan2.6-i2v-flash ★   │  I2V  │  50秒    │  极速+配音，I2V首选                      │
│ wan2.6-i2v            │  I2V  │  50秒    │  标准+配音，多镜头                       │
│ wan2.6-t2v            │  T2V  │  50秒    │  文生视频+配音，多镜头                   │
│ wan2.5-i2v-preview    │  I2V  │  50秒    │  2.5 preview，支持配音                   │
│ wan2.5-t2v-preview    │  T2V  │  50秒    │  2.5 preview，支持配音                   │
│ wan2.2-i2v-flash      │  I2V  │  50秒    │  2.2极速                                 │
│ wan2.2-i2v-plus       │  I2V  │  50秒    │  2.2专业                                 │
│ wan2.2-t2v-plus       │  T2V  │  50秒    │  2.2专业                                 │
│ wanx2.1-i2v-turbo     │  I2V  │ 200秒    │  2.1极速，大额度                         │
│ wanx2.1-i2v-plus      │  I2V  │ 200秒    │  2.1专业                                 │
│ wanx2.1-t2v-turbo     │  T2V  │ 200秒    │  2.1极速                                 │
│ wanx2.1-t2v-plus      │  T2V  │ 200秒    │  2.1专业                                 │
└──────────────────────┴────────┴──────────┴──────────────────────────────────────────┘

wan2.6/2.5/2.2 系列: endpoint = video-synthesis（同 v2.8 现有实现）
wanx2.1 系列:        endpoint = video-synthesis（同 model name 格式即可区分）

wan2.6+ 新增特性:
  audio: bool  - 是否自动生成 AI 配音（默认 True，为有声视频计费）
              audio=True  → 720P 0.3元/秒
              audio=False → 720P 0.15元/秒
  resolution: "720P" | "1080P" | "480P"
"""
from core.config import settings

import os
import logging
import asyncio
import aiohttp
from core.services.video_service.base import BaseVideoGeneratorAPI

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

logger = logging.getLogger(__name__)

# ── wan2.6+ 支持自动配音；wan2.2 及以下不支持 ─────────────────────────────
_AUDIO_CAPABLE_MODELS = {
    "wan2.6-i2v", "wan2.6-i2v-flash",
    "wan2.6-t2v",
    "wan2.5-i2v-preview", "wan2.5-t2v-preview",
}

# ── I2V 模型（需要 img_url 参数）─────────────────────────────────────────
_I2V_MODELS = {
    "wan2.6-i2v", "wan2.6-i2v-flash",
    "wan2.5-i2v-preview",
    "wan2.2-i2v-flash", "wan2.2-i2v-plus",
    "wanx2.1-i2v-turbo", "wanx2.1-i2v-plus",
    # 保持向后兼容：旧版 wan2.6 写法
    "wan2.6",
}


class Wan2_6VideoAPI(BaseVideoGeneratorAPI):
    """
    阿里云 DashScope 万相视频生成 API（全量模型覆盖）。
    支持 I2V（图生视频）与 T2V（文生视频），wan2.6+ 支持自动配音。
    """

    SUBMIT_URL = (
        "https://dashscope.aliyuncs.com/api/v1/services/aigc/"
        "video-generation/video-synthesis"
    )
    TASK_URL = "https://dashscope.aliyuncs.com/api/v1/tasks/{task_id}"

    def __init__(
        self,
        api_key: str = None,
        model: str = "wan2.6-i2v-flash",
        audio: bool = False,         # wan2.6+ 自动配音（默认关闭，节省配额）
        resolution: str = "720P",    # "480P" | "720P" | "1080P"
    ):
        self.model      = model
        self.audio      = audio and (model in _AUDIO_CAPABLE_MODELS)
        self.resolution = resolution
        self.api_key    = (
            api_key
            or settings.ALIYUN_API_KEY
            or settings.DASHSCOPE_API_KEY
        )
        if not self.api_key:
            logger.warning(f"⚠️ [{self.model}] ALIYUN_API_KEY 未配置，视频生成将失败")
        else:
            audio_tag = f"| audio={'ON' if self.audio else 'OFF'}" if model in _AUDIO_CAPABLE_MODELS else ""
            logger.info(f"✅ [{self.model}] API Key 已加载 {audio_tag}")

        self.is_i2v = model in _I2V_MODELS

        self._headers = {
            "X-DashScope-Async": "enable",
            "Authorization":     f"Bearer {self.api_key}",
            "Content-Type":      "application/json",
        }

    async def submit_task(
        self,
        prompt: str,
        image_url: str = "",
        **kwargs,
    ) -> str:
        """
        提交视频生成任务。

        Args:
            prompt    : 文字描述
            image_url : I2V 模式必须提供（图片公开 URL 或 base64）

        Returns:
            task_id (str) 供后续状态轮询

        Raises:
            ValueError : I2V 模式未提供 image_url
        """
        if self.is_i2v:
            if not image_url:
                raise ValueError(f"[{self.model}] I2V 模式必须提供 image_url 参数")
            input_payload: dict = {
                "img_url": image_url,
                "prompt":  prompt,
            }
        else:
            input_payload = {"prompt": prompt}

        params: dict = {}
        # 分辨率（wan2.2+ 支持）
        if "resolution" in kwargs:
            params["resolution"] = kwargs["resolution"]
        elif self.resolution:
            params["resolution"] = self.resolution

        # 视频时长
        if "duration" in kwargs:
            params["duration"] = kwargs["duration"]

        # wan2.6+ 自动配音
        if self.model in _AUDIO_CAPABLE_MODELS:
            params["audio"] = self.audio

        payload = {
            "model":      self.model,
            "input":      input_payload,
            "parameters": params,
        }

        timeout = aiohttp.ClientTimeout(total=60)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                self.SUBMIT_URL, headers=self._headers, json=payload
            ) as resp:
                data = await resp.json()

        code = data.get("code", "")
        if code and code not in ("OK", 0, ""):
            raise Exception(f"DashScope Submit Error [{self.model}]: {data}")

        task_id = data.get("output", {}).get("task_id", "")
        if not task_id:
            raise Exception(f"DashScope 未返回 task_id [{self.model}]: {data}")

        logger.info(f"[{self.model}] 任务已提交 task_id={task_id}")
        return task_id

    async def check_status(self, task_id: str) -> dict:
        """
        查询任务状态。

        Returns:
            {
              "status": "succeeded"|"failed"|"running",
              "video_url": str,
              "audio_url": str,   # wan2.6+ 有声视频会额外提供
            }
        """
        url = self.TASK_URL.format(task_id=task_id)
        headers = {"Authorization": f"Bearer {self.api_key}"}

        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, headers=headers) as resp:
                data = await resp.json()

        output      = data.get("output", {})
        status_code = output.get("task_status", "UNKNOWN").upper()

        if status_code == "SUCCEEDED":
            video_url = (
                output.get("video_url")
                or output.get("results", [{}])[0].get("video_url", "")
            )
            # wan2.6+ 有声视频
            audio_url = output.get("audio_url", "")
            result = {"status": "succeeded", "video_url": video_url}
            if audio_url:
                result["audio_url"] = audio_url
                logger.info(f"[{self.model}] 有声视频额外提供 audio_url")
            return result
        elif status_code == "FAILED":
            return {"status": "failed", "error": output.get("message", "Unknown error")}
        elif status_code in ("PENDING", "RUNNING", "QUEUED"):
            return {"status": "running"}
        else:
            return {"status": "unknown", "raw": status_code}
