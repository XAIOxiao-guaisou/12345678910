"""
core/services/post_production/__init__.py
v3.0.0 后期制作服务域。

服务列表：
  TTSService         - 语音合成（火山引擎 TTS / Edge-TTS 兜底）
  SFXService         - 音效/配乐生成（Pollinations.ai → 静音兜底）
  AudioOrchestrator  - 音频制作编排器（TTS+SFX+DeepSeek J2 自动生成）
  ffmpeg_service     - 多轨混流（含 FFmpeg 环境预检 + probe_duration）
"""
from .tts_service import TTSService
from .sfx_service import SFXService
from .audio_orchestrator import AudioOrchestrator
from . import ffmpeg_service

__all__ = ["TTSService", "SFXService", "AudioOrchestrator", "ffmpeg_service"]

