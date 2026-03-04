from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field
import os

class Settings(BaseSettings):
    # Feishu 必须配置
    FEISHU_APP_ID: str = Field(..., description="飞书应用ID不能为空")
    FEISHU_APP_SECRET: str = Field(default="", description="飞书应用Secret")
    FEISHU_APP_TOKEN_ASSETS: str = "Oj00bIhGVaq1cNsZsJhcMC58ndd"
    FEISHU_TABLE_ASSETS: str = "tblC6L0fO7FXP3fI"
    FEISHU_APP_TOKEN_FACTORY: str = "Cu75bLeuJarqg1s7ysscaNolnPg"
    FEISHU_TABLE_FACTORY: str = "tbloUrdwqG47ZmgI"
    FEISHU_APP_TOKEN_SCRIPT: str = "J7OPbwEHqaJMefs1NLecTvA1n2e"
    FEISHU_TABLE_SCRIPT: str = "tbluFmGLkmqPTd9S"
    FEISHU_APP_TOKEN_MEMORY: str = "XfWibZ0RjaPD1psTFwscKP1VnUb"
    FEISHU_TABLE_MEMORY: str = "tblcFydnJuwD8cIy"

    # LLM & Video API Keys
    DEEPSEEK_API_KEY: str = Field(default="", description="允许为空，可走免费代理")
    SILICONFLOW_API_KEY: str = ""
    VOLCENGINE_API_KEY: str = ""
    ALIYUN_API_KEY: str = ""
    DASHSCOPE_API_KEY: str = ""

    # Pollinations
    POLLINATIONS_API_KEY: str = ""
    POLLINATIONS_KEY: str = ""

    # Aria2
    ARIA2_RPC_URL: str = "http://localhost:6800/jsonrpc"
    ARIA2_RPC_SECRET: str = ""

    # Other
    MOCK_MODE: str = "False"
    WX_BOT_WEBHOOK: str = ""

    # ── Post-Production (v3.0.0) ────────────────────────────────────────────
    # 火山引擎 TTS（可选）
    VOLCENGINE_TTS_APP_ID: str = ""
    VOLCENGINE_TTS_ACCESS_TOKEN: str = ""
    # 阿里云 TTS（免费，复用 ALIYUN_API_KEY）
    ALIYUN_TTS_MODEL: str = "qwen3-tts-flash"     # 或 cosyvoice-v3.5-flash
    ALIYUN_TTS_VOICE: str = "Cherry"              # Cherry/Ethan/Serenity/Sunny/River
    # Edge-TTS 兜底音色（无需 API Key）
    EDGE_TTS_VOICE: str = "zh-CN-XiaoxiaoNeural"
    # FFmpeg 可执行文件路径（如已在系统 PATH 中，保持默认即可）
    FFMPEG_BINARY: str = "ffmpeg"
    # 成片输出目录（相对项目根）
    OUTPUT_DIR: str = "Download/output"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

settings = Settings()
