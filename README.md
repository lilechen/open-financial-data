# OpenFinancialData

OpenFinancialData（OFD）是一个 Schema 优先、数据供应商无关、本地优先的金融数据中台。

项目目标是将 AKShare、TuShare、REST API、SDK 和本地文件等异构来源映射为稳定、可验证、可追溯的标准金融数据集，并通过统一接口管理初始化、更新、诊断和本地数据状态。

> Any provider. One financial data standard.

## 当前状态

本地个人版核心已形成可运行闭环。股票日/周/月、指数/期货/期权日线、复权因子、财务三表、指数成分和行业归属已打通 Provider→Landing→Mapping→Schema→Quality→原子 Storage→Catalog/Manifest/Watermark 全链路；支持 AKShare、TuShare、配置化 REST/CSV 以及 Adapter/Mapping/Schema/Storage entry point 插件。运行具备任务持久化、锁、节流、重试、取消/崩溃恢复、Catalog 驱动 repair、Quarantine、Primary/Fallback、Provider 差异报告、显式 rebuild、Rich 进度和 TUI。

Schema 采用存储中立字段定义，可生成 Pydantic、Arrow 和 SQL。内置股票、ETF、指数、资产主数据、标识符、交易日历、行业、指数成分、期货、期权、财务三表和公司行动等 19 个数据产品；任意内置 Schema 均可通过 Canonical JSONL 原子入库并运行通用质量检查。

## 开发

```bash
python3.13 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
pytest
ofd --help
```

## 已实现命令

```bash
ofd init ./market-data --name my-market-data
ofd status --project ./market-data
ofd init ./cn-market --preset cn-equity-daily
ofd config explain-source \
  --project ./cn-market \
  --dataset market.equity.bar \
  --market CN \
  --frequency 1d
ofd doctor --project ./cn-market --provider akshare
ofd import legacy-csv \
  --project ./cn-market \
  --source /path/to/by_symbol \
  --dry-run
ofd import legacy-csv \
  --project ./cn-market \
  --source /path/to/by_symbol \
  --apply
ofd validate \
  --project ./cn-market \
  --dataset market.equity.bar \
  --profile equity-daily-standard
ofd bootstrap --project ./cn-market --start 2024-01-01 --dry-run
ofd update --project ./cn-market --format json --non-interactive
ofd backfill --project ./cn-market --start 2024-01-01 --end 2024-01-31
ofd repair --project ./cn-market --start 2024-01-01 --end 2024-01-31 --dry-run
ofd repair --project ./cn-market --resume-run RUN_ID --apply
ofd rebuild --project ./cn-market --scope range --start 2024-01-01 --end 2024-01-31 --apply
ofd compare-providers --project ./cn-market --provider-a akshare --provider-b tushare
ofd import canonical-jsonl --project ./market-data \
  --dataset reference.asset.master --source ./assets.jsonl --partition-by country
ofd jobs --project ./cn-market
ofd tui --project ./cn-market
ofd logs --project ./cn-market --run-id RUN_ID
ofd audit --project ./cn-market --event storage.write.committed
ofd metrics --project ./cn-market
ofd clean --project ./cn-market
ofd schedule --project ./cn-market --backend launchd
ofd schema list
ofd schema show --dataset reference.option.contract
ofd config check --project ./cn-market
```

数据规范约束由 Dataset Schema 以中立 constraint kind 定义；Schema 不知道执行函数或严重级别。Quality Policy 管理严重级别和阈值，Evaluator Registry 管理执行器，Scanner Protocol 管理存储读取，Quality Engine 只做编排。默认 A 股日线检查九项规则。

数据操作支持稳定 JSON 输出和关联 ID。`doctor --deep` 才会执行一个最小外部请求；普通 Doctor 不写业务数据。默认 `update` 根据预期交易水位计算区间，`--frequency` 可选择 `1d`、`1w` 或 `1mo`。

在线写入支持 `--dry-run`；全市场请求可由 Provider 发现资产，也可用 `--assets` 限定。Provider 历史变化默认严格拒绝；只有显式 `--provider-policy keep-history` 才允许从新完整分区保留混合历史。`rebuild --scope all` 可原子迁移 Provider 或 monthly/daily/by_asset 布局。所有实际参数以 `ofd --help` 为准。

## 项目边界

OFD 负责数据接入、规范化、验证、归档、状态和可观测性，不负责策略、因子、回测、撮合或金融指标计算。

完整架构见 `docs/open-financial-data-design.md`。
