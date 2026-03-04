from core.config import settings
"""
aria2c_service.py — v2.6.0 Aria2c 断点续传下载服务

功能:
  1. is_healthy()    : 调用 Aria2 RPC 检测服务是否可用（启动时调用）
  2. download()      : 通过 Aria2 RPC 提交下载任务，返回 gid
  3. wait_complete() : 轮询任务直到完成，返回本地绝对路径
  4. download_sync() : 同步封装（供 pipeline 非 async 路径调用）

设计原则:
  - 所有路径强制 os.path.abspath() 转换（Aria2 为独立进程，相对路径可能错位）
  - 若 Aria2 不可用，调用方可 fallback 至 aiohttp（不降级功能，仅降级下载能力）
  - 文件名包含 novel_id + episode_num，防止多用户并发冲突

环境变量:
  ARIA2_RPC_URL    Aria2c RPC 地址（默认 http://localhost:6800/jsonrpc）
  ARIA2_RPC_SECRET Aria2c RPC 密钥（默认空）
"""

import os
import json
import logging
import asyncio
import time
import aiohttp

logger = logging.getLogger(__name__)

ARIA2_RPC_URL    = settings.ARIA2_RPC_URL
ARIA2_RPC_SECRET = settings.ARIA2_RPC_SECRET

# 默认下载根目录（绝对路径）
_BASE_DOWNLOAD_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "Download")
)

# ------------------------------------------------------------------
# 工具函数
# ------------------------------------------------------------------
def _rpc_payload(method: str, params: list, req_id: int = 1) -> dict:
    """构造 JSON-RPC 2.0 请求体"""
    p = [f"token:{ARIA2_RPC_SECRET}"] + params if ARIA2_RPC_SECRET else params
    return {"jsonrpc": "2.0", "id": req_id, "method": method, "params": p}


