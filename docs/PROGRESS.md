# 开发进度与验收依据

依据 `dev-kit/01-开发任务拆解.md`，按 M1 → M2 → M3/M4/M5 的依赖顺序推进。
本表记录当前证据，不用测试数量或单批提交代替整阶段验收。
开发分支 `codex/continue-development`；每个可复核批次提交并推送。
用户指示：无法本地验证的项暂留待后续验证，其余计划持续开发到完成。
整体 M1–M4 已完成（外部依赖项暂留）；M5 待办继续保留。

## 当前优先级

1. M5 加固（14 项）：容器、记忆向量、预算、熔断、备份等。
2. 暂留项：真实飞书投递、真实 launchd、真实模型 URL 摘要、真实 GitHub/Gitea
   API 联调（E2E-05/07/09/10）、Mac mini 外部证据、macOS GUI 驱动后端
   （pyobjc/AppleScript/Quartz/Vision）、SortableJS 页面交互实测。

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

M2 已完成（21 项中 17 项本地验证；外部依赖项暂留）：

| ID | 当前证据 |
|---|---|
| M2-01~03 | `scm/base.py` 契约 + `scm/github.py` / `gitea.py` 双平台 10 方法适配器，27 项契约测试 |
| M2-04 | `workspace/mirror.py`：bare 镜像 + worktree + pa/&lt;task_id&gt; 分支识别 + 写锁 + prepare 限时 |
| M2-05/06 | Webhook：HMAC/时间戳/限流拉黑/X-Delivery 幂等，10 项测试 |
| M2-07 | `hub/subscriptions.py`：线程订阅/路由/bot 识别/14 天过期清理 |
| M2-08~10 | `ci/risk.py` 硬规则 + `merge_gate.py` 3×3 矩阵，13 项测试 |
| M2-11~13 | `ci/review.py` 三级审查 + `pipeline.py` 8 阶段 + `hooks.py` 发布核实（version_endpoint 用 merge-base 判定），10 项测试 |
| M2-14 | Client 层 D17 护栏：agent 身份禁调 merge 端点；merger 身份专用于合并 |
| M2-15 | `ci/communication.py`：PR/评论首行 Agent 标记、审查意见处理模板、重复改动关 PR |
| M2-16 | `notify/cards.py`：决定卡片（单问题/选项模板/记为先例复选）+ 回调四重校验 + decision.submitted 事件 |
| M2-17 | `judge/precedents.py`：台账（memories 表）、bigram 关键词检索（FTS5 中文整句单 token 限制已绕开，向量阈值 M5 接入）、md 镜像导出/解析、四类永不自动 |
| M2-18 | `judge/timeout.py`：24h 保守动作 + suspended + 一次性提醒 |
| M2-19 | `tools/registry.py` 六工具注册 + `tools/mcp_server.py` 127.0.0.1 单任务一次性 Token + 白名单 + 必问标签过判断层 |
| M2-20 | `skills/assets/sample-code/`：skill.yaml + prompt.md（真实平台闭环属 E2E-05，暂留） |
| M2-21 | E2E-06（三轮退回升级卡片）/ E2E-08（先例勾选免二次卡片）本地闭环通过；E2E-05/07/09/10 需真实平台，暂留 |

M3 已完成（11 项中 9 项本地验证；浏览器拖拽交互与 SortableJS 静态资源本地化待部署实测，端点层已验证）：

