"""
core/services/post_production/tts_service.py
v3.0.0: 语音合成引擎 — 三路优先级

优先级顺序：
  1. 阿里云 qwen3-tts-flash（免费配额，统一使用 ALIYUN_API_KEY）
     - 模型：qwen3-tts-flash / cosyvoice-v3.5-flash
     - 接口：dashscope.MultiModalConversation.call()
     - 免费额度：1万字符/90天（汉字=2字符）
     - 音色（qwen3）: Cherry / Samantha / River / Ethan / Serenity / Sunny
     - 音色（cosyvoice）: 龙婉 / 龙橙 等

  2. 火山引擎 TTS（商业可用，音色与剪映一致）
     - 依赖：VOLCENGINE_TTS_APP_ID + VOLCENGINE_TTS_ACCESS_TOKEN

  3. Edge-TTS 兜底（微软 Azure 免费接口，无需 API Key）
     - 依赖：pip install edge-tts

接口：
  tts = TTSService()
  mp3_path = await tts.synthesize(text="你好", output_path="/tmp/out.mp3")
"""
import asyncio
import logging
import os
import base64
import uuid

logger = logging.getLogger(__name__)

# ── qwen3-tts 可用音色（中文推荐）──────────────────────────────────────────
QWEN_TTS_VOICES = {
    "default":  "Cherry",      # 女声，清晰，偏播音
    "female_1": "Cherry",      # 温和女声
    "female_2": "Serenity",    # 成熟女声
    "male_1":   "Ethan",       # 沉稳男声
    "male_2":   "River",       # 活力男声
    "narrator": "Sunny",       # 旁白型
}

# cosyvoice 推荐中文音色
COSYVOICE_VOICES = {
    "default":  "龙婉",
    "male":     "龙橙",
}


