"""
scripts/migrate_schema_v280.py
v2.8.0 飞书多维表格 Schema 迁移脚本

功能：在【剧本拆解表】中新增 v3.0.0 后期制作所需的 4 个字段：
  1. 首帧参考图  (附件类型 type=17) - I2V 分镜首帧图（从素材表自动回填）
  2. 旁白与台词  (多行文本 type=1)  - TTS 输入源
  3. 音效提示词  (多行文本 type=1)  - SFX/BGM 关键词（如"暴雨"、"赛博朋克"）
  4. 局部运动笔刷 (多行文本 type=1) - 预留，未来 Motion API 控制区域运动

执行方式：
  python scripts/migrate_schema_v280.py

  --dry-run  : 仅打印当前字段列表，不做任何修改
  --rollback : 删除本次新增的 4 个字段（回滚，不影响现有数据）

幂等性保证：字段已存在时跳过创建，不会报错。
"""
import sys
import os
import argparse

# 确保运行时能找到 core/
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.config import settings
import requests
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("migrate_v280")

# ── 目标表：剧本拆解表 ────────────────────────────────────────────────────────
APP_TOKEN = settings.FEISHU_APP_TOKEN_SCRIPT
TABLE_ID  = settings.FEISHU_TABLE_SCRIPT

# ── v3.0.0 新增字段定义 ───────────────────────────────────────────────────────
# type: 1=多行文本, 2=数字, 3=单选, 17=附件
NEW_FIELDS = [
    {
        "field_name": "首帧参考图",
        "type": 17,          # 附件类型，存储分镜首帧图 URL
        "description": "I2V 图生视频锁帧图（从素材生成表按 entity_id 自动回填分镜首帧图）",
    },
    {
        "field_name": "旁白与台词",
        "type": 1,           # 多行文本
        "description": "角色旁白或对白，传入 TTS 引擎生成人声干声",
    },
    {
        "field_name": "音效提示词",
        "type": 1,
        "description": "场景环境音效关键词，如：暴雨、赛博朋克街道、古典钢琴BGM，传入 SFX 引擎",
    },
    {
        "field_name": "局部运动笔刷",
        "type": 1,
        "description": "预留字段，未来用于 Motion API 控制画面局部区域的运动方向和强度",
    },
]


def _get_token() -> str:
    url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
    resp = requests.post(url, json={
        "app_id": settings.FEISHU_APP_ID,
        "app_secret": settings.FEISHU_APP_SECRET,
    })
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"获取飞书 Token 失败: {data.get('msg')}")
    return data["tenant_access_token"]


def _headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


def list_fields(token: str) -> dict:
    """返回 {field_name: (field_id, type)} 映射。"""
    url = (
        f"https://open.feishu.cn/open-apis/bitable/v1/apps/{APP_TOKEN}"
        f"/tables/{TABLE_ID}/fields"
    )
    resp = requests.get(url, headers=_headers(token))
    resp.raise_for_status()
    result = {}
    for f in resp.json().get("data", {}).get("items", []):
        result[f["field_name"]] = (f["field_id"], f["type"])
    return result


def create_field(token: str, field_name: str, field_type: int, description: str = "") -> bool:
    """创建字段，返回是否成功。"""
    url = (
        f"https://open.feishu.cn/open-apis/bitable/v1/apps/{APP_TOKEN}"
        f"/tables/{TABLE_ID}/fields"
    )
    payload = {"field_name": field_name, "type": field_type}
    try:
        resp = requests.post(url, headers=_headers(token), json=payload)
        resp.raise_for_status()
        body = resp.json()
        if body.get("code") == 0:
            logger.info(f"✅ 字段创建成功: [{field_name}]（{description}）")
            return True
        else:
            logger.error(f"❌ 字段创建失败: [{field_name}] {body.get('msg')}")
            return False
    except Exception as e:
        logger.error(f"❌ 字段创建异常: [{field_name}] {e}")
        return False


def delete_field(token: str, field_id: str, field_name: str) -> bool:
    """物理删除字段。"""
    url = (
        f"https://open.feishu.cn/open-apis/bitable/v1/apps/{APP_TOKEN}"
        f"/tables/{TABLE_ID}/fields/{field_id}"
    )
    try:
        resp = requests.delete(url, headers=_headers(token))
        resp.raise_for_status()
        body = resp.json()
        if body.get("code") == 0:
            logger.info(f"🗑️  字段删除成功: [{field_name}]")
            return True
        logger.warning(f"⚠️  字段删除异常: [{field_name}] {body.get('msg')}")
        return False
    except Exception as e:
        logger.error(f"❌ 字段删除异常: [{field_name}] {e}")
        return False


def run_migration(dry_run: bool = False, rollback: bool = False):
    logger.info("=" * 60)
    logger.info(f"v2.8.0 Schema 迁移 ({'DRY RUN - 不执行修改' if dry_run else '正式执行'})")
    logger.info(f"目标表: 剧本拆解表 ({TABLE_ID})")
    logger.info("=" * 60)

    token = _get_token()
    existing = list_fields(token)

    logger.info(f"当前字段总数: {len(existing)}")
    for fname in sorted(existing.keys()):
        fid, ftype = existing[fname]
        logger.info(f"  [{ftype}] {fname} ({fid})")

    logger.info("-" * 60)

    if rollback:
        logger.info("🔄 回滚模式：删除 v2.8.0 新增字段")
        for fdef in NEW_FIELDS:
            fname = fdef["field_name"]
            if fname in existing:
                fid, _ = existing[fname]
                if not dry_run:
                    delete_field(token, fid, fname)
                else:
                    logger.info(f"[DRY RUN] 将删除: [{fname}]")
            else:
                logger.info(f"字段不存在，无需删除: [{fname}]")
        return

    # 正向迁移：新增字段
    created, skipped = 0, 0
    for fdef in NEW_FIELDS:
        fname = fdef["field_name"]
        if fname in existing:
            logger.info(f"⏩ 字段已存在，跳过: [{fname}]")
            skipped += 1
            continue
        if dry_run:
            logger.info(f"[DRY RUN] 将创建: [{fname}] type={fdef['type']} — {fdef['description']}")
            created += 1
        else:
            ok = create_field(token, fname, fdef["type"], fdef["description"])
            if ok:
                created += 1

    logger.info("-" * 60)
    logger.info(f"迁移完成: 创建 {created} 个，跳过 {skipped} 个")
    if not dry_run:
        logger.info("✅ 请前往飞书多维表格验证字段是否出现在【剧本拆解表】中")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="v2.8.0 飞书 Schema 迁移工具")
    parser.add_argument("--dry-run", action="store_true", help="仅打印操作计划，不执行修改")
    parser.add_argument("--rollback", action="store_true", help="删除本次新增的字段（回滚）")
    args = parser.parse_args()
    run_migration(dry_run=args.dry_run, rollback=args.rollback)
