from pydantic import BaseModel, Field
from typing import Literal

class Wan26Params(BaseModel):
    gateway: Literal["wan_2_6"]
    resolution: Literal["480p", "720p", "1080p"] = Field(default="720p", description="视频分辨率")
    fps: int = Field(default=24, ge=15, le=30, description="生成帧率")
    sampling_steps: int = Field(default=50, ge=20, le=100, description="采样引擎步数")
    cfg_scale: float = Field(default=7.0, ge=1.0, le=15.0, description="提示词服从度")

