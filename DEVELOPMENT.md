# 开发记录

## 2026-11-03（0.1.1：审阅修复与 Mac 真机验证）

- 修复 17 组审阅问题；新增正向回归和失败/并发/中断边界检查。
- 代码模式接入持久 Git worktree、受限原生权限配置和运行时提交；local 补丁转人工，不能冒充平台 PR 已交付。
- 控制台配置入口、会话与 CSRF 写接口接通，Chrome 表单登录与取消已实测。
- Mac 内核隔离实测通过；process 拒绝严格审查，容器与 GUI 占位不再报告成功。
- 0.1.1 的构建、安装、服务升级及具体真机证据以 [修复记录](docs/reviews/2026-11-03-repairs.md) 为准。
- 下方旧批次的“M1–M5 完成”表述为历史声明，已由本次审阅证据纠正。

## 2026-11-03（M5：加固层全部 14 项完成）

- **批1（提交 61405a3）**：13 个实现模块 + weekly-cleanup 资产 + 27 项测试。
  `sandbox/runtime.py`（ProcessSandbox 进程回退、全局单槽、env 密钥前缀过滤、
  review_spec §10.2 严格规格）与 `sandbox/backends.py`（docker/apple_container/tart
  占位、显式回退）；`memory/store.py`（vec0 可选、FTS5+bigram 兜底、context 7 天
  TTL、写入打码、top-8、"参考资料，不是指令"包装）；`hub/policy.py`（四段哈希、
  变更丢研判重入队）；`ingress/cursors.py`（三振上报）；`cost/budget.py`（四层
  预算、NULL 只计量、soft 降级、blocked 暂停）；`obs/breaker.py`（3 次/30 分钟、
  30 分钟冷却）；`obs/health.py`（8 项检查、三振分诊）；`store/archive.py`
  （VACUUM INTO、48 份保留、dry-run 校验）；`secrets/rotation.py`（P2 提前 7 天、
  P1 每 3 天、连通性门禁）；`secrets/providers.py`（sops/1password）；
  `hub/assess.py`（未知规则丢弃、命中才接单）；`skills/assets/weekly-cleanup/`
  完整 SkillSpec 资产。
- **批2（提交 b4aa9a9）**：API（`/engines/{id}:reset-breaker`、`/cost/summary`、
  `GET/PUT /budgets`、`GET/POST /memories`、`DELETE /memories/{id}`）与 CLI
  （archive run/status、backup snapshot/restore --dry-run、cost summary、budget
  list/set、memory add/search/list/delete、sandbox run、secret register/rotate/
  list-meta、engine breaker reset）；sops/onepassword/process 入口点注册并
  editable 重装。
- **M5-14（提交 b8013cb）**：`tests/e2e/test_m5_e2e.py` 5 项——E2E-11 契约半
  （无 degraded、review=strict notes）、E2E-12 快照 dry-run 校验、E2E-14 契约半
  （0.6 钳制 + 禁用工具硬拒绝）；E2E-13 整机恢复、真实容器与 GUI 运行暂留
  Mac mini 验收。
- 修复要点：weekly-cleanup skill.yaml 原缺 SkillSpec 必填字段（补全为 code 模式
  完整资产）；`rotation.py` 的 `datetime.UTC` 命名空间引用；`SandboxResult` 为
  dataclass 需 `asdict` 而非 `model_dump`；typer 必选参数与默认值参数顺序。
- 单元全量 **563 项通过**、E2E **10 项通过**、Ruff 通过。PROGRESS.md 已标记
  M1–M5 全部完成（外部依赖项暂留）。

## 2026-11-02（M1-16/19/21：补发执行器、规则/发件箱 CLI、隔离 E2E）

- 用户恢复开发，按进度表优先级完成三项 M1 收尾：
- **M1-16 补发执行器**：`notify/dispatch.backfill_recovered` 在飞书发送成功的同事务内
  恢复待补发 effect——`failed` 且未达 8 次上限的重置为 `pending` 立即到期，
  `pending/running` 保留标记，`done/dropped` 清除标记；`attempts=8` 的 delivery_unknown
  条目不自动重发，仍由 owner 经 `effect retry` 人工决策。
