from pydantic import BaseModel

class TaskRequest(BaseModel):
    account: str
    prompt: str
    gateway: str = "seedance-1.5-pro"

class NovelSubmission(BaseModel):
    account: str
    content: str
    style: str = "anime"
    gateway: str = "seedance-1.5-pro"
    llm_temperature: float = 0.7
    top_p: float = 1.0
    chunk_size: int = 1200
    video_params: dict = {}

