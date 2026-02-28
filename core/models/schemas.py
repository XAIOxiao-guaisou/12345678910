from pydantic import BaseModel, validator
from typing import Optional, Dict, Any, List
import re


class TaskRequest(BaseModel):
    account: str
    prompt: str
    gateway: str = "seedance-1.5-pro"


class NovelSubmission(BaseModel):
    account: str
    content: str
    style: str = "anime"
    gateway: str = "seedance-1.5-pro"
    novel_id: Optional[str] = ""
    video_params: Dict[str, Any] = {}
    llm_temperature: float = 0.7
    top_p: float = 0.9
    chunk_size: int = 1000
    sandbox_mode: bool = True


class UpdatePresetRequest(BaseModel):
    gateway: str
    preset_name: str
    video_params: Dict[str, Any] = {}


class RerouteRequest(BaseModel):
    record_id: str


# -------------------------------------------------------
# v2.6.0: 批量文件上传模型
# -------------------------------------------------------
class FileItem(BaseModel):
    """单个章节文件"""
    name: str       # 文件名，用作 chapter_name（断点续传 Key）
    content: str    # 文件内容


class BatchNovelSubmission(BaseModel):
    """
    批量多文件上传请求体。
    novel_id 为强制输入，是数据隔离、断点续传、Aria2c 文件命名的唯一 Key。
    """
    novel_id: str               # 强制必填，用户输入项目名
    files: List[FileItem]       # 按章节顺序排列的文件列表
    style: str = "anime"
    gateway: str = "wan_2_6"
    video_params: Dict[str, Any] = {}
    sandbox_mode: bool = True
    chunk_size: int = 1000

    @validator("novel_id")
    def validate_novel_id(cls, v):
        """
        防止特殊字符进入文件路径和飞书 filter。
        只允许中文、字母、数字、下划线，且不超过 32 字符。
        """
        v = v.strip()
        if not v:
            raise ValueError("novel_id 不能为空，请输入项目名称")
        # 更加严谨的安全过滤：仅允许中英文、数字、下划线及连字符
        if not re.match(r'^[\w\u4e00-\u9fa5\-]{1,32}$', v):
            raise ValueError(
                "novel_id 含有非法字符。为保证路径安全，只允许中文、字母、数字、连字符与下划线，且不超过 32 字符"
            )
        return v

    @validator("files")
    def validate_files(cls, v):
        if not v:
            raise ValueError("files 不能为空")
        if len(v) > 50:
            raise ValueError("单次批量上传最多支持 50 个文件")
        return v
