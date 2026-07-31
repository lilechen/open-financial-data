# OFD operation reference

## Contents

- Natural-language intent map
- Command discovery
- Safe execution workflow
- Automation contract
- Legacy repository fallback
- Result checklist

## Natural-language intent map

| Intent | Logical command | Mutation |
|---|---|---:|
| Initialize an empty project | `ofd init` | Yes |
| Build a historical dataset | `ofd bootstrap` | Yes |
| Bring configured datasets current | `ofd update` | Yes |
| Fill an explicit historical interval | `ofd backfill` | Yes |
| Find and restore missing/failed partitions | `ofd repair` | Yes |
| Explicitly replace partitions or the whole Dataset | `ofd rebuild` | Yes |
| Inspect coverage and resources | `ofd status` | No |
| Run data-quality checks | `ofd validate` | Normally no |
| Diagnose local state or Provider APIs | `ofd doctor` | No canonical writes |
| Inspect run history | `ofd jobs` | No |
| Inspect live terminal state | `ofd tui` | No |
| Search logs/audit/metrics | `ofd logs`, `ofd audit`, `ofd metrics` | No |
| Remove cache or temporary data | `ofd clean` | Yes |
| Generate external scheduling config | `ofd schedule` | May write config |
| Explain which Provider serves a Dataset | `ofd config explain-source` | No |
| Validate all configured contracts | `ofd config check` | No |
| Compare overlapping Providers | `ofd compare-providers` | No |
| Discover canonical data products | `ofd schema list/show` | No |

## Built-in source fallback

The `cn-equity-daily` preset ships with automatic fallback: the primary
`akshare.equity_daily` (Eastmoney `stock_zh_a_hist`) is backed by
`akshare.equity_daily_sina` (Sina `stock_zh_a_daily`) with
`fallback_policy: automatic`. When the primary endpoint is unreachable, the
CLI closes the failed run, starts a new run, and replays the whole planned
range on Sina — no user action required. Lineage is preserved per batch:
canonical rows record the actual `provider_endpoint` (`stock_zh_a_hist` or
`stock_zh_a_daily`) and `run_id`, so mixed-history partitions remain
auditable. `ofd update` therefore keeps working during an Eastmoney outage.
Inspect the transition with `ofd audit --event provider.fallback.triggered`.


## Command discovery

Never assume the design command exists. Discover the installed interface:

```bash
command -v ofd
ofd --help
ofd <command> --help
python -m open_financial_data --help
```

Inspect `pyproject.toml` console scripts and source entry points when the command is unavailable.

## Safe execution workflow

1. Resolve project path, Dataset, Provider, market, and date range from the request and local configuration.
2. Inspect active jobs, project locks, free space, and Provider health.
3. Use `--dry-run` when implemented for bootstrap, large backfill, repair, cleanup, or deep diagnostics.
4. Summarize the plan if estimated calls, time, or storage are material.
5. Execute using the public CLI or Python API.
6. Verify the final Job status, committed watermark, Manifest, and failed/quarantined tasks.
7. Report partial success accurately.

For source-route changes, inspect Provider coverage by partition. Keep history only at a complete new partition boundary with `--provider-policy keep-history`; replace old history explicitly with `ofd rebuild --scope range|all --apply`.

Do not ask for a date range when `update` can infer it from Watermarks and the trading calendar. Ask only when bootstrap or backfill scope cannot be derived safely.

## Automation contract

Preferred external invocation:

```bash
ofd update \
  --project /absolute/project/path \
  --format json \
  --non-interactive
```

Conceptual exit codes from the architecture design:

| Code | Meaning |
|---:|---|
| 0 | Success or no update needed |
| 2 | Invalid argument or configuration |
| 3 | Project lock conflict |
| 4 | Provider authentication/authorization failure |
| 5 | Provider temporarily unavailable or rate-limited |
| 6 | Contract drift or quality failure |
| 7 | Partial success |
| 8 | Insufficient local resources |
| 130 | Cancelled/SIGINT |

这些退出码是稳定目标；执行前仍以当前 `--help` 和实现为准。被中断的在线运行使用 `ofd repair --resume-run RUN_ID --apply` 恢复。Provider 切换默认 strict；只有在新完整分区边界才使用 `--provider-policy keep-history`。

## Legacy repository fallback

Before OFD is implemented, use existing repository evidence:

```bash
du -h -d 3 data
find data -type f | wc -l
find data/raw -path '*/manifests/*' -type f | sort
python3 scripts/diagnostics/check_data.py --help
python3 scripts/diagnostics/evaluate_data_dates.py --help
```

Read script help or source before executing it. Existing scripts may predate stable non-interactive contracts. Do not migrate or rewrite legacy CSV data without an explicit implementation request.

## Result checklist

For data operations, capture when available:

```text
run_id
correlation_id
command/status
dataset/schema_version
provider/upstream endpoint
requested date range
planned/succeeded/failed/empty/quarantined tasks
rows received/accepted/written/rejected
observed/expected/committed watermark
output size and free disk
manifest path
audit status and relevant event IDs
```

For Doctor, capture:

```text
probe level
dependency and SDK version
endpoint category, not secret-bearing URL
latency
health classification
quota/request estimate
field additions, removals, and type changes
```
