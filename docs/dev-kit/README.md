# PA 开发辅助资料包（pa-dev-kit）

> 唯一依据：`../PA-私人AI自动化系统-PRD.md`（v1.1）。
> - 文中所有 `§x.y` 都指 PRD 的章节，`v1.1·Dn/Kn` 指 PRD 中的 v1.1 编号。
> - 凡标注 **[实现约定]** 的内容，是为了让 PRD 能落地而补的工程细节，不扩展功能。
> - PRD 本身的歧义和缺口统一列在本文末尾，编程智能体不要自行猜测。

## 目录

| 文件 | 内容 | 对应需求 |
|---|---|---|
| `01-开发任务拆解.md` | 原子任务：M1 共 21 个、M2 共 21 个、M3 共 11 个、M4 共 8 个、M5 共 14 个，另有远期 3 个。每个任务写明输入、输出、验收标准和依赖关系 | 1 |
| `02-技术架构设计.md` | 分层与依赖规则、接口签名、10 个关键算法、容器隔离、权限分层、完整 DDL、状态与原因码 | 2 |
| `03-开发规范与约束.md` | 红线 R-01 至 R-29（安全和可靠性）、沟通规范 C 系列、代码规范、配置规范和字段字典、变更流程 | 3 |
| `04-API与CLI接口文档.md` | REST 端点及其请求、响应、错误码和调用示例；CLI 命令及参数、agent 权限和调用示例 | 4 |
| `05-部署与运维指南.md` | 部署清单、安装步骤、外部平台配置、验收清单、运维日历、故障恢复 | 5 |
| `deploy/macos/` | `install.sh`、`scripts/preflight.sh`、launchd 模板、cloudflared 配置模板、`containers/*.Containerfile` | 5 |
| `SETUP.md` | 由 Agent 带着部署的引导骨架（v1.1·D20） | 5 |
| `06-测试策略与用例.md` | 测试分层，单元测试 34 个、集成测试 69 个、端到端测试 15 个，测试夹具，CI 门禁 | 6 |
| `07-配置模板/` | `pa.yaml`（含 worker 段）、`rules.yaml`、`sandbox.yaml`、`engines.yaml`、`sources.yaml`、`merge_policy.yaml`、`risk.yaml`、`others.yaml`（含 schedules、principals、notify、storage、secrets、budgets、pricing、redaction），两个示例 Skill、`precedents/*.md`、GUI 剧本、`schemas/receipt.schema.json`、`examples/*.json` | 7 |

## 给编程智能体的使用方式

1. 先读 `03`，里面是红线和规范；再读 `02`，里面是架构。
2. 按 `01` 中的依赖顺序领取任务。每个任务完成前，都要跑通 `06` 中对应的用例。
3. 配置文件直接复制 `07-配置模板/` 到 `~/.config/pa/`，再填写其中的 `<…>` 占位符。
4. 接口的实现以 `04` 为准，Schema 以 `07-配置模板/schemas/` 为准。
5. 遇到 PRD 没有定义的内容，在代码里标 `TODO(PRD)`，并提出问题，不要自行发明。

## PRD 歧义与缺口清单（需要你确认）

| # | 问题 | 当前处理 |
|---|---|---|
| A1 | 你提到的 `worker.yaml` 在 PRD 中不存在。worker 的参数在 `pa.yaml` 的 `worker` 段里（§19.1） | 模板中没有单独建 `worker.yaml`，而是放在 `pa.yaml` 中 |
| A2 | 任务阶段表里有"受阻"（blocked），但任务状态机（§4.2）中没有 `blocked` 状态 | 用最近一条回执的 `status=blocked` 来判断，不新增任务状态（见 02 §3.9） |
| A3 | 容器隔离（K5）和向量记忆排在 M5，但 M2 的审查纪律和先例匹配要用到它们 | M2 先用降级方案：审查在本机运行、不注入密钥，并标记 `sandbox=degraded`；先例匹配先用 FTS5。到 M5 再切换（见 01 中 M2 的注） |
| A4 | PRD 要求合并身份单独使用一套凭证（D17），但 `sources.yaml` 中没有给出对应字段名 | 模板中只以注释形式给了建议字段 `merger_app_id`、`merger_private_key`、`merger_token`，请你确认 |
| A5 | 以下功能 PRD 分期表中没有明确写在哪个阶段：工具注册与 MCP 服务（§9.2）、决定超时（§11）、合并身份分离、Webhook 时间戳校验 | 按依赖关系放进了 M2 |
| A6 | `risk.yaml` 和 `pricing.yaml` 的字段结构 PRD 没有定义，只给了规则和计量方式 | 补了结构（标注为 [实现约定]），阈值和取值全部来自 PRD |
| A7 | GitHub 打标签的事件 `category` 应该取什么值，PRD 没有规定映射规则 | 示例数据中暂时写成 `manual`，请你确认 |
| A8 | `brew install container` 是 PRD 原文的写法；Apple Containerization 实际是否能通过 Homebrew 安装，部署时需要核实 | 部署指南中注明了：装不上就改用官方安装方式，或者改用 tart / colima |
| A9 | `memories_vec` 的向量维度取决于 embedding 模型，PRD 没有规定 | DDL 中保留占位符 `<dim>`，部署时填写 |
