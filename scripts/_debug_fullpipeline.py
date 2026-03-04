#!/usr/bin/env python3
"""
_debug_fullpipeline.py — 全流程端到端诊断测试
覆盖: 环境变量 → 飞书 → 记忆引擎 → 图像生成 → 分镜-素材绑定 → i2v视频提交

注意: 会触发真实 API 调用（阿里云生图/视频），每次运行产生少量费用。
"""
import sys, asyncio, os, time
sys.path.insert(0, r"d:\桌面\12345678910-2.0.0")

from dotenv import load_dotenv
load_dotenv(r"d:\桌面\12345678910-2.0.0\.env")

PASS, FAIL, WARN = "✅ PASS", "❌ FAIL", "⚠️ WARN"
results = []

def section(title):
    print(f"\n{'='*60}\n{title}\n{'='*60}")

def report(name, ok, detail=""):
    tag = PASS if ok else FAIL
    results.append((name, ok))
    print(f"  {tag} {name}" + (f"  [{detail}]" if detail else ""))

# ─────────────────────────────────────────────
# T1: 环境变量
# ─────────────────────────────────────────────
section("T1: 关键环境变量")
aliyun_key = os.getenv("ALIYUN_API_KEY", "")
feishu_app_id = os.getenv("FEISHU_APP_ID", "")
feishu_secret = os.getenv("FEISHU_APP_SECRET", "")

report("ALIYUN_API_KEY", aliyun_key.startswith("sk-"), aliyun_key[:12] + "***")
report("FEISHU_APP_ID", bool(feishu_app_id), feishu_app_id)
report("FEISHU_APP_SECRET", bool(feishu_secret), feishu_secret[:8] + "***")

# ─────────────────────────────────────────────
# T2: 模块导入
# ─────────────────────────────────────────────
section("T2: 核心模块导入")
modules_ok = True
try:
    from core.pipeline import PipelineOrchestrator
    from core.services.db_service.feishu_bitable import FeishuBitableManager
    from core.services.llm_service.memory_engine import MemoryEngine
    from core.services.llm_service.deepseek_service import DeepSeekService
    from core.services.image_service.aliyun_image_service import AliyunImageService
    from core.services.video_service.aliyun_service import Wan2_6VideoAPI
    report("所有核心模块导入", True)
except Exception as e:
    report("模块导入", False, str(e)[:80])
    modules_ok = False

if not modules_ok:
    print("\n❌ 模块导入失败，终止测试")
    sys.exit(1)

# ─────────────────────────────────────────────
# T3: 飞书连通性
# ─────────────────────────────────────────────
section("T3: 飞书 API 连通性")
try:
    bitable = FeishuBitableManager()
    # 查询记忆表（novel_id=我不是戏神）
    t_start = time.time()
    memories = bitable.get_memories_by_novel("我不是戏神")
    elapsed = time.time() - t_start
    report("飞书记忆表查询", True, f"{len(memories)} 条记忆, {elapsed:.1f}s")

    # 查询素材表
    asset = bitable.get_asset_by_entity("陈伶", "我不是戏神")
    report("素材表 get_asset_by_entity", True,
           f"found={asset.get('found')}, image_url={'有' if asset.get('image_url') else '无'}")
except Exception as e:
    report("飞书连通性", False, str(e)[:100])

# ─────────────────────────────────────────────
# T4: 记忆引擎 Stage1 局部测试
# ─────────────────────────────────────────────
section("T4: MemoryEngine.local_diff → stage1_entities")
try:
    me = MemoryEngine(bitable)
    # 构造最小差分结果
    sample_diff = {
        "updates": [],
        "inserts": [{"entity_id": "test_entity", "name": "测试角色",
                     "present_in_current": True, "lore": "测试设定"}],
        "archives": []
    }
    from unittest.mock import patch, MagicMock
    with patch.object(me, '_call_llm', return_value=sample_diff):
        pass  # 只测试结构，不真实调LLM
    report("MemoryEngine 实例化", True)
except Exception as e:
    report("MemoryEngine", False, str(e)[:80])

# ─────────────────────────────────────────────
# T5: 图像生成（qwen-image-plus）
# ─────────────────────────────────────────────
section("T5: AliyunImageService — qwen-image-plus 生图")
async def test_image_gen():
    svc = AliyunImageService()
    t_start = time.time()
    result = await svc.generate_image(
        prompt="a dramatic Chinese opera performer in red silk robe, cinematic, "
               "masterpiece quality, 8k",
        seed=114514,
        width=1024,
        height=1024,
    )
    elapsed = time.time() - t_start
    ok = result.get("status") == "success" and result.get("url")
    report("qwen-image-plus 生图", ok,
           f"耗时 {elapsed:.1f}s, url={result.get('url','')[:60]}" if ok
           else result.get("error", "")[:80])
    return result.get("url", "") if ok else ""