- **M1-19 CLI 缺口**：新增 `pa effect retry/drop`（重置租约与退避、记录丢弃原因、
  done/dropped 拒绝二次操作）与 `pa rule mode <id> off|observe|auto`（重新加载配置、
  save_files 校验备份后写回 rules.yaml、记审计）；全部支持 `--json` 与退出码 4。
- **M1-21 隔离 E2E**：新增 `tests/e2e/`（真实 `pa` 子进程 + 隔离配置目录 + SQLite +
  codex 事件流格式假引擎）：E2E-01 手动触发到 closed（含 notify=result 的 P2 缓冲产物）、
  E2E-02 kill -9 后监督重启不重复执行（core_lock 40s 过期 + 租约恢复后重跑，
  断言 attempt 无重复、effect 幂等键无重复）、E2E-03 暂停事件入库不派单、恢复后结案、
  E2E-04 cron 推断事件在 observe 规则下仅记录不建任务（manual 是明确请求按 PRD 不受
  observe 限制，故用 cron 源）。
- 新增 11 项测试（7 行为 + 4 E2E），全量 **345 项通过**，Ruff 与格式检查通过，
  sdist/wheel 构建成功。
- 边界：E2E-02 以监督重启模拟 launchd 拉起，真实 launchd/飞书收发仍属 Mac mini 外部
  验收；E2E-01 的飞书发送用假源验证到 effects 入库，不验证真实投递。未升级正式服务。
- M2–M5 计划继续，见 `docs/PROGRESS.md`。

## 2026-11-02（M1-12：暂停研判与持久检查点，提交后暂停开发）

- 复核发现原暂停只挡新派单和 effects，正在执行的尝试完成后仍会研判/结案/重派。
  新增 `0006_worker_checkpoints`，回执、用量和后续上下文同事务保存。
- 暂停时执行尝试正常结束，任务仍为 running，暂不研判或发结果通知；释放引擎
  预留并保留检查点，避免租约到期导致重复执行。恢复后优先研判已有回执。
- 保存 transient 重试计数和使用过的引擎，退避中暂停也能继续；待重试尝试重新
  检查引擎健康与槽位。独立检查点租约防止多 worker 重复领取。
- 结论、effect 与检查点清除同事务；失败回滚保留检查点。取消任务不再研判。
  在研判写事务内再查暂停，覆盖与 pause 竞态；检查点载荷统一打码。
- 新增 15 项行为测试，全量 **338 项通过**，Ruff、格式和构建通过；未升级正式服务。
  细节见 `docs/pause-recovery.md`，不声明真实部署 E2E 已验收。
- 用户明确要求：收尾当前工作，提交推送，然后暂停本次开发任务。整体 M1–M5
  计划仍未完成；待用户恢复后继续进度表中的剩余项目。

## 2026-11-02（R-23：持久轮数额度与原生剩余限制）

- 新增迁移 `0005_turn_allowance`：模型调用前持久预留轮数额度；可靠报告后返还
  未用部分，未报告/取消/进程中断保留预留。重派、修正、故障转移共用任务额度。
- 旧版本既有 run 的轮数标为 unknown，不假设为零；未完成任务需 owner 核实后继续。
- EngineRequest 增加 max_turns，EngineResult 增加适配器轮数元数据，不修改回执 Schema。
  Hermes 显式 `--max-turns`，避免配置覆盖 ENV 上限；WorkBuddy 传入剩余轮数。
  非零退出也读取用量；Codex 累计全部 turn.completed token，而不伪造内部模型轮数。
- 新增 15 项验证，两个重试测试的假引擎明确报告其单次迭代数；全量 **323 项通过**，
  Ruff、格式和构建通过。测试没有用降低额度或缩小完整 R-23 范围来声明验收。
- Codex 内部迭代上限仍待核实；保守额度不能证明其单次内部硬上限。
  明确边界及官方事件来源见 `docs/execution-limits.md`，整体计划继续。

## 2026-11-02（M1-13 / M1-10：Skill 引擎选择与原子槽位）

- 不再固定取全局 dev：按 Skill role/ref 顺序展开、去重，支持规则 engine_role；
  明确指定引擎也不能绕过 Skill 清单、启用状态和 allowed_tiers。
- 初派和故障转移共用健康、已知登录失败、熔断记录、并发空位过滤；货币预算门禁
  保留可注入估算接口，实际成本预算仍属于 M5。GUI 驱动仍未启用。
