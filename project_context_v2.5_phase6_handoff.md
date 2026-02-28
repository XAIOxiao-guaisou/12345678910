# Aiduanju API Engine — 新会话上下文移交 (v2.5 + Phase 6)

## 项目位置
`d:\桌面\12345678910-2.0.0` | Git Branch: `2.3.0`

## 架构总览

```
FastAPI (web/app.py) <-> pipeline.py (DeepSeek 2段 + Stage3 审计) <-> Feishu Bitable
                                                                          ↕
                                                                    worker.py (asyncio轮询)
                                                                          ↕
                                     VideoServiceFactory -> MockVideoService (沙盒) 或 AliyunAPI / VolcengineAPI
```

## 核心文件一览

| 文件 | 职责 |
|------|------|
| `worker.py` | 异步轮询飞书「待生成」队列，调用 VideoServiceFactory，发企微通知 |
| `core/pipeline.py` | 全流程协调：Stage1记忆构建 → Stage2场景拆解 → Stage3一致性审计 → 飞书写入 |
| `core/protocols/render_protocol.py` | 负责注入/提取 `[WAN_2_6_CONFIG]{...}[/WAN_2_6_CONFIG]` 路由标签 |
| `core/services/video_service/factory.py` | 工厂模式，`_sandbox_mode=True` 时返回 MockVideoService |
| `core/services/video_service/mock_service.py` | 沙盒适配器，模拟API延迟，生成虚拟估算账单 |
| `core/services/db_service/feishu_bitable.py` | 全量飞书 CRUD，包含动态字段映射 |
| `core/config.py` | 字段映射表，API Keys，MOCK_MODE 环境变量 |
| `core/services/video_service/defaults.py` | `SMART_PRESETS` 参数预设 + `GATEWAY_SPECS` 前端描述 |
| `core/api/routes.py` | FastAPI 路由：`/api/upload_novel`, `/api/update_presets`, `/api/reroute_sandbox`, `/api/system_info` |

## 已完成版本功能

### v2.3.0 — 架构解耦与 Prompt 资源化
- RenderProtocol 通信协议层（标签注入/提取）
- Jinja2 模板化 Prompt 管理（`prompts/` 目录，3阶段各自有 `.j2` 文件）
- MockVideoService 适配器（`_sandbox_mode` 工厂路由）

### v2.4.0 — 插件化网关与自描述UI
- `GATEWAY_REGISTRY` + `GATEWAY_SPECS` 驱动前端UI动态渲染
- Pydantic 强类型各网关参数(`wan26.py`, `seedance.py`)

### v2.5.0 — 环境级隔离（沙箱/生产）
- WebUI 顶部沙盒 Switch → `_sandbox_mode` 穿透到飞书协议标签
- `worker.py` 3级网关信任链（物理列 → 标签检测 → 安全降级）
- `/api/update_presets` 配置晋升（写回 `defaults.py`）
- `/api/reroute_sandbox` 一键剥离沙盒标记重新投产

### Phase 6 — 强网关关联防漂移（刚完成）
**问题根因**: Stage 3 一致性审计时，GPT 修补 Prompt 后回写了**裸文本**（无网关标签），Worker 拿到任务时找不到标签，触发安全降级落回 Seedance。

**修复清单**:
1. **`core/config.py`**: 新增字段映射 `"gateway": ["所属模型/网关", "网关模型", "Gateway", "模型"]`
2. **`feishu_bitable.py`**:
   - `insert_new_parsed_scenes(…, gateway="")` 新增参数，写入飞书「网关」专用列
   - `get_pending_tasks()` 优先从物理列读取网关，多字段名 Fallback
3. **`core/pipeline.py`**:
   - 调用 `insert_new_parsed_scenes(all_scenes, 1, gateway=gateway)` 传参
   - Stage 3 修补回写前，执行 `RenderProtocol.inject_render_config(updated_prompt, video_params, gateway)` 重注标签
4. **`worker.py`**: 重写网关信任日志，3级优先链清晰可见

## 飞书表结构（素材生成表 / 剧本拆解表）

### 剧本拆解表 (TABLE_SCRIPT) 现有映射字段
- `集数/场次` → 集数
- `视觉提示词` → 含嵌套网关标签的提示词
- `所属模型/网关` (NEW, Phase 6) → 防标签漂移的物理网关锁
- `状态` → 待生成 / 生成中 / 已完成(Mock) / 生成异常
- `异常日志` → Worker 异常写入 / Stage 3 修正原因

> **注意**: 飞书表中需手动添加 `所属模型/网关` (文本类型) 列，否则代码优雅跳过不写入。

## 已知问题 & 下一步建议
- `VOLCENGINE_API_KEY` 未配置，Seedance gateway 会直接熔断——需要用户添加 `.env` 凭证或仅使用 `wan_2_6`
- 沙盒模式下企微通知正常，但飞书附件上传跳过（设计如此）
- Feishu 表字段如缺少 `异常日志` 会自动重试一次后忽略，系统继续运行

## 快速启动
```bash
# 终端1：启动 FastAPI
uvicorn web.app:app --host 127.0.0.1 --port 8000

# 终端2：启动 Worker 守护进程
python worker.py
```

## 环境变量（.env）
```
FEISHU_APP_ID=...
FEISHU_APP_SECRET=...
FEISHU_APP_TOKEN_SCRIPT=...
FEISHU_TABLE_SCRIPT=...
FEISHU_APP_TOKEN_FACTORY=...
FEISHU_TABLE_FACTORY=...
FEISHU_APP_TOKEN_MEMORY=...
FEISHU_TABLE_MEMORY=...
ALIYUN_API_KEY=...
VOLCENGINE_API_KEY=...  # 暂未配置
DEEPSEEK_API_KEY=...
SILICONFLOW_API_KEY=...
WX_BOT_WEBHOOK=...
MOCK_MODE=False  # True 时全局 Mock（已废弃，新架构用 per-task _sandbox_mode）
```
