# 版本 2.1.1 更新日志 (v2.1.1 Changelog)

**发布日期**: 2026-02-27
**主题**: 飞书多维表格引擎升级 & MOCK 沙盒闭环模式

## ✨ 主要更新 (Features & Enhancements)

### 1. 动态智能多维表格引擎 (Dynamic Bitable Field Mapping)
- 彻底解决了因为用户修改飞书多维表格字段名而导致的 `FieldNameNotFound (1254045)` 崩溃问题。
- 新增 `get_table_field_names()` 方法动态拉取飞书表格真实 Schema。
- 引入优雅降级策略：如果表格内确实缺少某个字段（例如：`异常日志` 或 `代称`），系统将会在构建 JSON Payload 时安全忽略该字段，保证流媒体流水线稳定前行。

### 2. MOCK 沙盒测试模式 (Mock Sandbox Mode)
- 实现了纯本地的流程打桩：在 `.env` 生效 `MOCK_MODE=True` 后，不仅阻断对高成本视频生成 API 的请求，且在 `Worker` 日志中展示并回传至企微一条高亮的**【沙盒报告】**，以体现全链路运转成功并预估真金白银花销。
- 前端 WebUI 同步配备**高亮闪烁指示灯（Sandbox LED）**，在页面顶部给出黄牌警告，防止运维混淆真假状态。

### 3. 多模态实体寻回策略 (Entity Resolution)
- **别名抽取**：阶段一构建记忆中枢时，通过 DeepSeek 增强提取核心角色的 `aliases`（代称列表，例如“他/青衫剑客”）。
- **动态切片挂载**：调整 Pipeline 的 `relevant_memories` 挂载算法，能够通过代称智能关联出角色的原文本视觉设定（Hasselblad 哈苏参数等）。

### 4. 两阶段视觉连贯与抗幻觉协议 (Two-Stage Continuity & Anti-Hallucination)
- **视觉传承**：上下两 Chunk 的承接从“文字总结”升格为连带着“上一台机位的 `visual_prompt`”，强制镜头起幅接前帧。
- **防止衍生**：追加 Prompt Rule 4 协议：“绝对禁止私自添加未在记忆空间定义的宏大背景与群体”。约束大模型发散，死锁主角周围环境。

### 5. WebUI 界面交互 (Frontend Experience)
- 全新加入了 **TXT拖拽识别区域**：用户可将本地脚本 `.txt` 暴力拖入多行文本框。
- 增加控制滑动条（如 Temperature、Top P 与 Chunk_Size），让控片质量彻底可调节化。

## 🪲 错误修复 (Bug Fixes)
- 移除了启动脚本 `一键启动WebUI.bat` 中的中文字符报错和挂载后台进程杀不干净的情况。
- 修复了 DeepSeek API 断线返回 `truncated/Missing ]` 时的正则深度续写修复方案，挽救 JSON 反序列化致命问题。