- 新增迁移 `0004_engine_reservations`，领取任务时同事务写 lease_engine_id；
  并发检查覆盖尚未建 run 的租约。故障转移也同事务重查和移动预留，结束/重派/
  租约回收释放预留。无任务或暂时无可用引擎时保持等待，不停止通知循环。
- 新增 17 项行为测试（含四线程争抢一个引擎槽位），全量 **308 项通过**，
  Ruff 与构建通过。累计轮数及完整适配器契约仍待后续验收。

## 2026-11-02（M1-17：真实 URL 原文读取）

- 注册 `pa.tools/read_url`，接入 `payload.url` 或单个 URL 文本；读取前执行 Skill
  工具和域名许可，抓取结果与原始输入统一进入不可信区。
- 连接公共解析地址并保留 Host/TLS SNI，逐跳校验，不经隐式代理、Cookie 或凭证；
  限制时间、跳转、响应体及文本长度，支持静态 HTML/文本/JSON，不运行脚本。
- 读取失败同事务转人工决定并写 P1，不调用模型假装已读。准备阶段加入任务时间
  额度、心跳和取消检查；此前累计用量与回执修正回归保持通过。
- 新增 32 项测试；全量 **291 项通过**，Ruff 与构建通过。
- 真实 HTTPS 抓取 GitHub robots 文本成功（14540 字符）；匿名 GitHub API 请求
  返回非成功状态被拒绝。真实模型的 URL 摘要仍待独立验收。
- sample-direct 许可域名显式补充 `github.com` 根域名，保留 `*.github.com`；
  原始开发资料模板保持不变。边界与来源说明见 `docs/source-reader.md`。

## 2026-11-02（持续开发：Git 推送与 M1-08 持久调度）

- 用户要求用 Git 管理进度并持续推进至计划完成；已推送 `bf08c39` 到
  Gitea `codex/continue-development`，后续批次继续提交和推送。
- 新增 `docs/PROGRESS.md`，按原 M1–M5 任务清单追踪实现和验收，完整目标继续进行。
- 新增可选 `schedules.yaml` 严格模型、时区与五字段 cron 校验、唯一 id 校验；
  `enabled` 默认 true，初始化为空，不预设用户的业务时间。
- APScheduler 使用 SQLite 作业存储，迁移 `0003_schedules`；事件入库与下一触发点
  在同一事务提交，重启和短暂错误后按原时间逐条补接，配置变更/禁用会同步作业。
- 核心锁内启动调度器，与模型执行独立；全局暂停照常接收事件；关闭时等待投递线程
  完成后释放锁。修复 APScheduler 3.x 关闭时最后唤醒可能继续扫描已停执行器的竞态。
- 新增 14 项调度验证，全量 **259 项通过**，线程异常以错误级别检查通过；
  原有 Starlette 弃用提示保留。未升级正式运行环境。
- 参考官方 APScheduler 3.x 用户指南及 CronTrigger API。使用说明与实现约定见
  `docs/schedules.md`；后续继续 M1-17 真实 URL 输入。

## 2026-11-02（新工作区继续 M1：累计执行限制）

- 从 Gitea `muwei/pa` 的 `45165f1` 拉取到
  `/Users/weimu/Documents/ChatGPT/pa`，分支 `codex/continue-development`。
- 复现最后一次回执检查遗漏此前失败调用用量的问题，9 项新增回归修复前失败。
- worker 从 `cost_ledger` 恢复任务累计用量，读取 Skill 的 `per_task_tokens`，
  执行时长取 Skill `timeout_min` 与全局 `default_task_timeout_min` 较小值。
  重试、同引擎修正、故障转移和新 worker 重派共用额度。
- 达到 token 或时长上限后直接转 `awaiting_decision`，在同一事务内释放租约、
  写入决定与 P1 通知；不继续重试。超时通过异步取消触发 CLI 子进程清理。
- 正常关闭或取消时，保存已完成调用的 token 和已消耗时长，包含修正回执前的失败调用。
- 新增 13 项行为测试，覆盖累计额度、修正与故障转移、重派持久化、边界值、
  正常结案、修正中关闭恢复和真实子进程超时回收。
- 全量 **245 项测试通过**，Ruff 检查与格式校验通过，sdist/wheel 构建成功；
  原有 Starlette TestClient 弃用提示仍保留，不影响断言。
