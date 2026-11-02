# PA 私人 AI 自动化系统

依据 [开发资料包](docs/dev-kit/README.md) 和 PRD v1.1 开发。

## 开发状态

当前为 M1 开发版，已打通文本任务执行链路：事件接入（手动/飞书）→
规则路由与同主题合并 → 派单（槽位、租约、过期恢复）→ 本地 CLI 引擎执行与
心跳续租 → 回执研判与人工门禁 → 自动结案或转人工决定 → 分级通知。

已具备的关键能力：

- 本机引擎发现；Codex 与 Hermes 已实调用通过；WorkBuddy 适配器等待 CLI 登录；
- 派单按 Skill 的 role/ref 和规则角色选择，过滤启用状态、档位、健康、熔断及
  并发空位；任务租约原子预留引擎槽位，初派与切换均重查；
- Codex 使用 read-only 模式，WorkBuddy 禁用工具，Hermes 仅开放 clarify；尚未提供容器隔离；
- 事务性发件箱、发件箱 runner（退避/重试/幂等）与飞书发送 handler；
- 飞书长连接接收进程与配对；判断层自动重试、故障转移与重派；
- 单任务累计 token 与执行时间检查覆盖重试、回执修正、切换引擎和重派；达到上限后
  转人工决定并立即入队 P1 通知，执行超时会取消引擎并清理 CLI 子进程；
- 通知 P0–P3 分级、P2 十分钟合并窗口、飞书连续不可用 15 分钟后的 macOS
  `osascript` 本地兜底与待补发标记；
- 任务/技能排序值维护（置顶、优先级、移动、重编号）；
- [持久化定时事件](docs/schedules.md)：APScheduler + SQLite，重启补接、原触发时间去重、
  配置同步与启停，事件仍经过规则与 observe/auto 准入；
- Skill 包内资产与加载器：`sample-direct` 的 `skill.yaml`/`prompt.md`（Jinja2）
  经 Pydantic 校验并驱动提示词组装；支持文本，以及经
  [许可域名原文读取](docs/source-reader.md) 的 URL 输入与摘要回执；
- REST 基础接口（FastAPI + Uvicorn，绑定 127.0.0.1）：`/healthz`、`/api/v1/health`、
  `POST /events`（支持 `Idempotency-Key`）、`GET /tasks`、`/system:pause|resume|state`，
  私有接口强制 Bearer 校验；经 `python -m pa.procs.server <config-dir>` 启动，
  启动前需通过 `pa secret set secret://keychain/pa/api_owner_token` 配置 owner 令牌。

**尚未实施**：豆包 GUI 执行、完整 REST 与角色权限面（M3）、
代码平台闭环（M2）及容器隔离。不能视为完整 M1 已验收。
分项进度和 [评审记录](docs/reviews/2026-11-02-m1.md) 中列出了验证边界。
后续任务及逐项验收依据见 [开发进度表](docs/PROGRESS.md)。

累计 token 依据引擎已报告用量检查，在调用结束后及下一次调用前生效；尚不支持
流式 token 硬截断，也无法还原被强杀或未报告的 token。执行时间累计已记账的引擎
调用和输入准备（包括 URL 读取）时长，不包含排队与重试退避；轮数额度持久预留，
Hermes/WorkBuddy 接收剩余限制，Codex 单次内部迭代限制仍待核实，详见
[执行额度边界](docs/execution-limits.md)。这些限制不依赖金额预算。

## 本地开发

需要 Python 3.12 和 uv。在仓库目录运行：

```sh
uv sync --frozen
uv run --frozen pa --help
uv run --frozen pa --config-dir .pa-local/config init --json
uv run --frozen pa --config-dir .pa-local/config doctor --json
uv run --frozen pa --config-dir .pa-local/config event emit manual.test \
  --payload '{"skill":"sample-direct","text":"hello"}' --dedup-key hello-1 --json
uv run --frozen pa --config-dir .pa-local/config task list --json
```

重复使用同一个 `--dedup-key` 会返回 `duplicate`，不会再次创建任务。
已有配置目录执行 `init` 会拒绝覆盖；数据库升级使用 `pa migrate`。
可以通过 `PA_CONFIG_DIR` 指定配置目录，或在子命令前传 `--config-dir`。
所有已实现命令均支持 `--json`，当前默认也输出 JSON。

```sh
uv run --frozen pytest
uv run --frozen ruff check src tests alembic scripts
uv run --frozen ruff format --check src tests alembic scripts
uv build
```

数据库、虚拟环境、密钥文件、缓存和构建产物均由 `.gitignore` 排除。
附件是产品需求和参考资料；其中部署指令不代表已经部署。
开发模式通过 `pa run` 在本机运行（含飞书接收子进程）。本机正式运行环境已部署到
`~/Library/Application Support/PA/`，由 `com.pa.runtime` LaunchAgent 托管，
`~/.local/bin/pa` 指向该运行环境。HTTP 服务未随主运行时自动开放。

本次真实接入使用国际版 Lark 域名；长连接、本人配对和配对回复已验证。
生产配置和数据库独立于仓库，不会因为开发 checkout 改动自动升级。

## 资料与扩展

原始资料保存在 `docs/dev-kit/`，不含缩略图缓存。原始模板保留不变；
目前 `pa init` 生成本阶段可校验的最小配置，**不要直接将整个模板目录复制到运行配置目录**。
未实现的配置文件会明确报错，不会默默忽略。

九类插件扩展点通过 `pa plugin list --json` 查看。已注册 manual/cron 事件转换器、
codex/hermes/workbuddy 三个引擎、env/keychain 密钥后端与 feishu.send effect handler，
其余扩展点留空。
