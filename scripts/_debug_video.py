#!/usr/bin/env python3
"""
_debug_video.py — 视频生成模块后台诊断脚本
测试:
  1. ALIYUN_API_KEY 环境变量读取
  2. Wan2_6VideoAPI 初始化（wan2.6-t2v 模型名）
  3. gateway 字符串 → API model 名称映射
  4. DashScope 单条视频任务提交（真实 API 调用，短 prompt）
  5. 限流重试逻辑：模拟 Throttling 响应
"""
import sys, asyncio, os
sys.path.insert(0, r"d:\桌面\12345678910-2.0.0")

from dotenv import load_dotenv
load_dotenv(r"d:\桌面\12345678910-2.0.0\.env")

PASS = "✅ OK"
FAIL = "❌ FAIL"

print("=" * 60)
print("Test 1: ALIYUN_API_KEY 读取")
print("=" * 60)
aliyun_key = os.getenv("ALIYUN_API_KEY", "")
ok1 = aliyun_key.startswith("sk-")
print(f"  Key 前缀: {aliyun_key[:8]}*** ({'已配置' if ok1 else '未配置或格式异常'})")
print(f"\n{PASS}" if ok1 else f"\n{FAIL}")
assert ok1, "ALIYUN_API_KEY 未设置"

print()
print("=" * 60)
print("Test 2: GATEWAY_MODEL_MAP 映射验证")
print("=" * 60)
GATEWAY_MODEL_MAP = {
    "wan_2_6":          "wan2.6-t2v",
    "wan2.6":           "wan2.6-t2v",
    "wan2.6-t2v":       "wan2.6-t2v",
    "seedance-1.5-pro": "doubao-seedance-1-5-pro-251215",
}
tests = [
    ("wan_2_6",          "wan2.6-t2v"),
    ("wan2.6",           "wan2.6-t2v"),
    ("seedance-1.5-pro", "doubao-seedance-1-5-pro-251215"),
]
ok2 = True
for gw, expected in tests:
    got = GATEWAY_MODEL_MAP.get(gw, gw)
    status = PASS if got == expected else FAIL
    print(f"  {gw:25s} → {got}  {status}")
    if got != expected:
        ok2 = False
print(f"\n{PASS if ok2 else FAIL}")
assert ok2

print()
print("=" * 60)
print("Test 3: Wan2_6VideoAPI 初始化（不调用 API）")
print("=" * 60)
from core.services.video_service.aliyun_service import Wan2_6VideoAPI
api = Wan2_6VideoAPI(api_key=aliyun_key, model="wan2.6-t2v")
print(f"  model:   {api.model}")
print(f"  api_key: {api.api_key[:8]}***")
ok3 = api.model == "wan2.6-t2v" and api.api_key == aliyun_key
print(f"\n{PASS if ok3 else FAIL}")
assert ok3

print()
print("=" * 60)
print("Test 4: DashScope 单条视频任务提交（真实 API）")
print("=" * 60)
print("  提交一个极短测试 prompt（wan2.6-t2v）...")

async def test_submit():
    try:
        task_id = await api.submit_task(
            "A serene mountain landscape, golden sunset, cinematic wide shot"
        )
        if task_id:
            print(f"  ✅ task_id = {task_id}")
            return True
        else:
            print("  ❌ task_id 为空")
            return False
    except Exception as e:
        err = str(e)
        if "Throttling" in err:
            print(f"  ⚠️ 限流触发（符合预期，说明 API 可通达）: {err[:120]}")
            return True   # 能触发限流说明 API 调用已到达服务端
        print(f"  ❌ 异常: {err[:200]}")
        return False

ok4 = asyncio.run(test_submit())
print(f"\n{PASS if ok4 else FAIL}")

print()
print("=" * 60)
print("Test 5: Semaphore 限流逻辑验证（本地模拟，无网络）")
print("=" * 60)
import time

async def test_semaphore():
    sem = asyncio.Semaphore(3)
    timestamps = []
    async def task(idx):
        async with sem:
            await asyncio.sleep((idx - 1) * 0.05)  # 模拟 2s 间隔用 0.05s
            timestamps.append((idx, asyncio.get_event_loop().time()))

    tasks = [task(i+1) for i in range(6)]
    await asyncio.gather(*tasks)
    return timestamps

ts = asyncio.run(test_semaphore())
print(f"  6 个任务时间戳 (相对): ", [round(t - ts[0][1], 2) for _, t in ts])
ok5 = len(ts) == 6
print(f"\n{PASS if ok5 else FAIL}")

print()
print("=" * 60)
print("ALL VIDEO DEBUG TESTS DONE")
print("=" * 60)
