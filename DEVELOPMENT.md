# 开发记录

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
| M1-19 | 部分 | init/migrate/doctor/config/event/task 查询取消/pause/resume/status/observe/effect/plugin，及 move/pin/priority、skill reorder |
| M1-18 / M2–M5 | 未实现 | REST 接口、真实模型研判、PR、GUI、容器与恢复演练未验收 |

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
