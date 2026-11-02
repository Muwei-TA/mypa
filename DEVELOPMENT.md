# 开发记录

## 2026-11-02

- 初始化独立 Git 仓库，默认分支 `main`。
- 保留开发资料包的原文、配置示例和部署模板，排除缩略图缓存。
- 第一轮目标：M1 工程基础、配置、数据库迁移、状态机与事件接单链路。
- M2–M5 和真实引擎、飞书、平台部署尚未实施；验收结果以实际测试为准。
- README A1–A9 保留为待决事项，不替用户填写外部平台配置。

## 首批实现范围

| 任务 | 状态 | 本次结果与边界 |
|---|---|---|
| M1-01 | 完成 | Python 3.12、uv.lock、Typer、包目录、九类扩展点、sdist/wheel |
| M1-02 | 部分 | pa/rules/principals 的严格校验、ENV 插值、惰性 secret 引用；其他配置待实现 |
| M1-03 | 部分 | 只读 env 插件与 .env 权限检查函数；keychain、secret CLI、启动整合未实现 |
| M1-04 | 部分 | structlog 打码处理器、CLI 与入库 effect payload 打码；JSONL、记忆、飞书出口待实现 |
| M1-05 | 完成 | 基于架构附录 A 的完整基线表、索引、JSON 约束、FTS5、Alembic；SQLAlchemy Core 反射模型 |
| M1-06 | 完成 | 唯一状态迁移函数、乐观锁、状态/审计同事务、合法迁移矩阵；增加授权记录不可变触发器 |
| M1-07 | 部分 | 同事务入队、幂等键唯一；runner、执行前状态核对、退避和通知 handler 未实现 |
| M1-08 | 部分 | Event、manual/cron 规范化、event CLI；APScheduler 与定时任务持久化未实现 |
| M1-09 | 部分 | 人员匹配、规则顺序、continue、接单模式、同主题合并及上下文引用；模型研判待实现 |
| M1-12 | 部分 | 持久化暂停标记、事件继续入库、决定到期顺延；执行器与输入投递尚未接入 |
| M1-19 | 部分 | init/migrate/doctor/config/event/task 查询与取消/pause/resume/status/observe/effect 查询/plugin |
| 其余 M1 / M2–M5 | 未实现 | 不宣称真实 AI、通知、PR、GUI、容器与恢复演练已验收 |

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

## 实现约定与待办

- 本阶段仅支持本机 owner CLI。不可将 OwnerFacade 直接暴露给未认证的远程请求或 agent。
- `assess: true` 的推断事件只记录 pending 观察，`would_accept=0`；未接入模型前不自动接单。
- 当前唯一约束实现永久 delivery/idempotency-key 去重；600 秒窗口式去重待按来源区分实现。
- 只实现基础身份映射，未实现 principals 全部白名单、policy 热更新及配置版本写入。
- 有效引擎和 Skill 检查、槽位派单、租约、恢复、实际执行、结果研判是下一批依赖。
- 初始迁移为不可变 SQL 快照；禁止破坏性 downgrade，未来升级新增 migration。
- SQLite 使用 WAL、NORMAL、5 秒 busy_timeout、外键，以及短 `BEGIN IMMEDIATE` 写事务。
- 此次未修改用户全局 Git 身份；提交使用单次命令指定 `Codex <codex@localhost>`。
- 仅建立本地仓库，未配置 remote 或推送。

实现时核对了 [Pydantic 模型文档](https://docs.pydantic.dev/latest/concepts/models/)、
[SQLAlchemy SQLite 文档](https://docs.sqlalchemy.org/en/20/dialects/sqlite.html) 与
[Alembic 迁移文档](https://alembic.sqlalchemy.org/en/latest/tutorial.html)。
