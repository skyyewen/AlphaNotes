# AlphaNotes

本地股票研究与复盘工作台。围绕标的、资料截止时间和策略版本检索证据，通过研究 Agent 生成带引用的结果，经用户确认后归档，并按保存时的完整快照回放。

## 技术与能力

- **后端**：Python 3.12、FastAPI、LangGraph、DuckDB、Chroma、SQLite。
- **前端**：Vue 3、TypeScript、Vite、Pinia、Vue Router、Tailwind CSS、Axios、markdown-it。
- **Agent 状态机**：组织模型与工具循环，校验引用并限制调用次数及等待时间。
- **RAG 混合检索**：Parent/Child、SQLite BM25、Chroma Dense、RRF 与重排，按标的和资料日期限定证据范围。
- **受控工具**：只读日线查询与策略 Wiki 读取，服务端约束上下文、路径和工具参数。
- **单表快照**：SQLite 保存完整研究结果，确认归档与历史回放复用冻结快照。
- **报告展示**：Markdown 渲染标题、列表、表格与代码块；有效引用可点击定位，原始 HTML 和远程图片禁用。

研究链路采用同步 JSON：发起研究 → 保存结果 → 人工确认 → 历史回放。浏览器停止等待不表示后端已经取消。

## 本地运行

按 [后端安装说明](workbench-backend/README.md) 配置 Python 3.12 虚拟环境并启动服务，再按 [前端安装说明](workbench-front-dev/README.md) 安装依赖并启动界面。仅从 `.env.example` 创建本地配置，模型密钥只放在后端。

仓库根目录自带 [小型演示数据](demo-data/README.md)：3 个标的共 360 条合成日线（Parquet 约 10 KB）和 3 份虚构策略规则。默认配置已指向这些文件，无需完整行情或私人 Wiki。证券代码仅用于测试映射，样本价格、量额和规则都不代表真实市场或实际策略。

复制后端 `.env.example` 为 `.env`，填写 `DEEPSEEK_API_KEY`（需要时调整服务地址和模型名）；复制前端 `.env.example` 为 `.env.local` 后已启用本地 API。启动前后端，即可按演示数据说明发起研究、确认归档并回放。

模型密钥和运行数据库不随仓库分发。RAG 初始为空库，不影响行情和 Wiki 演示；需要资料检索时再上传自己的 Markdown，首次向量入库会下载 Chroma 默认嵌入模型，命中重排需要 `SILICONFLOW_API_KEY`。

## API 与验证

主要入口为 `/api/documents`、`/api/retrieve`、`/api/strategies`、`/api/research/run`、`/api/research/{run_id}/confirm` 和 `/api/records`。完整接口见后端 `/docs`。

后端运行 `python -m pytest`；前端运行 `npm run test:run`、`npm run typecheck` 和 `npm run build`。质量门禁与安装命令见各目录 README。

项目用于有来源依据的研究整理和人工确认，不执行交易，不承诺收益或严格历史时点重建。

## 项目截图

![首页](F:\Desktop\github\AlphaNotes\首页.png)

![对话](F:\Desktop\github\AlphaNotes\对话.png)