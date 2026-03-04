"""
sandbox_batch_test.py — v2.6.0 沙盒端到端测试脚本

测试流程：
1. 读取 测试小说章节/ 目录下所有 .txt 文件
2. 调用 /api/upload_novel_batch 提交批量任务
3. 订阅 /api/progress_stream/{task_id} 实时打印进度（SSE）
4. 轮询 /api/batch_status/{task_id} 直到任务完成
5. 打印最终统计
"""

import os
import sys
import json
import time
import glob
import requests
import sseclient  # pip install sseclient-py

# ── 配置 ──────────────────────────────────────────────────────────────
BASE_URL     = "http://127.0.0.1:8000"
NOVEL_DIR    = os.path.join(os.path.dirname(__file__), "测试小说章节")
NOVEL_ID     = "我不是戏神_沙盒测试"   # 符合校验规则：中文+下划线
STYLE        = "anime"
GATEWAY      = "wan_2_6"
SANDBOX_MODE = True                     # 沙盒模式：不触发真实视频生成
# ──────────────────────────────────────────────────────────────────────

BOLD  = "\033[1m"
GREEN = "\033[32m"
YELLOW= "\033[33m"
RED   = "\033[31m"
CYAN  = "\033[36m"
RESET = "\033[0m"

def log(color, tag, msg):
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}] {color}{BOLD}[{tag}]{RESET} {msg}")

def load_chapters():
    """按文件名排序读取所有章节"""
    files = sorted(glob.glob(os.path.join(NOVEL_DIR, "*.txt")))
    if not files:
        log(RED, "ERROR", f"测试目录为空: {NOVEL_DIR}")
        sys.exit(1)
    chapters = []
    for path in files:
        name = os.path.basename(path)
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read().strip()
        chapters.append({"name": name, "content": content})
        log(CYAN, "LOAD", f"{name}  ({len(content)} 字)")
    return chapters

def health_check():
    """检查服务是否在线"""
    try:
        r = requests.get(f"{BASE_URL}/api/system_info", timeout=5)
        if r.status_code == 200:
            log(GREEN, "HEALTH", "服务在线 ✅")
            return True
    except Exception as e:
        pass
    log(RED, "HEALTH", f"服务不可达: {BASE_URL}，请先启动 FastAPI 服务")
    return False

def submit_batch(chapters):
    """提交批量任务，返回 task_id"""
    data = {
        "novel_id":     NOVEL_ID,
        "style":        STYLE,
        "gateway":      GATEWAY,
        "image_gateway": "aliyun",
        "sandbox_mode": str(SANDBOX_MODE).lower(),
        "aliyun_image_model": "wan2.6-t2i",
        "aliyun_video_model": "wan2.6-i2v-flash",
        "tts_voice": "Cherry",
        "aspect_ratio": "16:9",
        "segmented_processing": "auto"
    }
    
    files_payload = []
    for c in chapters:
        # requests requires tuple (filename, fileobj)
        files_payload.append(("files", (c["name"], c["content"])))
        
    log(YELLOW, "SUBMIT", f"提交 {len(chapters)} 个章节 → /api/upload_novel_batch ...")
    r = requests.post(f"{BASE_URL}/api/upload_novel_batch", data=data, files=files_payload, timeout=30)
    if r.status_code != 200:
        log(RED, "ERROR", f"HTTP {r.status_code}: {r.text[:300]}")
        sys.exit(1)
    data_json = r.json()
    if data_json.get("status") != "ok":
        log(RED, "ERROR", f"提交失败: {data_json}")
        sys.exit(1)
    task_id = data_json["task_id"]
    log(GREEN, "SUBMIT", f"任务已入队 task_id={task_id}")
    return task_id

def stream_progress(task_id):
    """SSE 流式打印进度（直到任务完成或 5 分钟超时）"""
    url = f"{BASE_URL}/api/progress_stream/{task_id}"
    log(CYAN, "SSE", f"订阅实时进度流: {url}")
    last_stage1 = ""
    deadline = time.time() + 3600  # 最长等待 1 小时（长篇处理需要时间）
    try:
        # 使用普通 requests stream + 手动解析 SSE
        with requests.get(url, stream=True, timeout=None) as resp:
            for raw_line in resp.iter_lines():
                if time.time() > deadline:
                    log(RED, "TIMEOUT", "等待超时（1h），中止监听")
                    break
                if not raw_line:
                    continue
                line = raw_line.decode("utf-8") if isinstance(raw_line, bytes) else raw_line
                if line.startswith(": keep-alive"):
                    log(CYAN, "♡", "SSE 心跳")
                    continue
                if line.startswith("data: "):
                    try:
                        entry = json.loads(line[6:])
                    except Exception:
                        continue

                    # 最终完成事件
                    if entry.get("event") == "done":
                        status  = entry.get("status", "unknown")
                        scenes  = entry.get("scenes", 0)
                        failed  = entry.get("failed_chapters", [])
                        color   = GREEN if status == "success" else RED
                        log(color, "DONE",
                            f"status={status}, scenes={scenes}, failed={len(failed)}")
                        if failed:
                            log(RED, "FAILED", f"失败章节: {failed}")
                        return entry

                    # 普通进度条目
                    stage   = entry.get("stage", "?")
                    chapter = entry.get("chapter", "")
                    progress= entry.get("progress", "")
                    status  = entry.get("status", "")
                    icon    = {"success":"✅","跳过":"⏭","error":"❌"}.get(status, "🔄")
                    color   = {"success":GREEN,"跳过":YELLOW,"error":RED}.get(status, CYAN)
                    if progress != last_stage1 or status in ("error","success"):
                        log(color, stage.upper(),
                            f"{icon} {chapter} ({progress}) — {status}")
                        last_stage1 = progress
    except KeyboardInterrupt:
        log(YELLOW, "INTERRUPT", "用户中断监听，任务仍在后台运行")
    return {}

def final_report(task_id):
    """轮询最终状态并打印统计"""
    try:
        r = requests.get(f"{BASE_URL}/api/batch_status/{task_id}", timeout=10)
        data = r.json()
        log(BOLD + GREEN, "REPORT", "═" * 50)
        log(GREEN, "REPORT", f"novel_id    : {data.get('novel_id')}")
        log(GREEN, "REPORT", f"总文件数    : {data.get('total_files')}")
        log(GREEN, "REPORT", f"Stage1完成  : {data.get('stage1_done')}")
        log(GREEN, "REPORT", f"生成分镜数  : {data.get('scenes')}")
        log(GREEN, "REPORT", f"失败章节    : {data.get('failed_chapters', [])}")
        log(GREEN, "REPORT", f"最终状态    : {data.get('status')}")
        log(BOLD + GREEN, "REPORT", "═" * 50)
    except Exception as e:
        log(RED, "REPORT", f"获取最终状态失败: {e}")


# ────────────────── 主流程 ──────────────────
if __name__ == "__main__":
    print(f"\n{BOLD}{CYAN}{'='*60}")
    print(f"  v2.6.0 沙盒端到端测试 — 《我不是戏神》 20章")
    print(f"{'='*60}{RESET}\n")

    if not health_check():
        sys.exit(1)

    chapters = load_chapters()
    print(f"\n{BOLD}共加载 {len(chapters)} 个章节，总字数: {sum(len(c['content']) for c in chapters):,} 字{RESET}\n")

    task_id = submit_batch(chapters)
    done    = stream_progress(task_id)
    final_report(task_id)
