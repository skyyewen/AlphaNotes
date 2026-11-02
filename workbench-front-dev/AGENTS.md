# 前端协作约定

适用于本目录。安装、环境变量与接口见 [README.md](README.md)。

- 沿用 Vue 3、TypeScript、Vite、Pinia、Vue Router、Tailwind CSS；通用能力放 `src/shared/`，研究状态与交互放 `src/features/`。
- 先检查 Git 状态、相关源码与测试；只修改任务范围内文件，复用主题变量与共享 UI 组件。
- 请求统一使用 `shared/api/request.ts` 的 Axios 封装；研究使用同步 JSON，失败不得静默回退示例。
- 前端环境模板启用本地研究 API；界面静态示例与实际 API 结果应明确区分。密钥只在后端，禁止进入 `VITE_*`、URL、日志或浏览器存储。
- 保持研究上下文和 `run_id` 隔离，旧请求不得覆盖新视图。
- 归档以后端持久化结果为准，历史回放只读；浏览器停止等待不等于后端取消。
- 报告使用 `shared/components/MarkdownContent.vue` 转换为 Vue 节点；禁用原始 HTML、远程图片和危险链接，仅为当前快照的有效引用生成按钮，保留原文复制与历史回放。
- 接口变化同步后端契约与前端类型；测试不依赖私人资料和本机绝对路径。
- 按改动运行 `npm run typecheck`、`npm run test:run`、`npm run build`；界面变更核验对应交互，说明实际验证范围。