async def _rpc_call(session: aiohttp.ClientSession, method: str, params: list) -> dict:
    """发送 RPC 请求并返回 result 字段"""
    payload = _rpc_payload(method, params)
    async with session.post(ARIA2_RPC_URL, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
        body = await resp.json()
        if "error" in body:
            raise RuntimeError(f"Aria2 RPC[{method}] 错误: {body['error']}")
        return body.get("result", {})


# ------------------------------------------------------------------
# 健康检查（启动时调用一次）
# ------------------------------------------------------------------
async def is_healthy() -> bool:
    """
    检查 Aria2c RPC 是否可用。
    返回 True 表示可用，False 表示不可用（调用方应 fallback 至 aiohttp）。
    """
    try:
        async with aiohttp.ClientSession() as session:
            result = await _rpc_call(session, "aria2.getVersion", [])
            version = result.get("version", "unknown")
            logger.info(f"✅ [Aria2c] 服务可用，版本: {version}, RPC: {ARIA2_RPC_URL}")
            return True
    except Exception as e:
        logger.warning(f"⚠️ [Aria2c] 服务不可用 ({e})，下载将 fallback 至 aiohttp（无断点续传）")
        return False


# ------------------------------------------------------------------
# 提交下载任务
# ------------------------------------------------------------------
async def download(
    url: str,
    novel_id: str = "",
    episode_num: int = 0,
    download_dir: str = "",
) -> str:
    """
    向 Aria2c 提交下载任务。

    Args:
        url:          视频/资产 URL
        novel_id:     用于文件名中的项目标识（防多用户冲突）
        episode_num:  集数/序号（用于文件名中的排序标识）
        download_dir: 下载绝对路径（为空则使用默认 Download 目录）

    Returns:
        Aria2 gid 字符串（用于后续状态轮询）

    强制 abspath：Aria2c 是独立进程，相对路径会解析到其工作目录，而非 FastAPI 的工作目录。
    """
    # ── 文件名规范化：novel_id + 集数 + 时间戳防碰撞
    ts = int(time.time()) % 100000
    safe_id = (novel_id or "novel")[:16].replace(" ", "_")
    padded_ep = str(episode_num).zfill(3)
    out_filename = f"{safe_id}_{padded_ep}_{ts}.mp4"

    # ── 绝对路径隔离（强制为每部小说建立独立下载集目录，并且规避特殊字符引起Aria报错）
    base_dir = os.path.abspath(download_dir) if download_dir else _BASE_DOWNLOAD_DIR
    target_dir = os.path.join(base_dir, safe_id)
    os.makedirs(target_dir, exist_ok=True)

    options = {
        "dir":      target_dir,      # 📌必须是已创建的绝对路径
        "out":      out_filename,
        "continue": "true",          # 断点续传
        "max-connection-per-server": "4",
    }

    try:
        async with aiohttp.ClientSession() as session:
            gid = await _rpc_call(session, "aria2.addUri", [[url], options])
            local_path = os.path.join(base_dir, out_filename)
            logger.info(f"📥 [Aria2c] 任务提交: gid={gid}, 目标={local_path}")
            return str(gid)
    except Exception as e:
        logger.error(f"[Aria2c] 提交下载任务失败: {e}")
        raise


# ------------------------------------------------------------------
# 轮询等待完成
# ------------------------------------------------------------------
async def wait_complete(
    gid: str,
    timeout_sec: int = 600,
    poll_interval: float = 3.0,
) -> str:
    """
    轮询 Aria2c 任务直到完成或超时。

    Returns:
        绝对路径（成功时）
    Raises:
        RuntimeError（失败/超时时）
    """
    deadline = time.time() + timeout_sec
    async with aiohttp.ClientSession() as session:
        while time.time() < deadline:
            status_info = await _rpc_call(session, "aria2.tellStatus",
                                          [gid, ["status", "files", "errorMessage"]])
            state = status_info.get("status", "")
            if state == "complete":
                files = status_info.get("files", [{}])
                local_path = files[0].get("path", "") if files else ""
                local_path = os.path.abspath(local_path) if local_path else ""
                logger.info(f"✅ [Aria2c] gid={gid} 下载完成: {local_path}")
                return local_path
            elif state in ("error", "removed"):
                err = status_info.get("errorMessage", "未知错误")
                raise RuntimeError(f"[Aria2c] gid={gid} 下载失败: {err}")
            logger.debug(f"[Aria2c] gid={gid} 状态={state}，继续等待…")
            await asyncio.sleep(poll_interval)
    raise RuntimeError(f"[Aria2c] gid={gid} 下载超时（{timeout_sec}s）")


# ------------------------------------------------------------------
# aiohttp 降级下载（Aria2c 不可用时的备选方案）
# ------------------------------------------------------------------
async def fallback_download(
    url: str,
    novel_id: str = "",
    episode_num: int = 0,
    download_dir: str = "",
) -> str:
    """
    当 Aria2c 不可用时，使用 aiohttp 流式下载（不支持断点续传）。
    路径规则与 download() 保持一致（绝对路径+规范文件名）。
    """
    ts = int(time.time()) % 100000
    safe_id = (novel_id or "novel")[:16].replace(" ", "_")
    padded_ep = str(episode_num).zfill(3)
    out_filename = f"{safe_id}_{padded_ep}_{ts}.mp4"

    base_dir = os.path.abspath(download_dir) if download_dir else _BASE_DOWNLOAD_DIR
    target_dir = os.path.join(base_dir, safe_id)
    os.makedirs(target_dir, exist_ok=True)
    file_path = os.path.join(target_dir, out_filename)

    logger.warning(f"[aiohttp fallback] 降级下载: {url} → {file_path}")
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=300)) as resp:
                if resp.status == 200:
                    with open(file_path, "wb") as f:
                        async for chunk in resp.content.iter_chunked(1024 * 256):
                            f.write(chunk)
                    logger.info(f"✅ [aiohttp fallback] 下载完成: {file_path}")
                    return file_path
                else:
                    raise RuntimeError(f"HTTP {resp.status}")
    except Exception as e:
        logger.error(f"[aiohttp fallback] 下载失败: {e}")
        raise


# ------------------------------------------------------------------
# 统一入口（自动选择 Aria2c 或 fallback）
# ------------------------------------------------------------------
class Aria2cService:
    """
    统一下载服务入口。
    在 PipelineOrchestrator 初始化时调用 Aria2cService.init()，
    缓存 is_healthy 结果，之后所有 download 调用自动路由。
    """
    _aria2_available: bool = False

    @classmethod
    async def init(cls):
        """启动时调用一次，检测 Aria2c 可用性并缓存结果"""
        cls._aria2_available = await is_healthy()

    @classmethod
    async def smart_download(
        cls,
        url: str,
        novel_id: str = "",
        episode_num: int = 0,
        download_dir: str = "",
    ) -> str:
        """
        智能下载：可用时用 Aria2c，不可用时 fallback 至 aiohttp。
        统一返回绝对本地路径。
        """
        if cls._aria2_available:
            try:
                gid = await download(url, novel_id, episode_num, download_dir)
                return await wait_complete(gid)
            except Exception as e:
                logger.warning(f"[Aria2c] 下载过程异常，尝试 fallback: {e}")
                # 单次失败不改变 _aria2_available，避免偶发错误导致全局降级
        return await fallback_download(url, novel_id, episode_num, download_dir)
