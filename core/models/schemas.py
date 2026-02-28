from pydantic import BaseModel
from typing import Optional, Dict, Any

class TaskRequest(BaseModel):
    account: str
    prompt: str
    gateway: str = "seedance-1.5-pro"
    sandbox_mode: bool = False

class NovelSubmission(BaseModel):
    account: str
    content: str
    style: str = "anime"
    gateway: str = "seedance-1.5-pro"
    llm_temperature: float = 0.7
    top_p: float = 1.0
    chunk_size: int = 1200
    video_params: dict = {}
    sandbox_mode: bool = False

class UpdatePresetRequest(BaseModel):
    gateway: str
    preset_name: str = "standard"
    video_params: dict

class RerouteRequest(BaseModel):
    record_id: str
