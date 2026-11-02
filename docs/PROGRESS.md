# 开发进度与验收依据

依据 `dev-kit/01-开发任务拆解.md`，按 M1 → M2 → M3/M4/M5 的依赖顺序推进。
本表记录当前证据，不用测试数量或单批提交代替整阶段验收。
开发分支 `codex/continue-development`；每个可复核批次提交并推送到 Gitea。
用户已要求本批“暂停研判与恢复”收尾提交推送后暂停开发；恢复需用户明确指示。
整体 M1–M5 尚未完成，以下待办继续保留。

## 当前优先级

1. M1-21：隔离配置 E2E-01–04 已完成（本批）；Mac mini 真实外部证据仍待补。
2. M1-16/19：失败通知补发执行器与 rule mode/effect CLI 已实现（本批）。
3. M1-17：真实模型 URL 摘要仍待独立验收。
4. R-23 / M1-13–15：Codex 单次内部迭代限制仍待核实。
5. M1-18：对照三个独立进程的部署模型再审计。
6. 继续 M2 代码平台闭环、M3 控制台、M4 桌面引擎、M5 加固，逐项验收。

## M1 验收清单

| ID | 当前证据 | 尚待验证或补齐 |
|---|---|---|
| M1-01 | uv 锁定依赖、包/CLI/entry points，已有构建检查 | 新批次持续回归 |
| M1-02 | 已实现文件严格校验、ENV 插值、惰性 secret 引用；新增 schedules | 尚未支持的其他 PRD 配置文件 |
| M1-03 | env/keychain 与 secret CLI | 完整 secrets 配置、启动入口的权限校验审计 |
| M1-04 | structlog、凭证打码与 SDK 票据回归 | JSONL/记忆等出口与全量日志验收 |
| M1-05 | Alembic 基线、effects 租约、调度存储升级 | 持续保留升级路径测试 |
| M1-06 | 状态矩阵、乐观锁及同事务审计测试 | 无新增实现缺口声明 |
| M1-07 | 发件箱 runner、重试/幂等/租约与飞书 handler | 对照严格保序、系统问题及崩溃时点再审计 |
| M1-08 | manual/cron 事件；持久化调度、原触发点去重、失败原子回滚、真实调度器重启测试 | 新配置正式服务升级后的实测 |
| M1-09 | 规则、人员、接单模式和主题合并测试 | 模型研判属于 M5；不得将 pending 当已自动接单 |
| M1-10 | 槽位、租约、原子引擎预留和四线程争抢回归；Skill concurrency 生效 | max_parallel 与完整派单验收 |
| M1-11 | 排序维护及性质测试 | 无新增实现缺口声明 |
| M1-12 | 持久暂停、到期顺延、结果检查点、恢复研判、失败/退避/取消/并发与竞态测试 | 真实部署 E2E-03 验收 |
| M1-13 | CLI 适配器；Skill 选择/原子槽位、剩余轮数请求、失败用量提取 | Codex 内部迭代限制、可用登录、完整接口契约 |
| M1-14 | Skill 资产、8 段提示词、回执校验、恢复提示测试 | 对照全部失败条件再审计 |
| M1-15 | 重试/重派、人工门禁、累计 token/时间和持久轮数额度 | Codex 单次内部上限、原因码及全部判断条件 |
| M1-16 | 分级、合并窗口、本地兜底、backfill 标记与恢复后自动补发执行器 | 真实飞书恢复场景实测 |
| M1-17 | 文本链路；URL 抓取、权限/资源限制、失败转人工与假引擎摘要回归，公共 HTTPS 实测 | 真实模型 URL 摘要验收 |
| M1-18 | 基础 API、鉴权、分页、单例锁和退出测试 | 对照三个独立进程的部署模型 |
| M1-19 | 基础 CLI、JSON 输出与 effect retry/drop、rule mode | agent 令牌权限边界（M3） |
| M1-20 | 已有本机 LaunchAgent 安装和运行记录 | 对当前版本执行启动/重启/故障拉起验收 |
| M1-21 | 隔离配置 E2E-01–04（真实子进程、SQLite、假引擎与监督重启） | Mac mini 真实 launchd 与飞书外部证据 |

M2（21 项）、M3（11 项）、M4（8 项）、M5（14 项）按开发资料包继续，当前尚未验收。
远期 F-01–03 保持 PRD 定义的预留范围。

## 本批验证

- 累计执行限制：`tests/test_execution_limits.py`，13 项；提交 `bf08c39`，已推送。
- 持久调度：`tests/test_schedules.py`，14 项；包括真实 APScheduler 重启、重复投递、
  事件/游标事务回滚、暂停、observe、禁用/更新及时间区间。
- 调度批次全量 259 项通过；原有 Starlette TestClient 弃用提示仍存在。
- URL 读取：`tests/test_read_url.py`，32 项；公共 GitHub robots 文本真实读取通过，
  不是仅验证 URL 字符串传递。全量测试现为 291 项。
- 当前开发成果尚未升级 Application Support 中的正式服务。
- 引擎选择与原子槽位：`tests/test_engine_selection.py`，17 项；全量现为 308 项。
- 轮数额度：`tests/test_turn_allowance.py`，15 项；全量现为 323 项。
  完整 R-23 的边界见 `execution-limits.md`，不把预留值当作实际模型轮数。
- 暂停研判与恢复：`tests/test_pause_judgement.py`，15 项；全量现为 338 项。
  实现及验证边界见 `pause-recovery.md`。
- 补发执行器与规则/发件箱 CLI：`tests/test_backfill_and_cli.py`，7 项；
  隔离 E2E：`tests/e2e/test_m1_e2e.py`，4 项（E2E-01–04，真实子进程与 SQLite，
  E2E-02 以监督重启模拟 launchd）。全量现为 345 项；Ruff 与构建通过。
  E2E 的外部边界（真实 launchd、飞书投递、真实模型）仍待 Mac mini 验收。