class TTSService:
    """
    三路 TTS 引擎：阿里云 qwen3-tts（主路）→ 火山引擎 → Edge-TTS（兜底）。
    实例化时自动探测可用引擎，无需手动配置路由。
    """

    def __init__(
        self,
        voice: str = None,
        aliyun_model: str = "qwen3-tts-flash",   # 或 "cosyvoice-v3.5-flash"
        use_aliyun: bool = None,
        use_volcengine: bool = None,
    ):
        from core.config import settings

        # ── 阿里云 TTS ────────────────────────────────────────────────────
        self.aliyun_api_key      = settings.ALIYUN_API_KEY or settings.DASHSCOPE_API_KEY
        self.aliyun_model        = aliyun_model
        self._is_cosyvoice       = "cosyvoice" in aliyun_model

        if use_aliyun is None:
            self._use_aliyun = bool(self.aliyun_api_key)
        else:
            self._use_aliyun = use_aliyun and bool(self.aliyun_api_key)

        # 音色选择
        if voice:
            self.aliyun_voice = voice
        elif self._is_cosyvoice:
            self.aliyun_voice = COSYVOICE_VOICES["default"]
        else:
            self.aliyun_voice = QWEN_TTS_VOICES["default"]

        # ── 火山引擎 TTS ──────────────────────────────────────────────────
        self.volcengine_app_id = settings.VOLCENGINE_TTS_APP_ID
        self.volcengine_token  = settings.VOLCENGINE_TTS_ACCESS_TOKEN

        if use_volcengine is None:
            self._use_volcengine = bool(self.volcengine_app_id and self.volcengine_token)
        else:
            self._use_volcengine = use_volcengine

        # ── Edge-TTS ──────────────────────────────────────────────────────
        self.edge_voice = getattr(settings, "EDGE_TTS_VOICE", "zh-CN-XiaoxiaoNeural")

        # ── 日志 ──────────────────────────────────────────────────────────
        if self._use_aliyun:
            engine = f"阿里云 {self.aliyun_model} (voice={self.aliyun_voice}, 免费1万字符)"
        elif self._use_volcengine:
            engine = "火山引擎 TTS"
        else:
            engine = f"Edge-TTS ({self.edge_voice})"
        logger.info(f"[TTS] 初始化完成，主引擎: {engine}")

    async def synthesize(
        self,
        text: str,
        output_path: str,
        voice: str = None,
    ) -> str:
        """
        合成语音到本地文件，自动按引擎优先级降级。

        Args:
            text        : 合成文本（汉字=2字符，每集30-60字约需60-120字符）
            output_path : 输出文件路径（.mp3）
            voice       : 覆盖默认音色（可选）

        Returns:
            output_path (str) 成功时，"" 表示文本为空

        Raises:
            RuntimeError: 三路引擎均失败时
        """
        if not text or not text.strip():
            logger.warning("[TTS] 输入文本为空，跳过合成")
            return ""

        out_dir = os.path.dirname(output_path)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)

        # 路由1：阿里云 TTS（免费首选）
        if self._use_aliyun:
            try:
                return await self._aliyun_tts(text, output_path, voice)
            except Exception as e:
                logger.warning(f"[TTS] 阿里云失败，尝试下一路: {e}")

        # 路由2：火山引擎
        if self._use_volcengine:
            try:
                return await self._volcengine_tts(text, output_path, voice)
            except Exception as e:
                logger.warning(f"[TTS] 火山引擎失败，降级 Edge-TTS: {e}")

        # 路由3：Edge-TTS 兜底
        try:
            return await self._edge_tts(text, output_path, voice or self.edge_voice)
        except Exception as e:
            raise RuntimeError(f"[TTS] 三路引擎均失败: {e}") from e

    # ─────────────────────────────────────────────────────────────────────────
    # 引擎1：阿里云 qwen3-tts-flash / cosyvoice
    # ─────────────────────────────────────────────────────────────────────────

    async def _aliyun_tts(self, text: str, output_path: str, voice: str = None) -> str:
        """
        阿里云 TTS 合成（qwen3-tts-flash / cosyvoice-v3.5-flash）。
        使用 DashScope MultiModalConversation API（HTTP，无需 SDK WebSocket）。

        免费额度：1万字符/90天（汉字=2字符）
        音色：Cherry（女，默认）/ Ethan（男）/ Serenity / Sunny / River

        API endpoint（非SDK，纯HTTP避免额外安装）：
          POST https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation
        """
        import aiohttp

        selected_voice = voice or self.aliyun_voice

        # qwen3-tts 专用构造（MultiModal消息格式）
        if not self._is_cosyvoice:
            payload = {
                "model": self.aliyun_model,
                "input": {
                    "messages": [
                        {
                            "role": "user",
                            "content": text,
                        }
                    ]
                },
                "parameters": {
                    "voice": selected_voice,
                    "format": "mp3",
                }
            }
        else:
            # cosyvoice 格式略有不同
            payload = {
                "model": self.aliyun_model,
                "input": {
                    "text": text,
                    "voice": selected_voice,
                },
                "parameters": {
                    "format": "mp3",
                    "sample_rate": 22050,
                }
            }

        headers = {
            "Authorization": f"Bearer {self.aliyun_api_key}",
            "Content-Type":  "application/json",
        }

        timeout = aiohttp.ClientTimeout(total=60)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation",
                headers=headers,
                json=payload,
            ) as resp:
                data = await resp.json()

        # 错误检查
        code = data.get("code", "")
        if code and code not in ("OK", 0, ""):
            raise RuntimeError(f"阿里云 TTS 错误: {data.get('message', data)}")

        # 提取音频 base64（qwen3-tts 返回格式）
        output  = data.get("output", {})
        choices = output.get("choices", [])
        audio_b64 = ""

        for choice in choices:
            content = choice.get("message", {}).get("content", [])
            if isinstance(content, list):
                for item in content:
                    if item.get("type") == "audio" or "audio" in item:
                        audio_b64 = item.get("audio", item.get("data", ""))
                        break
            if audio_b64:
                break

        # 降级查找：有些版本直接在 output.audio
        if not audio_b64:
            audio_b64 = output.get("audio", "")

        if not audio_b64:
            raise RuntimeError(f"阿里云 TTS 无音频数据: {data}")

        audio_bytes = base64.b64decode(audio_b64)
        with open(output_path, "wb") as f:
            f.write(audio_bytes)

        logger.info(
            f"[TTS][Aliyun|{self.aliyun_model}] ✅ 合成完成 "
            f"voice={selected_voice} size={len(audio_bytes)/1024:.1f}KB → {output_path}"
        )
        return output_path

    # ─────────────────────────────────────────────────────────────────────────
    # 引擎2：火山引擎 TTS（商业）
    # ─────────────────────────────────────────────────────────────────────────

    async def _volcengine_tts(self, text: str, output_path: str, voice: str = None) -> str:
        """火山引擎 TTS 合成（HTTP 接口）。"""
        import aiohttp

        endpoint = "https://openspeech.bytedance.com/api/v1/tts"
        headers  = {
            "Authorization": f"Bearer;{self.volcengine_app_id}",
            "Content-Type":  "application/json",
        }
        payload = {
            "app": {
                "appid":   self.volcengine_app_id,
                "token":   self.volcengine_token,
                "cluster": "volcano_tts",
            },
            "user": {"uid": "pipeline_tts"},
            "audio": {
                "voice_type": voice or "zh_female_qingxin",
                "encoding":   "mp3",
                "rate":       24000,
            },
            "request": {
                "reqid":    f"req_{uuid.uuid4().hex[:8]}",
                "text":     text,
                "operation": "query",
            },
        }

        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(endpoint, headers=headers, json=payload) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"火山 TTS HTTP {resp.status}: {await resp.text()}")
                data = await resp.json()
                audio_b64 = data.get("data", "")
                if not audio_b64:
                    raise RuntimeError(f"火山 TTS 空音频: {data}")

        audio_bytes = base64.b64decode(audio_b64)
        with open(output_path, "wb") as f:
            f.write(audio_bytes)

        logger.info(f"[TTS][Volcengine] ✅ 合成完成: {output_path}")
        return output_path

    # ─────────────────────────────────────────────────────────────────────────
    # 引擎3：Edge-TTS 兜底
    # ─────────────────────────────────────────────────────────────────────────

    async def _edge_tts(self, text: str, output_path: str, voice: str) -> str:
        """Edge-TTS 免费兜底（Microsoft Azure，无需 API Key）。"""
        try:
            import edge_tts
        except ImportError:
            raise ImportError("请安装 edge-tts: pip install edge-tts")

        communicate = edge_tts.Communicate(text, voice)
        await communicate.save(output_path)
        logger.info(f"[TTS][Edge] ✅ 合成完成 voice={voice}: {output_path}")
        return output_path