- 边界：token 依据调用结束时引擎报告值，不是流式硬截断；尚不能还原强杀或
  引擎未报告的 token。执行时间只累计调用时间，不含排队及退避；累计轮数待办。
  未变更本机 Application Support 中的正式运行环境。

## 2026-11-02（评审后的运行时开发）

- worker、发件箱和接收进程监控改为独立异步循环，长模型调用不会拖住已入库的通知。
- 处理 SIGTERM，停止时释放核心锁、关闭长连接接收进程；引擎取消继续区分 owner
  取消与整个 worker 被关闭，避免吞掉关闭信号。
- SDK 失败通过打码的结构化日志记录；新增 WebSocket URL ticket 等参数打码。
- 新增 5 项验证：模型阻塞时通知独立执行、循环异常清理、外部 worker 取消、真实
  子进程 SIGTERM 释放锁、连接票据打码。合计 **232 个测试通过**，Ruff 与构建通过。
- 依赖审计未发现已知漏洞。测试仍有一条 Starlette 对 httpx TestClient 的弃用提示，
  不影响断言；未以屏蔽 warning 的方式处理。
- 已将锁定依赖和 wheel 部署到 `~/Library/Application Support/PA/runtime/`。
  主服务运行、核心心跳、Lark 长连接与本人配对均已核对；安装代码与仓库源文件一致。
- 明文 App Secret 扫描覆盖当前仓库文件和 Git 提交补丁，均未发现；密钥仍只在钥匙串。
- WorkBuddy 的现有内置 CLI 仍返回 auth required；豆包工作尚无后台 GUI 驱动，二者不计入可用引擎。

## 2026-11-02（后续提交评审）

评审 `b565dd9`、`dedd052`、`d7162d2`，复现并修复 6 类缺陷：默认 API 越权、
无效事件请求变成 500、任务过滤丢失旧记录、取消后仍重试、修正回执漏记用量、
重试耗尽未通知。新增 8 个先失败后通过的回归用例，合计 **227 个测试通过**。
详见 `docs/reviews/2026-11-02-m1.md`。

更正此前完成声明：URL 的测试仅证明字符串传递与隔离，未证明实际抓取；cron 只有
事件转换器，尚无持久化定时调度。生产服务现已在 Application Support 部署，
国际版 Lark 长连接、本人配对及配对回复已实测，密钥保存在 macOS 钥匙串。
完整 M1 仍有待办；历史各批次记录按当时状态保留，以上述更正为准。

## 2026-11-02

- 初始化独立 Git 仓库，默认分支 `main`。
- 保留开发资料包的原文、配置示例和部署模板，排除缩略图缓存。
- 第一轮目标：M1 工程基础、配置、数据库迁移、状态机与事件接单链路。
- M2–M5 和真实引擎、飞书、平台部署尚未实施；验收结果以实际测试为准。
- README A1–A9 保留为待决事项，不替用户填写外部平台配置。

## 2026-11-02（第二批：补齐 M1 核心缺口）

开发前先提交保存点（86b368a，含执行引擎、飞书、运行时等既有未提交成果），
随后补齐三项：

- **M1-11 排序值维护**：新增 `hub/ranking.py` 纯函数（固定步长 1000、置顶/前置/
  置底取邻居中值、整数间隙耗尽自动全量重编号）；OwnerFacade 增加移动/置顶/
  优先级/技能重排；CLI 增加 `task move/pin/unpin/priority` 与 `skill reorder`。
- **M1-15 判断层重试与重派**：重写 `procs/worker.py` 为「引擎选择 + 驱动循环 +
  终局/重派」。transient 同引擎重试 3 次（0.5s 起指数退避加抖动），
  auth/quota 立即换引擎，invalid_output 先同引擎修复一次再换引擎；重派写
  `engine_hint` 且下一轮 deprioritize 该引擎；重派达上限（max_retry=2）转
  `awaiting_decision`，原因 `retry_exhausted` 并写决定。
- **M1-16 通知分级与本地兜底**：新增 `notify/levels.py`（P0–P3）、
  `notify/dispatch.py`（P2 十分钟合并窗口、失败追踪、待补发标记）、
  `notify/local.py`（osascript）。P0/P1 立即入队，P2 缓冲到窗口到期合并；
  飞书连续失败 15 分钟触发 macOS 本地通知、写入 backfill 并重置窗口；
  `decision_only` 不发结案卡。`serve` 主循环每轮刷新到期窗口。

