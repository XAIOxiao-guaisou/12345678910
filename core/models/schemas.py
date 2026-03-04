from pydantic import BaseModel, validator, Field
from typing import Optional, Dict, Any, List
import re

# ==========================================
# --- 基础请求域 (API Requests) ---
# ==========================================

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
    image_gateway: str = "aliyun"
    video_params: Dict[str, Any] = {}
    sandbox_mode: bool = True
    chunk_size: int = 1000
    memory_lock: bool = False

    @validator("novel_id")
    def validate_novel_id(cls, v):
        """
        防止特殊字符进入文件路径和飞书 filter。
        只允许中文、字母、数字、下划线，且不超过 32 字符。
        """
        v = v.strip()
        if not v:
            raise ValueError("novel_id 不能为空，请输入项目名称")
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


# ==========================================
# --- 记忆路由域 (Memory & Routing) ---
# ==========================================

class MemoryState(BaseModel):
    """长文本记忆聚合"""
    world_lore: str = Field(default="", description="世界观与背景设定")
    character_profiles: Dict[str, str] = Field(default_factory=dict, description="角色档案及外观")
    style_guideline: str = Field(default="", description="视觉与气氛定调")


class Stage1Result(BaseModel):
    """阶段一(提取)输出"""
    is_success: bool = True
    memory: MemoryState = Field(default_factory=MemoryState)
    raw_response: str = ""


# ==========================================
# --- 视频网关域 (Video Gateway) ---
# ==========================================

class VisualAsset(BaseModel):
    """底层图像/视觉资产模型"""
    image_url: Optional[str] = None
    prompt_used: str = ""
    gateway_name: str = ""


class VideoTaskState(BaseModel):
    """单一视频任务状态"""
    task_id: str
    status: str = "PENDING"
    video_url: Optional[str] = None
    error_msg: Optional[str] = None


# ==========================================
# --- 任务状态域 (Task State) ---
# ==========================================

class BatchTaskState(BaseModel):
    """批处理任务整体进度状态"""
    novel_id: str
    total_chapters: int = 0
    completed_chapters: int = 0
    status: str = "INIT"
    details: Dict[str, Any] = Field(default_factory=dict)
