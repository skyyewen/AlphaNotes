# AlphaNotes 前端

股票研究与复盘工作台界面，支持研究上下文配置、资料上传、同步研究、引用展示、确认归档和历史只读回放。

技术栈：Vue 3、TypeScript、Vite、Pinia、Vue Router、Tailwind CSS、Axios、markdown-it、Vitest。后端采用 FastAPI、LangGraph、DuckDB、Chroma、SQLite。

## 安装与启动

使用 Node.js 22.12+ 的 22.x 版本，或满足 `package.json` engines 的版本。在仓库根目录执行：

```powershell
cd workbench-front-dev
npm ci
Copy-Item .env.example .env.local
npm run dev
```

访问 `http://127.0.0.1:5173`。模板已设置 `VITE_ENABLE_RESEARCH_API=true`；先在后端填写模型密钥并启动服务，即可按 [演示数据说明](../demo-data/README.md) 查询日线、选择演示策略并归档回放。修改环境变量后需重启 Vite。

## 环境变量

| 变量 | 模板值 | 用途 |
| --- | --- | --- |
| `VITE_API_BASE_URL` | 空 | 同源 API；部署时可设置后端地址 |
| `DEV_API_PROXY_TARGET` | `http://127.0.0.1:8000` | Vite 开发代理 |
| `VITE_ENABLE_RESEARCH_API` | `true` | 启用本地后端研究接口 |

`VITE_*` 配置可能进入浏览器产物，禁止填写模型 API Key。

## 系统能力

- 配置标的、研究问题、资料及行情截止日、可选策略版本。
- 上传 Markdown 资料并展示索引状态。
- 同步获取 Agent 研究结果、步骤与引用。
- 报告支持 Markdown 标题、列表、表格、代码块和链接；只为当前快照中的有效引用生成定位按钮。原始 HTML 与远程图片禁用，复制保留报告原文，历史回放复用同一渲染组件。
- 确认服务端单表快照归档，历史回放不重新调用模型。
- 界面示例使用虚构资料；API 失败不会静默替换为示例。

当前研究采用同步 JSON。“停止等待”仅中止浏览器接收，不表示后端任务取消。

## 主要接口

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `GET / POST` | `/api/documents` | 资料列表与上传 |
| `GET` | `/api/strategies` | 策略目录 |
| `POST` | `/api/research/run` | 同步研究 |
| `POST` | `/api/research/{run_id}/confirm` | 确认归档 |
| `GET` | `/api/records` | 归档列表 |
| `GET` | `/api/records/{run_id}` | 历史回放 |

## 质量检查

```powershell
npm run typecheck
npm run test:run
npm run build
```

质量门禁：70 项 Vitest 测试通过，类型检查与生产构建通过。浏览器已用合成接口验证 Markdown 历史回放、引用切换、原文复制，以及 HTML／远程图片阻断；不代表真实模型或外部服务验收。