## 2026-11-02（第三批：M1-17 sample-direct Skill）

按关键路径（M1-15 → M1-17 → M1-18）推进，把 Skill 从硬编码提示词升级为
包内数据驱动：

- 新增包内资产 `src/pa/skills/assets/sample-direct/{skill.yaml,prompt.md}`，与
  07 配置模板保持一致；hatch 已确认把资产打入 wheel。
- 新增 `skills/loader.py`：`SkillSpec` 等 Pydantic 模型（extra=forbid），覆盖
  引擎选择器（role/ref 恰好一个）、权限、人工门禁（7 标签强制）、交付、
  预算、记忆等字段；提供 `load_skill / list_skills / render_prompt`。
- 引入 PRD §7.1 要求的 **Jinja2** 依赖渲染 `prompt.md`；`prompt_assembly.assemble`
  改为加载 Skill 资产驱动 `[skill]` 段，保留 8 段固定顺序、untrusted 转义、
  secret 拦截与 recovery 前缀。
- 验收：文本/URL 输入经 worker 完成读取 → 核实 → 摘要回执 → `closed` 结案。

## 2026-11-02（第四批：M1-18 进程入口与 REST 基础接口）

引入 PRD §21 指定的 **FastAPI + Uvicorn**，新增本地 HTTP 入口：

- 新增 `api/app.py`：`create_app(facade, api_token=None)`，实现 `/healthz`（公开）、
  `/api/v1/health`（db 检查）、`POST /api/v1/events`（`Idempotency-Key` 映射为
  manual 事件 dedup_key，经 ManualAdapter + emit）、`GET /api/v1/tasks`
  （status/skill 过滤）、`POST /system:pause|resume` 与 `GET /system/state`。
- 鉴权：默认本地 loopback 单用户放行；提供 `api_token` 时启用 Bearer 校验，
  healthz 保持公开；PAError 统一映射为 4xx JSON 响应。
- 新增 `procs/server.py`：uvicorn 启动入口，绑定 127.0.0.1；已用真实 uvicorn
  启动 + httpx 请求完成冒烟。
- 边界：完整 REST 资源面、Cookie 会话与角色 facade 隔离属于 M3，本批只做基础端点。

## 首批实现范围

| 任务 | 状态 | 本次结果与边界 |
|---|---|---|
| M1-01 | 完成 | Python 3.12、uv.lock、Typer、包目录、九类扩展点、sdist/wheel |
| M1-02 | 部分 | pa/rules/principals 的严格校验、ENV 插值、惰性 secret 引用；其他配置待实现 |
| M1-03 | 部分 | 只读 env 插件与 .env 权限检查函数；keychain、secret CLI、启动整合未实现 |
| M1-04 | 部分 | structlog 打码处理器、CLI 与入库 effect payload 打码；JSONL、记忆、飞书出口待实现 |
| M1-05 | 完成 | 基于架构附录 A 的完整基线表、索引、JSON 约束、FTS5、Alembic；SQLAlchemy Core 反射模型 |
| M1-06 | 完成 | 唯一状态迁移函数、乐观锁、状态/审计同事务、合法迁移矩阵；增加授权记录不可变触发器 |
| M1-07 | 完成 | 同事务入队、幂等键唯一；发件箱 runner 退避/重试/执行前核对、feishu handler 已接入 |
| M1-08 | 部分 | Event、manual/cron 规范化、event CLI；APScheduler 与定时任务持久化未实现 |
| M1-09 | 完成 | 规则路由、人员匹配、continue、接单模式、同主题合并；规则化研判与人工门禁（模型研判留待后续） |
| M1-11 | 完成 | 排序值纯函数、Facade 与 CLI 置顶/移动/优先级/重排，含随机移动性质测试 |
| M1-12 | 部分 | 持久化暂停标记、事件继续入库、决定到期顺延；执行器已接入 |
| M1-15 | 完成 | transient 同引擎重试、auth/quota/invalid_output 故障转移、重派与 engine_hint、重试上限升级 |
| M1-16 | 完成 | P0–P3 分级、P2 合并窗口、飞书失败 15 分钟 osascript 兜底与 backfill、decision_only 不发卡 |
| M1-17 | 完成 | sample-direct 包内资产、Skill 加载器与 Jinja2 渲染、资产驱动提示词、端到端读取核实摘要结案 |
| M1-18 | 完成 | FastAPI 基础端点、Idempotency-Key、Bearer 校验、procs/server 入口与真实 uvicorn 冒烟 |
| M1-19 | 部分 | init/migrate/doctor/config/event/task 查询取消/pause/resume/status/observe/effect/plugin，及 move/pin/priority、skill reorder |
| M2–M5 | 未实现 | 真实模型研判、PR、GUI、容器与恢复演练未验收 |