| ID | 当前证据 |
|---|---|
| M3-01 | `store/roles.py`：Agent/Executor/Hub/Settings Facade，D21 结构护栏（AGENT_FORBIDDEN_ATTRS 测试覆盖） |
| M3-02 | `api/app.py` REST 面：owner/agent 双令牌，写端点 require_owner + audit_log，读端点 require_agent |
| M3-03 | `config/versions.py`：base_revision 409 冲突、同内容无操作、版本链 + git 提交、drop_same_as_default |
| M3-04 | `console/auth.py` + `console/routes.py`：TOTP RFC 6238、PBKDF2、签名 Cookie（HttpOnly/SameSite=strict）、CSRF 派生；/console/login 等会话路由 + 页面守卫 |
| M3-05 | `alembic 0007_task_stage_view`：7 阶段 first-match-wins 视图 |
| M3-06 | `console/reasons.py`：约 30 原因码中文翻译 + stage_label 七桶 |
| M3-07 | `console/overview.py`：只显示 owner/* 四类等待项 + 系统问题 + counts（数字=列表条数） |
| M3-08 | `/skills:reorder`、`/rules:reorder` 端点 + `reorder_rules`（rules.yaml 写回 + 审计）；SortableJS 页面交互待部署实测 |
| M3-09 | `console/pages.py` + 11 个 Jinja2 模板：总览/队列/任务详情/决定/引擎/观察/成本/配置/审计/搜索/登录；破坏性操作 data-confirm 二次确认 |
| M3-10 | `console/search.py` + `/api/v1/search`：文本/ID/#ref/URL 四形态检索事件、线程、任务 |
| M3-11 | `console/testing.py` dry_run_rules/dry_run_skill + `POST /rules:test`、`POST /skills/{id}:test`、`pa rule test`、`pa skill test`（只返回命中，不执行） |

M4 已完成（8 项中逻辑层全部本地验证；macOS 图形会话依赖项暂留）：

| ID | 当前证据 |
|---|---|
| M4-01 | `engines/gui/driver.py`：GUIDriver 契约 + SyntheticDriver 测试后端（剪贴板快照/恢复、user_active 防打扰）；pyobjc/AppleScript/Quartz/Vision 真实后端随 Mac mini 部署暂留 |
| M4-02 | `engines/gui/playbook.py`：§8.4 八种步骤类型、前置条件（驱动可用/用户占用键鼠）、失败即 ui_drift |
| M4-03 | `procs/gui.py`：全局 GUI 单锁、只领 C 档、通知暂停、owner_busy_timeout 等待 |
| M4-04 | `engines/gui/playbooks.py`：codex_desktop/doubao_desktop/workbuddy_desktop 三剧本，dry-run 全部通过 |
| M4-05 | `engines/gui/limits.py`：risk_ceiling=low、repo_write 拒绝、external_message/spend_money 禁用、confidence 0.6 钳制、c_tier_auto_close 显式声明 |
| M4-06 | `engines/gui/convert.py`：固定模板 + 一次低价模型调用，confidence 钳制、坏输出转人工 |
| M4-07 | `engines/gui/selfcheck.py`：截图 7 天清理（不上传）、每日 dry-run 自检、连续 3 次失败触发熔断 |
| M4-08 | `pa engine playbook record/dry-run`：record 为 Mac mini 暂留占位，dry-run 只打开不提交 |

M5（14 项）按开发资料包继续，当前尚未验收。
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
- M2 批1（SCM/Webhook/风险/矩阵/授权/角色/执行变量）：`test_scm_adapters.py` 27 项、
  `test_risk_and_merge_gate.py` 13 项、`test_webhooks.py` 10 项、`test_m2_core.py` 9 项；
  提交 86c9888，全量 391 项。
- M2 批2（工作区/订阅/审查/流水线/钩子）：`test_workspace_and_subs.py` 10 项、
  `test_ci_pipeline.py` 10 项；全量 411 项。
- M2 批3（身份护栏/沟通/决定卡片/先例/超时）：`test_decisions_and_precedents.py`
  14 项；全量 425 项。
- M2 批4（工具注册/MCP/sample-code/E2E 隔离）：`test_tools_and_sample_code.py` 15 项、
  `tests/e2e/test_m2_e2e.py` 2 项；单元全量 440 项，E2E 6 项（含 M1 的 4 项），
  Ruff 通过。
- M3 批1~3（角色 Facade/REST/配置版本/task_stage/原因码）：`test_console_core.py`
  16 项、`test_config_versions.py` 7 项、`test_api_resources.py` 12 项；全量 475 项。
- M3 批4（登录/总览）：`test_console_auth_overview.py` 10 项、`test_console_routes.py`
  8 项；全量 491 项；修复 _new_session 与 verify_session 的 CSRF 派生不一致。
- M3 批5（排序/搜索/干跑）：`test_reorder_search_testing.py` 9 项；全量 500 项。
- M3 批6（页面模板）：`test_console_pages.py` 8 项；全量 508 项，Ruff 通过。
  模板 `dict.items` 与 Jinja2 方法冲突已改用中括号访问。
- M3 期间新增端点全部走 owner/agent 双令牌测试；agent 令牌对 reorder 与
  decisions:answer 返回 403 已有断言。
- M4 批1（GUI 全层）：`test_gui_tier.py` 18 项（剧本解析/执行/ui_drift/防打扰、
  C 档硬限制、转换钳制、GUI 锁与 owner 等待、截图留存、自检熔断、三剧本
  dry-run、GuiEngine 适配器、CLI record/dry-run）；全量 527 项，Ruff 通过。
  EngineConfig 增加 playbook/pricing 字段；gui 适配器注册进 pa.engines 入口点。