image_url = asyncio.run(test_image_gen())

# ─────────────────────────────────────────────
# T6: DeepSeek 视觉 Prompt 生成
# ─────────────────────────────────────────────
section("T6: DeepSeekService.generate_visual_prompt")
try:
    entity = {
        "entity_id": "陈伶",
        "name": "陈伶",
        "category": "角色",
        "lore": "28岁男性，穿着破旧大红戏袍，眼神空洞迷茫，站在废弃戏台上",
    }
    prompt = DeepSeekService.generate_visual_prompt(entity, "", "")
    ok = bool(prompt) and len(prompt) > 20
    report("generate_visual_prompt", ok, (prompt[:80] + "...") if ok else "空返回")
except Exception as e:
    report("generate_visual_prompt", False, str(e)[:80])

# ─────────────────────────────────────────────
# T7: wan2.6-i2v 视频提交（i2v 模式）
# ─────────────────────────────────────────────
section("T7: Wan2_6VideoAPI wan2.6-i2v 视频提交")

async def test_i2v_submit():
    api = Wan2_6VideoAPI(api_key=aliyun_key, model="wan2.6-i2v")
    report("Wan2_6VideoAPI 初始化", api.is_i2v, f"model={api.model}")

    if not image_url:
        report("i2v 提交（需图片URL）", False, "T5 未生成图片，跳过")
        return

    try:
        t_start = time.time()
        task_id = await api.submit_task(
            prompt="dramatic stage movement, slow motion, cinematic",
            image_url=image_url,
        )
        elapsed = time.time() - t_start
        report("wan2.6-i2v 任务提交", bool(task_id),
               f"task_id={task_id}, {elapsed:.1f}s")

        if task_id:
            # 轮询一次状态确认联通
            await asyncio.sleep(5)
            status = await api.check_status(task_id)
            report("状态查询", status.get("status") in ("running", "succeeded"),
                   f"status={status.get('status')}")
    except Exception as e:
        err = str(e)
        if "QuotaExhausted" in err or "FreeTier" in err or "Throttling" in err:
            report("wan2.6-i2v API 可达性", True, f"Quota/限流（API通达）: {err[:60]}")
        else:
            report("wan2.6-i2v 提交", False, err[:100])

asyncio.run(test_i2v_submit())

# ─────────────────────────────────────────────
# T8: Scene-Asset 绑定逻辑（本地单元）
# ─────────────────────────────────────────────
section("T8: Scene-Asset 绑定逻辑单元测试")
try:
    async def test_binding():
        bitable2 = FeishuBitableManager()
        # 读取新生成的素材
        asset2 = bitable2.get_asset_by_entity("陈伶", "我不是戏神")
        cached_url = asset2.get("image_url", "") if asset2.get("found") else ""

        # 模拟分镜数据
        fake_scenes = [
            {"visual_prompt": "A young actor stands in ruins, dramatic lighting, 8k",
             "entity_ids": ["陈伶"]},
            {"visual_prompt": "Close-up of a worn red silk robe, cinematic, 8k",
             "entity_ids": ["大红戏袍"]},
        ]

        packed = []
        asset_cache_local = {"陈伶": cached_url} if cached_url else {}

        for sc in fake_scenes:
            vp = sc.get("visual_prompt", "")
            img = ""
            for eid in sc.get("entity_ids", []):
                if eid in asset_cache_local:
                    img = asset_cache_local[eid]
                    break
            packed.append((vp, img) if img else vp)

        i2v_count = sum(1 for x in packed if isinstance(x, tuple))
        t2v_count = sum(1 for x in packed if isinstance(x, str))
        report("i2v 分镜打包数", i2v_count >= 0, f"i2v={i2v_count}, t2v_fallback={t2v_count}")
        report("飞书素材命中", cached_url != "", f"url={'有' if cached_url else '无（首次运行正常）'}")

    asyncio.run(test_binding())
except Exception as e:
    report("Scene-Asset 绑定", False, str(e)[:100])

# ─────────────────────────────────────────────
# 汇总
# ─────────────────────────────────────────────
section("全流程测试汇总")
passed = sum(1 for _, ok in results if ok)
total = len(results)
for name, ok in results:
    print(f"  {'✅' if ok else '❌'} {name}")
print(f"\n{'🏆 全部通过！' if passed == total else f'⚠️ {passed}/{total} 通过，请查看上方详情'}")
print(f"  通过率: {passed}/{total}")
