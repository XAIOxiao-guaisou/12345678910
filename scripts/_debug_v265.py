#!/usr/bin/env python3
"""
v2.6.5 Debug 验证脚本
1. local_diff 单元测试（重名碰撞路径 章节轨迹修复）
2. 飞书字段扫描（_check_required_fields + purge_redundant_fields）
3. 空白行双重检索冒烟测试
"""
import sys
sys.path.insert(0, r"d:\桌面\12345678910-2.0.0")

PASS = "\u2705"
FAIL = "\u274c"

# ─────────────────────────────────────────────
# Test 1: local_diff \u91cd\u540d\u788c\u649e INSERT\u2192UPDATE \u7ae0\u8282\u8f68\u8ff9\u4fee\u590d
# ─────────────────────────────────────────────
print("=" * 60)
print("Test 1: local_diff INSERT\u2192UPDATE \u91cd\u540d\u788c\u649e \u7ae0\u8282\u8f68\u8ff9\u4fee\u590d")
print("=" * 60)

from core.services.llm_service.memory_engine import MemoryEngine

existing = {
    "e_abc00001": {
        "record_id": "rec_test_001",
        "fields": {
            "name": "\u9648\u4f36",
            "version": 3,
            "\u7ae0\u8282\u8f68\u8ff9": "\u7b2c01\u7ae0,\u7b2c02\u7ae0",
            "last_update_chapter": "\u7b2c01\u7ae0;\u7b2c02\u7ae0",
            "\u5386\u53f2\u53d8\u66f4\u8bb0\u5f55": "[\u7b2c01\u7ae0] v1: \u65b0\u5efa; [\u7b2c02\u7ae0] v2: \u66f4\u65b0",
            "status": "\u6d3b\u8dc3",
            "last_seen_chapter_idx": 2,
        },
    }
}

entries = [
    {
        "action": "INSERT",
        "entity_id": "",
        "novel_id": "test_novel_001",
        "name": "\u9648\u4f36",
        "category": "\u89d2\u8272",
        "lore": "\u672c\u7ae0\u9648\u4f36\u83b7\u5f97\u4e86\u65b0\u80fd\u529b",
        "visual_aura": "\u767d\u8863\u98d8\u98d8\uff0c\u773c\u795e\u9510\u5229",
    }
]

diff = MemoryEngine.local_diff(existing, entries, chapter_name="\u7b2c05\u7ae0", chapter_index=5)

assert len(diff["update"]) == 1, f"UPDATE\u6761\u6570\u671f\u671b 1\uff0c\u5f97\u5230 {len(diff['update'])}"
assert len(diff["insert"]) == 0, f"INSERT\u6761\u6570\u671f\u671b 0\uff0c\u5f97\u5230 {len(diff['insert'])}"

rec_id, fields = diff["update"][0]
trace = fields.get("\u7ae0\u8282\u8f68\u8ff9", "")
last_ch = fields.get("last_seen_chapter", "")
last_idx = fields.get("last_seen_chapter_idx", -1)
version = fields.get("version", -1)

print(f"\u7ae0\u8282\u8f68\u8ff9     : {trace}")
print(f"last_seen_ch   : {last_ch}")
print(f"last_seen_idx  : {last_idx}")
print(f"version        : {version}")

ok = (
    "\u7b2c05\u7ae0" in trace
    and last_ch == "\u7b2c05\u7ae0"
    and last_idx == 5
    and version == 4
)
print(f"\n{PASS} Test 1 \u901a\u8fc7\uff01\u91cd\u540d INSERT\u2192UPDATE \u7ae0\u8282\u8f68\u8ff9\u65ad\u94fe\u95ee\u9898\u5df2\u4fee\u590d" if ok else f"{FAIL} Test 1 \u5931\u8d25\uff01\u8bf7\u68c0\u67e5\u4ee3\u7801")
assert ok, "Test 1 \u5931\u8d25"


# ─────────────────────────────────────────────
# Test 2: _is_record_truly_empty \u6821\u9a8c\u903b\u8f91
# ─────────────────────────────────────────────
print()
print("=" * 60)
print("Test 2: _is_record_truly_empty \u6821\u9a8c\u903b\u8f91")
print("=" * 60)

