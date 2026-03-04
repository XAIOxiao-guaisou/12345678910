"""
core/services/image_service/aliyun_image_service.py
v3.0.0: 阿里云百炼文生图全量覆盖

支持模型（有免费配额，优先级从高到低）：
┌─────────────────────────────┬─────────┬────────┬──────────────────────────────┐
│  模型 ID                     │ 免费额度 │ 接口   │  说明                        │
├─────────────────────────────┼─────────┼────────┼──────────────────────────────┤
│ wan2.6-t2i ★推荐            │  50 张  │ 同步   │ 万相2.6，最新旗舰，16:9=1696*960│
│ wan2.5-t2i-preview          │  50 张  │ 异步   │ 万相2.5，灵活尺寸               │
│ wan2.2-t2i-flash            │ 100 张  │ 异步   │ 万相2.2极速                    │
│ wanx2.1-t2i-turbo ✅已有    │ 500 张  │ 异步   │ 万相2.1极速（保持兼容）         │
│ stable-diffusion-3.5-large  │ 500 张  │ 异步   │ SD3.5，目前免费体验             │
└─────────────────────────────┴─────────┴────────┴──────────────────────────────┘

wan2.6-t2i 使用全新 multimodal-generation 同步接口（无需轮询）。
其余模型使用 text2image/image-synthesis 异步接口 + 轮询。
"""
from core.config import settings

import os
import asyncio
import logging
import random
import aiohttp
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

logger = logging.getLogger(__name__)


# ── 模型路由常量 ─────────────────────────────────────────────────────────────
# wan2.6 系列使用全新同步 multimodal-generation 接口
_SYNC_MODELS = {"wan2.6-t2i", "wan2.6-t2i-turbo", "z-image-turbo", "flux-merged"}

# 所有支持的有免费配额模型
FREE_QUOTA_MODELS = {
    "wan2.6-t2i":                {"quota": 50,  "type": "sync",  "desc": "万相2.6 旗舰(推荐)"},
    "z-image-turbo":             {"quota": "?", "type": "sync",  "desc": "z-image-turbo"},
    "flux-merged":               {"quota": 100, "type": "sync",  "desc": "FLUX 融合版"},
    "wan2.5-t2i-preview":        {"quota": 50,  "type": "async", "desc": "万相2.5 灵活尺寸"},
    "wan2.2-t2i-flash":          {"quota": 100, "type": "async", "desc": "万相2.2 极速"},
    "wanx2.1-t2i-turbo":         {"quota": 500, "type": "async", "desc": "万相2.1 极速(大额度)"},
    "wanx2.1-t2i-plus":          {"quota": 500, "type": "async", "desc": "万相2.1 专业版"},
    "stable-diffusion-3.5-large":{"quota": 500, "type": "async", "desc": "SD3.5 大(免费体验)"},
    "stable-diffusion-3.5-large-turbo":{"quota":500,"type":"async","desc":"SD3.5 大Turbo"},
    "stable-diffusion-xl":       {"quota": 500, "type": "async", "desc": "SDXL"},
}

# 推荐分辨率映射（16:9 视频首帧）
_SIZE_PRESETS = {
    "1:1":  "1280*1280",
    "16:9": "1696*960",
    "9:16": "960*1696",
    "4:3":  "1472*1104",
    "3:4":  "1104*1472",
}


