# AnythingLLM 文档上传工具

一个单文件的轻量网页工具，用于把本地文档上传到本机运行的 [AnythingLLM](https://anythingllm.com) 服务，并自动解析、向量化到指定工作区，供后续 RAG 检索问答使用。

## 功能特性

- **API Key 门禁**：进入工具前需粘贴 AnythingLLM API Key，可勾选「记住我」保存在本机浏览器，下次自动进入；也可随时切换 Key 或退出
- **工作区下拉选择**：自动拉取工作区列表，支持下拉选择目标工作区并手动刷新；记住上次选择
- **拖拽或点击选择文件**：支持多文件批量上传
- **显示真实上传进度**：用 `XMLHttpRequest` 展示跨文件连续的上传进度；服务端解析/向量化阶段显示不确定进度动画
- **上传结果清晰区分**：成功 / 失败一目了然，失败附带友好错误分类（网络、Key 无效、格式不支持、超时、服务端错误）
- **本地持久化**：上传记录与错误日志分别保存在 localStorage（各最多 50 条，可清空）

## 环境要求

- 本机已安装并启动 **AnythingLLM**（默认监听 `http://localhost:3001`）
- 已至少创建一个工作区（工具通过下拉列表选择目标工作区）
- 现代浏览器（Chrome / Edge / Firefox 等）

## 使用说明

1. 启动 AnythingLLM 服务，确保 `http://localhost:3001` 可访问。
2. 在 AnythingLLM 的「设置 → API Key」中生成并复制一个 API Key。
3. 用浏览器直接打开 `upload.html`。
4. 粘贴 API Key 进入；如勾选「记住我」，下次打开会自动进入（如需更换 Key，点右上角「切换 Key / 退出」）。
5. 在目标工作区下拉中选择要上传到的工作区（如列表为空，可点「刷新列表」）。
6. 点击上传区域选择文件，或直接将文件拖入（可多选）。
7. 点击「上传到「工作区名」」，等待上传与向量化完成。
8. 在 AnythingLLM 对应工作区中即可对该文档进行检索问答。

## 配置

工具的 API 服务地址在 `upload.html` 脚本顶部的 `API` 常量中（默认 `http://localhost:3001/api`），如服务地址不同需同步修改。API Key 无需修改源码，直接在页面中运行时输入即可。

## 工作原理

1. 页面加载时 `GET /api/v1/workspaces`：拉取工作区列表，供用户在下拉中选择目标（记住上次选择）。
2. `POST /api/v1/document/upload`：以 multipart 表单上传文件，并通过 `addToWorkspaces` 字段挂载到所选工作区。
3. 服务端完成解析与向量化后，前端展示结果；失败时按状态码归类为网络 / Key 无效 / 格式不支持 / 超时 / 服务端错误，并写入错误日志。

上传进度使用 `XMLHttpRequest` 获取（`fetch` 不提供上传进度事件），并设置了 5 分钟超时防止大文件 / 向量化卡死。

## 目录结构

```
anythingLLMServer/
├── upload.html   # 单文件前端工具（HTML + CSS + JS）
└── README.md     # 本说明文档
```

## 本地存储键

工具使用以下 localStorage 键保存状态，均在浏览器本地，不会上传：

| 键 | 用途 |
| ---- | ---- |
| `anythingllm_api_key` | 「记住我」保存的 API Key |
| `anythingllm_workspace_slug` | 上次选择的工作区 slug |
| `anythingllm_upload_history` | 上传记录（最多 50 条） |
| `anythingllm_error_log` | 错误日志（最多 50 条） |

## 注意事项

- **面向本机个人自用**：API Key 保存在浏览器本地，页面面向 `localhost` 服务，请勿部署到公网或共享给他人。
- **API Key 不硬编码**：Key 在页面运行时输入，避免写入源码；但仍建议定期轮换。
- 上传目标通过下拉选择工作区；如 AnythingLLM 没有任何工作区，需先在服务端创建。
- 本项目仅包含前端页面，不包含服务端代码，依赖本机已运行的 AnythingLLM 服务。
