# 项目交接上下文 (Handoff Context for v2.1.2)

> **请将此文档发送给下一个 AI 对话窗口，以快速恢复当前的项目记忆与上下文。**

## 1. 项目概览
当前项目是一个**AI短剧智能拆解与自动化流媒体生成流水线**。系统现已迭代至 `v2.1.1`，核心逻辑基于 FastAPI + Uvicorn 运行后端 API 和前端 WebUI，并通过异步 Worker 调度视频大模型。
- **数据核心**：飞书多维表格 (Feishu Bitable)，作为全链路的“数据库”和“记忆中枢”。
- **大模型驱动**：使用 DeepSeek (V3/R1) 进行剧本拆解、提取世界观和构建符合游戏引擎规范的高维视觉分镜（Prompts）。

## 2. 核心系统架构解读
1. **记忆中枢与两阶段架构 (Two-Stage Pipeline)** 
   - **阶段一 (Entity Extraction)**：DeepSeek 解析前几千字提取宏观世界观与角色设定，不仅提取 `lore` (设定内容) 和 `visual_aura` (视觉通感/外貌)，还提取了 `aliases` (角色的代称列表)。数据写入飞书【记忆中枢】表。
   - **阶段二 (Scene Breakdown & Assembly)**：按 `chunk_size` 切分剩余小说，并且强过滤相关记忆（通过角色真名与**代称**匹配 `name in chunk or aliases in chunk`）。最后连带“上一镜头的最后视觉位置 (`visual_prompt` 的尾部)” 一同喂给大模型，确保光影连续性和机位统一。
   - 包含明确的** Anti-Hallucination（反幻觉约束）**，杜绝大模型私自凭空添加无关背景元素。
2. **飞书多维表格防脆弱映射 (Dynamic Bitable Mapping)** 
   - `feishu_bitable.py` 在执行写入前会先调用 API `get_table_field_names` 查出表头真实存在的字段。如果用户在前端临时修改飞书表头（比如把 `深层设定逻辑` 换成了 `设定内容`），底层 `FIELD_MAPPING` 字典能无缝降级映射，甚至优雅舍弃用户删掉的字段，彻底杜绝了 `FieldNameNotFound (1254045)` 面条式报错。
3. **MOCK 资源沙盒模式 (Sandbox Mode)**
   - 在 `.env` 置入 `MOCK_MODE=True` 时，进入测试沙盒。
   - 界面：`index.html` 顶部有高亮橙色报警灯。
   - 后台：`worker.py` 不会去花钱请求视频 API，而是生成 1 毫秒空视频提交，并计算推演真实环境下的 Token 与金钱消耗报告，发送到企业微信机器人。

## 3. 下一步迭代方向 / TODO
- 目前短剧剧本能够高度保真地提取“视觉指纹”并分配在对应的场景中。
- 前后端接口已预留 `temperature`, `top_p`, `chunk_size`, 以及针对多后端的适配，未来可进一步调优模型参数。
- 视频生成节点暂为 Mock，下一步可能需要正式接线/调试【真实的大模型视频生成接口】（如火山引擎 Seedance，或是阿里云）。

## 4. 环境与启动文件
- 启动脚本: `一键启动WebUI.bat` （双线程拉起 Uvicorn 和后台 Worker，并自启浏览器本地端口 8000）
- 主入口: `core/pipeline.py` (AI 处理管线)
- Web 入口: `core/api/routes.py`
- 大模型请求逻辑: `core/services/llm_service/deepseek_service.py`
- 飞书接口: `core/services/db_service/feishu_bitable.py`
- 后台轮询渲染: `worker.py`
