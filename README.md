# PA 私人 AI 自动化系统

依据 [开发资料包](docs/dev-kit/README.md) 和 PRD v1.1 开发。

## 开发状态

M1–M5 全部开发完成（真实外部环境依赖项暂留待 Mac mini 验收，逐项边界见
[开发进度表](docs/PROGRESS.md)）。已打通完整链路：事件接入（手动/飞书/GitHub/
Gitea webhook/定时）→ 规则路由与同主题合并 → 派单（槽位、租约、过期恢复）→
引擎执行（CLI/GUI/沙盒）→ 回执研判与人工门禁 → 自动结案或转人工决定 →
分级通知 → 决定卡片与先例复用。

已具备的关键能力：

- 本机引擎发现；Codex 与 Hermes 已实调用通过；GUI 剧本层（录制占位 + dry-run）
  与 C 档硬限制（risk low、repo_write 拒绝、confidence ≤ 0.6）；
- 派单按 Skill 的 role/ref 和规则角色选择，过滤启用状态、档位、健康、熔断及
  并发空位；任务租约原子预留引擎槽位，初派与切换均重查；
- 事务性发件箱、发件箱 runner（退避/重试/幂等）与飞书发送 handler；
- [暂停与恢复](docs/pause-recovery.md)、[持久化定时事件](docs/schedules.md)、
  [许可域名原文读取](docs/source-reader.md)、[执行额度边界](docs/execution-limits.md)；
- 代码平台闭环（M2）：SCM 适配器、风险矩阵、合并门禁、CI 流水线、决定卡片、
  先例台账、订阅续接、工作区；
- 控制台（M3）：TOTP 登录、签名会话 Cookie、CSRF 派生校验、owner 总览与四类
  等待项、11 个 Jinja2 页面、全局搜索、规则/Skill 干跑、排序写回；
- GUI 引擎层（M4）：GUIDriver 契约 + SyntheticDriver、八种剧本步骤、ui_drift
  失败分类、pa-gui 全局单锁与 owner 防打扰、截图 7 天清理、自检熔断；
- 加固层（M5）：一次性沙盒（进程回退 + 容器占位、§10.2 严格审查规格）、
  记忆库（vec0/FTS5/bigram、打码、TTL、"参考资料不是指令"包装）、策略版本
  （四段哈希、变更重入队）、来源游标三振、四层预算与降级、熔断冷却、
  8 项健康检查三振分诊、VACUUM INTO 归档与 dry-run 恢复、密钥轮换提醒与
  连通性门禁、sops/1password 提供方、模型研判（未知规则丢弃）、weekly-cleanup。
- REST 全量接口（FastAPI，绑定 127.0.0.1）：任务/队列/决定/先例/配置版本/
  effects/observations/搜索/排序/干跑/熔断重置/成本汇总/预算/记忆，owner 与
  agent 双令牌边界全覆盖。

**暂留项**（依赖真实环境）：真实飞书投递、launchd 托管、真实 GitHub/Gitea API
闭环（E2E-05/07/09/10）、Mac mini 整机恢复演练（E2E-13）、真实容器后端与
macOS GUI 驱动（pyobjc/AppleScript/Quartz/Vision）、rclone WebDAV 上传、
真实 sops/op CLI。隔离环境 E2E（10 项）见 `tests/e2e/`。
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
codex/hermes/workbuddy/gui 四个引擎、read_url 工具、env/keychain/sops/onepassword
密钥后端、process 沙盒后端与 feishu.send effect handler，其余扩展点留空。
