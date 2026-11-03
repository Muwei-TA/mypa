# PA 私人 AI 自动化系统

依据 [开发资料包](docs/dev-kit/README.md) 和 PRD v1.1 开发。

## 开发状态

当前版本 **0.1.1**。已修复后续提交审阅中的 17 组问题，并在 Apple Silicon Mac 上
验证真实 CLI 模型执行、代码工作区修改、HTTP 控制台和浏览器交互。修复与真机证据见
[修复记录](docs/reviews/2026-11-03-repairs.md)。不以模块存在或单元测试通过替代整阶段验收。

当前可用的运行路径：

- 手动/飞书/定时事件 → 规则与准入 → 槽位、租约及恢复 → CLI 执行 → 回执研判、
  人工决定与分级通知；文本输入和许可域名 URL 读取有独立验证。
- [代码模式](docs/code-mode.md)：仅接收 code.yaml 许可的仓库，创建持久任务工作区，
  Codex 在受限权限配置下编辑文件，由 PA 提交及生成补丁。local 仓库停在人工审阅；
  配置平台适配器后才创建 PR 并等待指定检查，完整远端审查/合并/发布仍待联调。
- 控制台：密码 + TOTP、签名 Cookie、CSRF、任务与决定操作、跨文件配置版本、
  冲突检测和中断恢复；规则禁用在下一条接入事件前生效。
- 金额预算已接入派单及每次原生调用，支持保守预留和实际计价；未配置单价时仍可
  计量，但存在有限金额预算的调用会拒绝无法估价的请求。token/轮数/时间限制独立。
- Mac 内核沙盒实际拒绝工作区写入、宿主文件读取和网络连接；这是文件与网络隔离，
  不是 VM/容器，不提供 CPU/RAM 硬配额。process 后端只用于开发，拒绝严格审查；
  Docker/Apple Container/Tart 未实现，显式选择时拒绝降级。
- [暂停与恢复](docs/pause-recovery.md)、[持久化定时事件](docs/schedules.md)、
  [许可域名读取](docs/source-reader.md)、[执行额度](docs/execution-limits.md) 保留各自边界。

**未验收或未接通**：PA 的原生 GUI 驱动、完整远端 PR/发布验证、真正容器运行、
向量检索、模型研判/订阅/策略变更等模块与主循环的全面接线、整机恢复、WebDAV 上传。
GUI 的 SyntheticDriver 仅限显式注入的测试；生产健康检查不再用它报告“可执行”，
CLI dry-run 在没有真实驱动时报告不可用。原有 M1–M5 分项记录见
[开发进度表](docs/PROGRESS.md)，其中历史“完成”标记不能当作当前验收结论。

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
codex/hermes/workbuddy/gui 四个引擎、read_url 工具、env/keychain/sops/onepassword
密钥后端、process 沙盒后端与 feishu.send effect handler，其余扩展点留空。
