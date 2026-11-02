# AlphaNotes 后端

股票研究与复盘工作台后端，提供带引用的资料检索、受控工具调用、研究结果保存与确认归档。

技术栈：Python 3.12、FastAPI、LangGraph、DuckDB、Chroma、SQLite。

## 安装与启动

在仓库根目录执行，确认 `python --version` 为 Python 3.12：

```powershell
cd workbench-backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock.txt
Copy-Item .env.example .env
.\.venv\Scripts\python.exe -m app
```

默认地址为 `http://127.0.0.1:8000`，健康检查 `/api/health`，接口文档 `/docs`。
启动与健康检查无需密钥。仓库根目录已提供 [小型合成日线和演示 Wiki](../demo-data/README.md)，模板路径可直接使用。填写 `.env` 中的 `DEEPSEEK_API_KEY`，按需要调整 `DEEPSEEK_BASE_URL` 和 `DEEPSEEK_MODEL`，即可使用示例数据发起研究。

## 环境变量

在本目录 `.env` 中配置，密钥仅用于后端。

| 变量 | 用途 |
| --- | --- |
| `APP_HOST` / `APP_PORT` | 监听地址，默认 `127.0.0.1:8000` |
| `DEEPSEEK_API_KEY` | 研究与调试聊天的模型密钥 |
| `DEEPSEEK_BASE_URL` / `DEEPSEEK_MODEL` | 模型服务地址与模型名 |
| `SILICONFLOW_API_KEY` | RAG 重排服务密钥 |
| `RAG_DATA_DIR` | RAG 本地索引目录，默认 `data/rag` |
| `STOCK_DAILY_PARQUET_PATH` | 默认 `../demo-data/market/daily.parquet`，约 10 KB 合成样本 |
| `STRATEGY_WIKI_ROOT` | 默认 `../demo-data/strategy-wiki`，含 3 个演示策略 |
| `RESEARCH_DB_PATH` | 快照库，默认 `data/research/research.sqlite3` |

相对数据路径以本后端目录为基准，`../demo-data` 指向仓库根目录。`.env.example` 中的密钥为空；换用自己的行情或 Wiki 时再修改对应路径，私有数据仍保持 Git 忽略。

RAG 初始为空库。行情和 Wiki 演示只需配置研究模型，不需要重排密钥。上传自己的 Markdown 进行 RAG 演示时，首次入库可能下载 Chroma 默认嵌入模型；正向检索重排需填写 `SILICONFLOW_API_KEY`。

## 核心能力

- **Agent 状态机**：LangGraph 组织模型、工具与引用校验循环，限制调用次数和等待时间。
- **RAG 混合检索**：SQLite BM25、Chroma Dense、RRF、重排与 Parent/Child 扩展；按标的和资料日期筛选证据，返回实际引用。
- **受控日线与 Wiki 工具**：DuckDB 只读本地未复权日线；读取已登记 Wiki 版本。服务端固定标的、截止日和路径范围，模型不能提交任意 SQL 或文件路径。
- **单表快照**：SQLite `research_runs` 保存完整 payload；确认只更新归档状态，历史回放读取冻结快照。

研究使用同步 JSON。停止等待不表示后端取消；缺少证据时保留缺失状态。当前按单进程、单 worker 使用。

## 关键 API

| 方法与路径 | 用途 |
| --- | --- |
| `GET /api/health` | 健康检查 |
| `GET /api/documents` | 文档与索引状态 |
| `POST /api/documents` | 上传 Markdown 与来源元数据 |
| `DELETE /api/documents/{document_id}` | 删除文档及索引 |
| `POST /api/retrieve` | 检索证据及引用 |
| `GET /api/strategies` | 策略目录 |
| `POST /api/strategy-wiki/query` | 查询指定 Wiki 规则 |
| `POST /api/research/run` | 同步研究并保存快照 |
| `GET /api/research/{run_id}` | 查询已保存运行 |
| `POST /api/research/{run_id}/confirm` | 确认归档 |
| `GET /api/records` | 已归档记录列表 |
| `GET /api/records/{run_id}` | 历史快照回放 |

完整参数见 `/docs`。日线通过 Python 工具调用。

## 测试与质量门禁

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
```

质量门禁：296 项 Pytest 通过，2 项可选检查跳过。测试与替身通过不代表真实外部模型服务已验收。
