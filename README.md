# PA 私人 AI 自动化系统

依据 [开发资料包](docs/dev-kit/README.md) 和 PRD v1.1 开发。

## 开发状态

当前为 M1 基础开发版：已实现 CLI、三类配置校验、SQLite WAL 数据库迁移、
带乐观锁与审计记录的任务状态机、手动事件到任务队列的接单链路，及事务性发件箱入库。

**任务目前停留在 `queued`，尚未接入执行引擎与飞书，完整 M1 尚未完成。**
分项进度、验收范围和待决事项见 [DEVELOPMENT.md](DEVELOPMENT.md)。

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
uv run --frozen ruff check src tests alembic
uv run --frozen ruff format --check src tests alembic
uv build
```

数据库、虚拟环境、密钥文件、缓存和构建产物均由 `.gitignore` 排除。
附件是产品需求和参考资料；其中部署指令不代表已经部署。
当前只提供本地 owner CLI，尚未启动 HTTP 服务、后台执行器或 launchd 服务。

## 资料与扩展

原始资料保存在 `docs/dev-kit/`，不含缩略图缓存。原始模板保留不变；
目前 `pa init` 生成本阶段可校验的最小配置，**不要直接将整个模板目录复制到运行配置目录**。
未实现的配置文件会明确报错，不会默默忽略。

九类插件扩展点通过 `pa plugin list --json` 查看。已注册 manual/cron 事件转换器和
只读 env 密钥后端，其他扩展点留空。