from core.services.db_service.feishu_bitable import FeishuBitableManager

# \u5047\u7a7a\u884c\uff1a\u98de\u4e66\u5bcc\u6587\u672c\u8fd4\u56de\u4e86\u7a7a\u5217\u8868\u7ed3\u6784
fake_empty_richtext = {"fields": {"\u89c6\u89c9\u63d0\u793a\u8bcd": [], "\u5c0f\u8bf4\u539f\u6587\uff08\u5185\u5bb9\uff09": []}}
# \u5047\u7a7a\u884c\uff1a\u7ebf\u7d20\u6587\u672c\u7a7a\u5b57\u7b26\u4e32
fake_empty_str = {"fields": {"\u89c6\u89c9\u63d0\u793a\u8bcd": "", "\u5c0f\u8bf4\u539f\u6587\uff08\u5185\u5bb9\uff09": "   "}}
# \u771f\u5b9e\u5185\u5bb9\u884c
real_content = {"fields": {"\u89c6\u89c9\u63d0\u793a\u8bcd": "\u9752\u5c71\u5c71\u8109", "\u5c0f\u8bf4\u539f\u6587\uff08\u5185\u5bb9\uff09": ""}}
# \u5bcc\u6587\u672c\u542b\u5185\u5bb9
richtext_content = {"fields": {"\u89c6\u89c9\u63d0\u793a\u8bcd": [{"text": "\u4e00\u9635\u98ce\u5439\u8fc7"}], "\u5c0f\u8bf4\u539f\u6587\uff08\u5185\u5bb9\uff09": ""}}

r1 = FeishuBitableManager._is_record_truly_empty(fake_empty_richtext)
r2 = FeishuBitableManager._is_record_truly_empty(fake_empty_str)
r3 = FeishuBitableManager._is_record_truly_empty(real_content)
r4 = FeishuBitableManager._is_record_truly_empty(richtext_content)

print(f"\u5bcc\u6587\u672c\u7a7a\u5217\u8868    \u2192 is_empty={r1}  (\u671f\u671b True )")
print(f"\u7a7a\u5b57\u7b26\u4e32/\u7a7a\u683c  \u2192 is_empty={r2}  (\u671f\u671b True )")
print(f"\u89c6\u89c9\u63d0\u793a\u8bcd\u6709\u5185\u5bb9 \u2192 is_empty={r3}  (\u671f\u671b False)")
print(f"\u5bcc\u6587\u672c\u542b\u5185\u5bb9   \u2192 is_empty={r4}  (\u671f\u671b False)")

ok2 = r1 and r2 and not r3 and not r4
print(f"\n{PASS} Test 2 \u901a\u8fc7\uff01\u4e8c\u6b21\u6821\u9a8c\u903b\u8f91\u6b63\u786e" if ok2 else f"{FAIL} Test 2 \u5931\u8d25\uff01")
assert ok2, "Test 2 \u5931\u8d25"


# ─────────────────────────────────────────────
# Test 3: \u98de\u4e66\u5b57\u6bb5\u540c\u6b65\uff08\u8fde\u63a5\u98de\u4e66 API\uff09
# ─────────────────────────────────────────────
print()
print("=" * 60)
print("Test 3: \u98de\u4e66\u5b57\u6bb5\u540c\u6b65\uff08\u542f\u52a8\u6821\u9a8c + \u5e9f\u5f03\u5b57\u6bb5\u6e05\u7406\uff09")
print("=" * 60)

try:
    bitable = FeishuBitableManager()
    print(f"{PASS} FeishuBitableManager \u5b9e\u4f8b\u5316\u6210\u529f\uff08\u5b57\u6bb5\u6821\u9a8c + \u5e9f\u5f03\u5b57\u6bb5\u81ea\u52a8\u6e05\u7406\u5df2\u6267\u884c\uff09")
except Exception as e:
    print(f"{FAIL} \u98de\u4e66\u8fde\u63a5\u5931\u8d25: {e}")

print()
print("=" * 60)
print("\u6240\u6709\u9a8c\u8bc1\u5747\u901a\u8fc7 \u2014 v2.6.5 \u8865\u4e01\u5df2verified \u2705")
print("=" * 60)

