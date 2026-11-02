# 后端协作约定

适用于本目录。安装、环境变量与接口见 [README.md](README.md)。

- 沿用 Python 3.12、FastAPI 和现有分层；HTTP 放 `app/api/`，研究循环放 `app/agent/`，检索放 `app/rag/`，受控工具放 `app/tools/`，快照放 `app/storage/`。
- 配置统一由 `app/core/config.py` 管理；新增变量同步 `.env.example` 与 README，密钥示例必须为空。
- 先检查 Git 状态与相关实现、测试；只修改任务涉及文件，保留已有修改。
- 保持同步研究、服务端上下文约束、引用证据和单表快照契约；接口变化同步前端调用与类型。
- 工具不得绕过标的、日期、路径和调用预算；缺证据不回退到无范围检索或模型记忆。
- 业务数据、模型密钥和本地运行环境不提交；测试使用替身与临时目录，不覆盖真实资料或快照。
- 常规回归不调用真实付费服务，不外发私人资料；真实集成检查应有对应任务授权。
- 使用 `python -m pytest`、`python -m ruff check .`、`python -m ruff format --check .` 检查改动；真实服务验收与替身测试分别说明。
