---
name: open-financial-data
description: Operate, diagnose, inspect, configure, or develop the OpenFinancialData (OFD) project through natural language. Use when working in this repository on financial data bootstrap, updates, backfills, repairs, status, Doctor/provider health checks, schemas, adapters, storage, manifests, or OFD implementation tasks; also use when a user asks conversationally whether local market data is current, healthy, complete, or consuming too many resources.
---

# OpenFinancialData

Translate natural-language requests into safe OFD inspection, CLI operations, or implementation work. Treat the repository as the source of truth and distinguish implemented behavior from architecture proposals.

## Start every task

1. Locate the repository root and read `docs/open-financial-data-design.md` only when architecture or an unimplemented feature matters.
2. Inspect the actual implementation before proposing commands:

   ```bash
   rg --files | sed -n '1,160p'
   rg -n "\bofd\b|OpenFinancialData|open_financial_data" pyproject.toml src cli tests docs 2>/dev/null
   ```

3. Determine the project stage:
   - If the `ofd` CLI/package exists, inspect `--help` and use only supported arguments.
   - If OFD is not implemented, explain that briefly and either inspect the legacy data or implement the requested scoped feature.
   - Never present a design-document command as already available without verifying it.
4. Inspect project-local instructions such as `AGENTS.md` or `CLAUDE.md` when present.

## Map natural language to an operation

Use [references/operations.md](references/operations.md) for intent mapping, safe command patterns, exit codes, and result reporting.

Common mappings:

| User intent | Preferred operation |
|---|---|
| “从 2020 年开始建立 A 股日线库” | bootstrap |
| “更新到最新” | update |
| “补一下去年三月的数据” | backfill |
| “看看缺了哪些股票/日期” | status or validate |
| “修复失败和缺口” | repair |
| “AKShare 还能用吗” | doctor provider probe |
| “本地数据占多少空间” | status/storage inspection |
| “定时每天更新” | generate or configure an external schedule |

## Operate safely

- Prefer read-only inspection before mutation.
- Show a dry run or execution plan before a large bootstrap, backfill, repair, cleanup, or deep Doctor request when supported.
- Treat bootstrap, update, backfill, repair, and clean as state-changing operations.
- Do not delete Canonical data unless the user explicitly asks.
- Do not expose tokens, cookies, signed URLs, headers, or environment-variable values.
- Do not bypass the public CLI/Python application service to write Catalog state or advance Watermarks.
- Respect project locks and active jobs; do not remove a lock until verifying its owning process is stale.
- Preserve Provider lineage, Schema version, Mapping version, run ID, and Manifest information.
- Report partial success and quarantined data; do not collapse them into success.

## Inspect data state

When asked whether data is current or complete, report separately:

- observed watermark;
- expected watermark;
- committed watermark;
- asset and partition coverage;
- failed, empty, retrying, and quarantined tasks;
- last successful run and Provider;
- Canonical, Landing, cache, and temporary storage size.

Do not infer completeness from the maximum date of one file. Inspect partition or asset coverage where available.

For the legacy repository, inspect `data/`, manifests, and diagnostic scripts without rewriting existing data. Clearly label results as legacy state rather than OFD Catalog state.

When validating local Canonical data, use the configured Quality Profile and report every rule by ID, severity, status, violations, report path, and run ID. Do not replace configured thresholds with ad hoc assumptions. Treat Schema, primary-key, Catalog, partition checksum, and Watermark failures as integrity failures unless the profile explicitly lowers their severity.

Keep every quality layer decoupled. Schema may declare only data facts and neutral constraint kinds; it must not reference evaluator names, severities, storage readers, or report writers. Quality Policy owns severity and thresholds. Evaluator Registry owns constraint execution. Scanner implementations own storage reads. Quality Engine only resolves contracts, orchestrates evaluation, and produces results. Put operational checks such as Catalog reconciliation, coverage, partition checksums, and Watermarks in Quality Profiles. Do not duplicate a financial constraint in multiple layers.

## Resolve data sources

Do not assume one global Provider. Resolve the configured source route using Dataset, market, asset class, frequency, adjustment, and session. For requests such as “日线用 AKShare，周线用 TuShare,” configure separate deterministic routes and verify each Adapter capability.

Before changing a route, inspect existing partition lineage. Report whether the change will keep mixed history, switch at an explicit date, or rebuild a range. Never silently overwrite old Provider data or mix Providers inside a partition. Use fallback only according to the configured policy and record the actual Provider, Adapter, endpoint, Mapping version, and run ID.

OFD core does not derive weekly bars from daily bars. Only route weekly data to a Provider that supplies it, unless the project explicitly adds a separately identified derived-data extension.

## Diagnose Providers

Use the lightest probe that answers the request:

1. L0 Local: configuration, directories, state DB, disk, locks.
2. L1 Runtime: Python packages, versions, plugin loading.
3. L2 Network: DNS, TCP, TLS, proxy, endpoint reachability.
4. L3 Auth: credential presence and low-cost authentication.
5. L4 Contract: minimal fixed data request and Mapping compatibility.

Run L4 only when requested or needed to diagnose contract drift. Warn about quota usage, keep requests minimal, write no Canonical data, and redact secrets. Distinguish unreachable, unauthorized, rate-limited, dependency error, and contract drift.

## Work with scheduling

Keep scheduling outside the core. Prefer cron, launchd, systemd timer, Airflow, CI, or another scheduler calling the stable non-interactive CLI:

```bash
ofd update --project /path/to/project --format json --non-interactive
```

Verify the real CLI contract before emitting configuration. Scheduling decides when to invoke OFD; core planning decides what data needs updating. Never let scheduling logic write data directly or maintain a second source of truth.

## Implement OFD features

When asked to build or change the project:

1. Read the relevant design sections and inspect existing code/tests.
2. Implement the smallest vertical slice that preserves these boundaries:
   - Dataset Schema defines canonical meaning.
   - Provider Adapter fetches RawBatch.
   - Mapping/Normalizer produces canonical Arrow data.
   - Validator rejects or quarantines invalid batches.
   - Storage publishes atomically.
   - Catalog/Manifest records committed facts.
3. Use Python as the implementation language.
4. Prefer PyArrow for batches, Pydantic for configuration and control models, Parquet for canonical storage, DuckDB for local querying, and SQLite for operational state.
5. Keep Provider SDKs optional; do not import AKShare or TuShare from the core package.
6. Add proportionate tests and run focused verification.

Do not build a generic workflow engine, scheduler, or Adapter programming language unless a concrete implemented use case requires it.

## Report outcomes

Lead with the result. Include:

- operation performed or implementation changed;
- dataset, Provider, and date range;
- committed watermark or explicit reason it did not advance;
- task and row counts when available;
- failures, retries, empty responses, or quarantine counts;
- resource impact for large operations;
- exact next safe command only when useful.

Preserve observability for every operation. Use or propagate a correlation ID when the CLI supports it, capture the run ID, and rely on structured event names rather than parsing free-text logs. Never print secrets or complete Provider responses. For state-changing operations, verify that the audit record and Manifest were committed; if they were not, do not report full success.

For machine-readable operations, preserve stdout JSON and treat stderr as logs. Interpret nonzero exit codes using the reference, but prefer the running implementation's documented codes when they differ.
