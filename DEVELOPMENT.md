# 开发记录

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
