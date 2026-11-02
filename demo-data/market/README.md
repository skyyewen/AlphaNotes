# 完全合成的日线演示数据

**本目录行情全部由确定性公式生成，价格、成交量、成交额和会话日期均为合成样本，不代表任何证券的真实行情，没有投资含义。**

`daily.parquet` 仅供演示 DuckDB 只读查询、证券代码映射、行情截止日和 Agent 工具链。证券代码仅用于路由与别名匹配。每行还包含 `is_synthetic=true` 与英文合成说明。

- 标的：`601318.SH`（界面输入示例）、`000001.SZ`、`600519.SH`。
- 每个标的 120 行，总计 360 行；截止 2026-10-28。
- 日期仅使用周一至周五，**未扣除法定休市日，不是真实交易日历**。
- 字段兼容后端 `query_stock_data` 的 CNY／1d／none 契约，OHLC、量额均为合法有限正数。
- 校验清单、日期范围、文件字节数和 SHA-256 见 `manifest.json`。

从仓库根目录使用已安装后端锁定依赖的 Python 重新生成：

```powershell
python demo-data/market/generate_synthetic_daily.py
```

生成过程只使用本脚本中的公式，不读取真实行情、模型密钥、用户资料或网络。后端的 `STOCK_DAILY_PARQUET_PATH` 应指向本目录的 `daily.parquet`；相对配置路径以后端目录为基准。
