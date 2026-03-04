"""
core/services/image_service/flux_image_service.py
v3.0.0: 阿里云百炼 FLUX 系列文生图客户端

支持模型（有免费配额）：
  flux-merged  ★推荐  100张 免费体验（结合flux-dev深度+schnell速度）
  flux-dev          100张 免费体验（开发版，非商业）
  flux-schnell      100张 免费体验（轻量快速版）

API 文档：
  https://help.aliyun.com/zh/model-studio/flux-api-reference/
  POST https://dashscope.aliyuncs.com/api/v1/services/aigc/image2image/image-synthesis
  X-DashScope-Async: enable
  → task_id → GET .../tasks/{task_id}

注意：FLUX endpoint 是 image2image/image-synthesis（与 wanx 的 text2image 不同）
注意：FLUX 不支持 seed 参数（API 规范）
"""
from core.config import settings

import asyncio
import logging
import random
import aiohttp

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

logger = logging.getLogger(__name__)


class FluxImageService:
    """
    阿里云 FLUX 系列图像生成客户端。
    接口与 AliyunImageService 兼容（返回 {url, seed, status}）。
    """

    SUBMIT_URL = (
        "https://dashscope.aliyuncs.com/api/v1/services/aigc/"
        "image2image/image-synthesis"
    )
    TASK_URL      = "https://dashscope.aliyuncs.com/api/v1/tasks/{task_id}"
    MAX_POLL      = 40
    POLL_INTERVAL = 4  # FLUX 生成较慢，间隔稍长

    FREE_QUOTA_MODELS = {
        "flux-merged":  {"quota": 100, "desc": "FLUX 融合版(推荐，深度+速度)"},
        "flux-dev":     {"quota": 100, "desc": "FLUX 开发版(高质量,非商业)"},
        "flux-schnell": {"quota": 100, "desc": "FLUX 快速版(轻量)"},
    }

    def __init__(self, model: str = "flux-merged"):
        self.model = model
        self.api_key = (
            settings.ALIYUN_API_KEY
            or settings.DASHSCOPE_API_KEY
        )
        if not self.api_key:
            logger.warning("⚠️ [FluxImage] ALIYUN_API_KEY 未配置，FLUX 生图将失败")
        else:
            info = self.FREE_QUOTA_MODELS.get(model, {})
            logger.info(
                f"✅ [FluxImage] 模型={self.model} "
                f"免费额度={info.get('quota','?')}张  {info.get('desc','')}"
            )

    def _headers(self) -> dict:
        return {
            "Authorization":    f"Bearer {self.api_key}",
            "Content-Type":     "application/json",
            "X-DashScope-Async": "enable",
        }

    async def generate_image(
        self,
        prompt: str,
        seed: int = None,
        width: int = 1024,
        height: int = 1024,
        negative_prompt: str = "low quality, blurry, distorted, watermark",
        aspect_ratio: str = "",
    ) -> dict:
        """
        FLUX 文生图（异步轮询）。

        Args:
            prompt         : 正向提示词（建议英文，FLUX 英文效果更好）
            seed           : FLUX API 不支持 seed，此参数仅用于记录（不传给 API）
            width/height   : 图像尺寸（优先 aspect_ratio）
            negative_prompt: 反向提示词
            aspect_ratio   : 快捷比例（"16:9", "1:1" 等）

        Returns:
            {"url": str, "seed": int, "status": "success", "model": str}
            {"status": "error", "error": str}
        """
        if not self.api_key:
            return {"status": "error", "error": "ALIYUN_API_KEY 未配置"}

        # FLUX 不传 seed，用随机数记录（仅追踪用）
        resolved_seed = seed if seed is not None else random.randint(1, 2**31 - 1)

        _SIZE_PRESETS = {
            "1:1":  "1024*1024",
            "16:9": "1664*936",
            "9:16": "936*1664",
            "4:3":  "1280*960",
            "3:4":  "960*1280",
        }
        if aspect_ratio and aspect_ratio in _SIZE_PRESETS:
            w_str, h_str = _SIZE_PRESETS[aspect_ratio].split("*")
            width, height = int(w_str), int(h_str)

        payload = {
            "model": self.model,
            "input": {
                "prompt":          prompt,
                "negative_prompt": negative_prompt,
            },
            "parameters": {
                "size": f"{width}*{height}",
                "n":    1,
            }
        }

        try:
            async with aiohttp.ClientSession() as session:
                # ① 提交任务
                async with session.post(
                    self.SUBMIT_URL,
                    headers=self._headers(),
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    data = await resp.json()

                if data.get("code") and data["code"] not in ("OK", 0, ""):
                    err = data.get("message", str(data))
                    logger.error(f"[FluxImage] 提交失败: {err}")
                    return {"status": "error", "error": err}

                task_id = data.get("output", {}).get("task_id")
                if not task_id:
                    return {"status": "error", "error": f"未获取到 task_id: {data}"}

                logger.info(f"🎨 [FluxImage|{self.model}] 任务已提交 task_id={task_id}")

                # ② 轮询结果
                check_headers = {"Authorization": f"Bearer {self.api_key}"}
                strike_count  = 0

                for attempt in range(self.MAX_POLL):
                    await asyncio.sleep(self.POLL_INTERVAL)
                    try:
                        async with session.get(
                            self.TASK_URL.format(task_id=task_id),
                            headers=check_headers,
                            timeout=aiohttp.ClientTimeout(total=20),
                        ) as poll_resp:
                            poll_data = await poll_resp.json()
                            strike_count = 0
                    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                        strike_count += 1
                        wait = min(5 * (2 ** strike_count), 60)
                        if strike_count >= 5:
                            return {"status": "error", "error": f"轮询失败: {e}"}
                        await asyncio.sleep(wait)
                        continue

                    output      = poll_data.get("output", {})
                    task_status = output.get("task_status", "UNKNOWN")

                    if task_status == "SUCCEEDED":
                        results = output.get("results", [])
                        if results:
                            img_url = results[0].get("url", "")
                            logger.info(
                                f"✅ [FluxImage|{self.model}] seed(tracked)={resolved_seed} "
                                f"url={img_url[:60]}..."
                            )
                            return {
                                "url":    img_url,
                                "seed":   resolved_seed,
                                "status": "success",
                                "model":  self.model,
                            }
                        return {"status": "error", "error": "SUCCEEDED 但无 results"}

                    elif task_status in ("FAILED", "CANCELED"):
                        err = output.get("message", "未知错误")
                        logger.error(f"❌ [FluxImage|{self.model}] 失败: {err}")
                        return {"status": "error", "error": err}

                    elif task_status in ("PENDING", "RUNNING"):
                        logger.debug(f"⏳ [FluxImage] 轮询 {attempt+1}/{self.MAX_POLL}")

                return {"status": "error", "error": f"轮询超时 ({self.MAX_POLL}次)"}

        except Exception as e:
            logger.error(f"❌ [FluxImage] 异常: {e}")
            return {"status": "error", "error": str(e)}

    async def evolve_image(
        self,
        original_seed: int,
        evolution_prompt: str,
        width: int = 1024,
        height: int = 1024,
    ) -> dict:
        """与 AliyunImageService 接口兼容（FLUX 不实际使用 seed 演进）。"""
        return await self.generate_image(
            prompt=evolution_prompt,
            seed=original_seed,
            width=width,
            height=height,
        )
