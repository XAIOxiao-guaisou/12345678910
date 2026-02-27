# AI 短剧自动化流水线 v2.1.0 架构升级与 Bug 修复报告 (Release Report)

---

## 📅 版本概览

- **版本号:** v2.1.0 (新分支)
- **核心升级方向:** Pipeline 健壮性提升、API 容灾切换自动补全、飞书 Bitable 集成深度优化、闭环企微消息通知。

---

## 🛠️ 具体功能优化与 Bug 修复列表

### 1. 企微机器人 (WeWork Bot) 闭环通知体系
**问题背景:** 此前 worker 完成视频下载后，缺乏对用户的即时感知，用户不知道系统何时跑完。
**实施方案:**
- 在 `core/config.py` 和 `.env` 中新增了 `WX_BOT_WEBHOOK` 环境变量支持。
- 在 `worker.py` 的守护进程中拦截视频下载完毕的信号。
- 视频成片成功下载到本地 `Download/` 文件夹并打通系统状态后，立刻组装一段包含**生成时间**、**任务ID**、**本地路径**和**被用模型**的 Markdown 格式报文。
- 通过 HTTP POST 实时推送给预设的企业微信群机器人，实现完全无人值守监听。

### 2. 多重模型自动容灾切换 (Auto-Fallback)
**问题背景:** 经常遇到火山引擎视频 API（Seedance 等模型）因为欠费或 Key 填写错漏而引发 HTTP 401/403，导致整个流线彻底熔断挂起。
**实施方案:**
- 升级了 `core/services/video_service/factory.py` 中的 `get_service()` 工厂函数。
- 引入了状态检查：一旦发现请求方传入的网关指向 Volcengine（火山引擎），但环境变量 `VOLCENGINE_API_KEY` 为空/无效/占位符时。
- **自动截断火山的请求**，并在后台无感且平滑地切换调用阿里云的 `Wan2.6` 模型（Aliyun API）。
- 并在后台抛出黄色的 `WARNING` 日志提示开发者，成功保证了任何时刻流水线的存货率。

### 3. 飞书多端表 (Feishu Bitable) 字段容错与减负
**问题背景:** 此前由于用户的飞书表字段命名与代码写死的 `异常日志` 或 `视频文件` (Attachment) 对不上，频发 `FieldNameNotFound (1254045)` 和 `MultiSelectFieldConvFail` 等熔断级异常（导致视频生成过了，但是状态永远卡在生成中）。
**实施方案:**
- **剥离沉重附件上传逻辑**: 因飞书 API 的附件媒体传输极度缓慢且不稳定，将物理 MP4 的二进制上传逻辑全量剥离。
- **改换轻量级文本写入**: 将原本的传输替换为，直接把生成的视频名转化为普通文本字符串推送至飞书中。如果飞书表内连此字符串列都没有，`feishu_bitable.py` 第 300 行左右增加了极为强壮的容灾递归 `pop()`，会智能吐弃不可用字段（发出 Warning），继续推行「状态」为“已完成”的神圣意志。
- **修正列表状态入参**: 修复飞书多选状态列（如 "状态" 字段写入 `["已完成"]` 替代原先字符串的 Bug 格式，解决 `MultiSelectFieldConvFail`）。

### 4. 火山 Engine Core SDK 异常处理修复
**问题背景:** 错误捕获块中的语法或模块导入写错，引发 `ImportError: cannot import name 'APIError' from 'volcenginesdkarkruntime'`。
**实施方案:**
- 跟进火山 SDK 更新，精准修订了 `core/services/video_service/volcengine_service.py` 内的 `_is_volc_retryable` 函数导入规范（正确指向 `ArkAPIConnectionError`, `ArkRateLimitError`），杜绝了二次报错。

---

## 📈 部署与使用建议

1. **飞书端配合:** 如果您希望在飞书前端明确看到生成的视频叫什么，请在飞书《剧本拆解表》中新增一列**文本类型**的字段并命名为 `视频文件`。
2. **安全无感知:** 若您不想新建上述列也无妨，引擎的闭环容灾确保它依然会将任务状态推向 `已完成`，并在企微通知你本地存放位置，避免产生 Zombie 僵尸任务。

---
*Created by Antigravity AI Engine during debug sprint.*
