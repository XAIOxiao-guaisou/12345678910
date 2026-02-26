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