class AliyunImageService:
    """
    阿里云百炼全量图像生成客户端（v3.0.0）。
    同时兼容旧版接口（wanx2.1-t2i-turbo）和新版同步接口（wan2.6-t2i）。
    与 PollinationsService 接口兼容（返回 {url, seed, status}）。
    """
    # ── V2 新同步接口（wan2.6-t2i 专用）────────────────────────────────────
    SYNC_URL  = (
        "https://dashscope.aliyuncs.com/api/v1/services/aigc/"
        "multimodal-generation/generation"
    )
    # ── 异步接口（万相2.1/2.2/2.5 + SD）───────────────────────────────────
    ASYNC_SUBMIT_URL = (
        "https://dashscope.aliyuncs.com/api/v1/services/aigc/"
        "text2image/image-synthesis"
    )
    TASK_URL     = "https://dashscope.aliyuncs.com/api/v1/tasks/{task_id}"
    MAX_POLL     = 40      # 最多轮询 40 次
    POLL_INTERVAL= 3       # 秒

    def __init__(self, model: str = "wanx2.1-t2i-turbo"):
        self.model = model
        self.api_key = (
            settings.ALIYUN_API_KEY
            or settings.DASHSCOPE_API_KEY
        )
        self._is_sync  = model in _SYNC_MODELS
        if not self.api_key:
            logger.warning("⚠️ [AliyunImage] ALIYUN_API_KEY 未配置，文生图将失败")
        else:
            mode_tag = "同步" if self._is_sync else "异步"
            info = FREE_QUOTA_MODELS.get(model, {})
            logger.info(
                f"✅ [AliyunImage] 模型={self.model} [{mode_tag}] "
                f"免费额度={info.get('quota','?')}张  {info.get('desc','')}"
            )

    def _headers(self, async_mode: bool = False) -> dict:
        h = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type":  "application/json",
        }
        if async_mode:
            h["X-DashScope-Async"] = "enable"
        return h

    # ─────────────────────────────────────────────────────────────────────────
    # 公共接口（与旧版完全兼容）
    # ─────────────────────────────────────────────────────────────────────────

    async def generate_image(
        self,
        prompt: str,
        seed: int = None,
        width: int = 1024,
        height: int = 1024,
        negative_prompt: str = "low quality, blurry, distorted, watermark, text, logo",
        aspect_ratio: str = "",     # 新增：直接用比例字符串 "16:9" 等
        prompt_extend: bool = True, # wan2.6 智能扩写
    ) -> dict:
        """
        文生图（支持同步 wan2.6 和异步旧版）。

        Returns:
            {"url": str, "seed": int, "status": "success", "model": str}
            {"status": "error", "error": str}
        """
        if not self.api_key:
            return {"status": "error", "error": "ALIYUN_API_KEY 未配置"}

        resolved_seed = seed if seed is not None else random.randint(1, 2**31 - 1)

        # 尺寸处理：优先 aspect_ratio > width/height
        if aspect_ratio and aspect_ratio in _SIZE_PRESETS:
            size_str = _SIZE_PRESETS[aspect_ratio]
        else:
            size_str = f"{width}*{height}"

        if self._is_sync:
            return await self._generate_sync(prompt, resolved_seed, size_str, negative_prompt, prompt_extend)
        else:
            return await self._generate_async(prompt, resolved_seed, size_str, negative_prompt)

    async def evolve_image(
        self,
        original_seed: int,
        evolution_prompt: str,
        width: int = 1024,
        height: int = 1024,
        aspect_ratio: str = "",
    ) -> dict:
        """固定原 seed 进行视觉演进（保留美学风格，更新内容）。"""
        return await self.generate_image(
            prompt=evolution_prompt,
            seed=original_seed,
            width=width,
            height=height,
            aspect_ratio=aspect_ratio,
        )

    # ─────────────────────────────────────────────────────────────────────────
    # 内部：wan2.6 同步接口
    # ─────────────────────────────────────────────────────────────────────────

    async def _generate_sync(
        self,
        prompt: str,
        seed: int,
        size_str: str,
        negative_prompt: str,
        prompt_extend: bool,
    ) -> dict:
        """
        wan2.6-t2i 同步接口 —— 一次请求直接返回结果（无需轮询）。
        Endpoint: POST .../multimodal-generation/generation
        """
        payload = {
            "model": self.model,
            "input": {
                "messages": [
                    {"role": "user", "content": [{"text": prompt}]}
                ]
            },
            "parameters": {
                "size":           size_str,
                "n":              1,
                "seed":           seed,
                "negative_prompt": negative_prompt,
                "prompt_extend":  prompt_extend,
                "watermark":      False,
            }
        }

        try:
            timeout = aiohttp.ClientTimeout(total=120)  # 同步直出，最多等 2 分钟
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    self.SYNC_URL,
                    headers=self._headers(async_mode=False),
                    json=payload,
                ) as resp:
                    data = await resp.json()

            # 错误检查
            if data.get("code") and data["code"] not in ("OK", 0, ""):
                err = data.get("message", str(data))
                logger.error(f"[AliyunImage|sync] 失败: {err}")
                return {"status": "error", "error": err}

            # 新接口结果在 output.choices[0].message.content[0].image_url.url
            output = data.get("output", {})
            choices = output.get("choices", [])
            if choices:
                content = choices[0].get("message", {}).get("content", [])
                for item in content:
                    if item.get("image_url"):
                        img_url = item["image_url"].get("url", "")
                        if img_url:
                            logger.info(f"✅ [AliyunImage|sync|{self.model}] seed={seed} url={img_url[:60]}...")
                            return {"url": img_url, "seed": seed, "status": "success", "model": self.model}
                    elif item.get("image"):
                        img_url = item.get("image", "")
                        if isinstance(img_url, str) and img_url.startswith("http"):
                            logger.info(f"✅ [AliyunImage|sync|{self.model}] seed={seed} url={img_url[:60]}...")
                            return {"url": img_url, "seed": seed, "status": "success", "model": self.model}

            # 也可能在 output.results（某些版本）
            results = output.get("results", [])
            if results:
                img_url = results[0].get("url", "")
                if img_url:
                    logger.info(f"✅ [AliyunImage|sync|{self.model}] seed={seed} url={img_url[:60]}...")
                    return {"url": img_url, "seed": seed, "status": "success", "model": self.model}

            return {"status": "error", "error": f"同步接口无结果: {data}"}

        except Exception as e:
            logger.error(f"❌ [AliyunImage|sync] 异常: {e}")
            return {"status": "error", "error": str(e)}

    # ─────────────────────────────────────────────────────────────────────────
    # 内部：异步接口（万相2.5以下 + SD）
    # ─────────────────────────────────────────────────────────────────────────

    async def _generate_async(
        self,
        prompt: str,
        seed: int,
        size_str: str,
        negative_prompt: str,
    ) -> dict:
        """
        标准异步接口：提交任务 → 轮询 task_id → 返回结果。
        """
        payload = {
            "model": self.model,
            "input": {
                "prompt":          prompt,
                "negative_prompt": negative_prompt,
            },
            "parameters": {
                "size": size_str,
                "seed": seed,
                "n":    1,
            }
        }

        try:
            async with aiohttp.ClientSession() as session:
                # ① 提交任务
                async with session.post(
                    self.ASYNC_SUBMIT_URL,
                    headers=self._headers(async_mode=True),
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    data = await resp.json()

                if data.get("code") and data["code"] not in ("OK", 0, ""):
                    err = data.get("message", str(data))
                    logger.error(f"[AliyunImage|async] 提交失败: {err}")
                    return {"status": "error", "error": err}

                task_id = data.get("output", {}).get("task_id")
                if not task_id:
                    return {"status": "error", "error": f"未获取到 task_id: {data}"}

                logger.info(f"🎨 [AliyunImage|{self.model}] 任务已提交 task_id={task_id}")

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
                        logger.warning(f"[AliyunImage] 轮询异常 strike={strike_count}: {e}")
                        if strike_count >= 5:
                            return {"status": "error", "error": f"连续轮询失败: {e}"}
                        await asyncio.sleep(wait)
                        continue

                    output      = poll_data.get("output", {})
                    task_status = output.get("task_status", "UNKNOWN")

                    if task_status == "SUCCEEDED":
                        results = output.get("results", [])
                        if results:
                            img_url = results[0].get("url", "")
                            logger.info(
                                f"✅ [AliyunImage|{self.model}] seed={seed} "
                                f"url={img_url[:60]}..."
                            )
                            return {"url": img_url, "seed": seed, "status": "success", "model": self.model}
                        return {"status": "error", "error": "SUCCEEDED 但无 results"}

                    elif task_status in ("FAILED", "CANCELED"):
                        err = output.get("message", "未知错误")
                        logger.error(f"❌ [AliyunImage|{self.model}] 任务失败: {err}")
                        return {"status": "error", "error": err}

                    elif task_status in ("PENDING", "RUNNING"):
                        logger.debug(f"⏳ [AliyunImage] 轮询 {attempt+1}/{self.MAX_POLL} 状态={task_status}")
                    else:
                        logger.warning(f"[AliyunImage] 未知状态: {task_status}")

                return {"status": "error", "error": f"轮询超时 ({self.MAX_POLL}次)"}

        except Exception as e:
            logger.error(f"❌ [AliyunImage] 异常: {e}")
            return {"status": "error", "error": str(e)}