## 实测验证

- `uv sync --frozen` 成功；使用 CPython 3.12.15。
- `ruff check`、`ruff format --check` 通过。
- `pytest`：152 个测试用例通过，包含 121 个状态迁移组合；不是 PRD 中 152 个独立业务场景。
- 覆盖配置错误定位、拒绝明文敏感字段、不可删除的先例禁用类别、重复 YAML 键、
  YAML `off` 字符串、惰性密钥引用、并发四次事件入库只建一个任务、未知人员只观察、
  observe/off/auto、明确请求、规则顺序、状态与 effect 共同回滚、授权不可改、暂停到期顺延。
- 已用真实本地 SQLite 执行 init/migrate/doctor 和手动 emit；同键第二次返回 duplicate。
- 构建 sdist 和 wheel；构建产物在 `dist/`，由 Git 忽略。
- wheel 在独立 Python 环境安装后，初始化数据库与 doctor 均成功。
- `pip-audit`：已将 pytest 升级至 9.2.0，复查未发现已知漏洞；升级后 152 项测试仍通过。
- 这些是开发机上的基础验证；PRD E2E-01–04 所需的引擎、飞书和服务托管尚未具备。
- 第二批后 `pytest` 增至 **204 个用例通过**：新增排序 10 个（含 400 次随机移动的
  全序/唯一性性质测试）、判断层重试 6 个、通知分级与兜底 11 个；并把 `configured`
  fixture 收敛到 conftest 共享。
- 新增覆盖：transient 重试不增 retry_count、重试耗尽重派与 engine_hint、auth/quota
  立即换引擎、invalid_output 修复后换引擎、重派 deprioritize、P2 窗口合并、
  15 分钟本地兜底与 backfill、decision_only 不发结案卡。
- 第三批后 `pytest` 增至 **212 个用例通过**：新增 Skill 加载器 8 个（含模型校验、
  prompt 渲染、文本/URL 端到端结案）；已核对 wheel 内含 `skills/assets` 资产。
- 第四批后 `pytest` 增至 **219 个用例通过**：新增 API 测试 7 个（healthz/health、
  事件建任务、幂等、任务过滤、pause/resume、Bearer 401/200）。

## 实现约定与待办

- 本阶段仅支持本机 owner CLI。不可将 OwnerFacade 直接暴露给未认证的远程请求或 agent。
- `assess: true` 的推断事件只记录 pending 观察，`would_accept=0`；未接入模型前不自动接单。
- 当前唯一约束实现永久 delivery/idempotency-key 去重；600 秒窗口式去重待按来源区分实现。
- 只实现基础身份映射，未实现 principals 全部白名单、policy 热更新及配置版本写入。
- 引擎检查、槽位派单、租约、恢复、实际执行与规则化研判已落地；剩余依赖为 REST
  接口（M1-18）、真实模型研判、backfill 自动补发执行器，以及 M2 以后能力。
- 初始迁移为不可变 SQL 快照；禁止破坏性 downgrade，未来升级新增 migration。
- SQLite 使用 WAL、NORMAL、5 秒 busy_timeout、外键，以及短 `BEGIN IMMEDIATE` 写事务。
- 此次未修改用户全局 Git 身份；提交使用单次命令指定 `Codex <codex@localhost>`。
- 仅建立本地仓库，未配置 remote 或推送。

实现时核对了 [Pydantic 模型文档](https://docs.pydantic.dev/latest/concepts/models/)、
[SQLAlchemy SQLite 文档](https://docs.sqlalchemy.org/en/20/dialects/sqlite.html) 与
[Alembic 迁移文档](https://alembic.sqlalchemy.org/en/latest/tutorial.html)。
