# PA 私人 AI 自动化系统

依据 [开发资料包](docs/dev-kit/README.md) 和 PRD v1.1 开发。

## 开发状态

当前为 M1 开发版，已打通本机端到端执行链路：事件接入（手动/飞书/定时）→
规则路由与同主题合并 → 派单（槽位、租约、过期恢复）→ 本地 CLI 引擎执行与
心跳续租 → 回执研判与人工门禁 → 自动结案或转人工决定 → 分级通知。

已具备的关键能力：

- 三个本地 CLI 引擎适配器（Codex / Hermes / WorkBuddy）、进程沙箱与本地发现；
- 事务性发件箱、发件箱 runner（退避/重试/幂等）与飞书发送 handler；
- 飞书长连接接收进程与配对；判断层自动重试、故障转移与重派；
- 通知 P0–P3 分级、P2 十分钟合并窗口、飞书连续不可用 15 分钟后的 macOS
  `osascript` 本地兜底与待补发标记；
- 任务/技能排序值维护（置顶、优先级、移动、重编号）；
- Skill 包内资产与加载器：`sample-direct` 的 `skill.yaml`/`prompt.md`（Jinja2）
  经 Pydantic 校验并驱动提示词组装，完成读取 → 核实 → 摘要 → 结案。

**尚未实施**：REST 接口（M1-18）、M2 及以后能力、HTTP 服务与 launchd 托管、
真实外部平台部署。分项进度、验收范围和待决事项见 [DEVELOPMENT.md](DEVELOPMENT.md)。

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
后台执行通过 `pa serve` 在本机运行（含飞书接收子进程）；尚未启动 HTTP 服务或
launchd 托管。

## 资料与扩展

原始资料保存在 `docs/dev-kit/`，不含缩略图缓存。原始模板保留不变；
目前 `pa init` 生成本阶段可校验的最小配置，**不要直接将整个模板目录复制到运行配置目录**。
未实现的配置文件会明确报错，不会默默忽略。

九类插件扩展点通过 `pa plugin list --json` 查看。已注册 manual/cron 事件转换器、
codex/hermes/workbuddy 三个引擎、env/keychain 密钥后端与 feishu.send effect handler，
其余扩展点留空。
