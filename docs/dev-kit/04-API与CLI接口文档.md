# 04 API 与 CLI 接口文档

> 依据：PRD v1.1 第 20 章，以及第 5、6、12、19 章中的相关语义。
> - 端点和命令都来自 PRD 中的清单。
> - 请求和响应字段取自 PRD 中定义的数据结构（§5.1 Event、§7.3 回执、§14 表结构）。
> - PRD 只给出端点、没有给出参数格式的，按 REST 惯例补齐，标注 **[实现约定]**。

## 1. 通用约定

| 项目 | 约定 |
|---|---|
| 基础路径 | `/api/v1`。`/healthz` 和 `/hooks/*` 不加前缀 |
| 鉴权 | `Authorization: Bearer <token>`，token 分 owner 和 agent 两种，作用范围与 CLI 相同；控制台使用 Cookie 会话（§20.2） |
| 写接口 | 全部写入 `audit_log`（§20.2） |
| 幂等 | `POST /events` 支持 `Idempotency-Key` 请求头（§20.2） |
| 配置写入 | 必须带 `base_revision`（§19.3） |
| 自定义动作 | 用 `:verb` 后缀表示，例如 `/tasks/{id}:retry`（§20.2） |
| 文档 | OpenAPI 文档地址为 `/api/docs`，只能在局域网访问 |
| 分页 [实现约定] | 列表接口支持 `?limit=50&cursor=<ulid>`，响应为 `{"items":[…], "next_cursor": "…"\|null}` |

### 1.1 错误响应 [实现约定，状态码与 CLI 退出码对应]

```json
{"error": {"code": "conflict", "message": "base_revision 12 != current 13", "details": {"current_revision": 13}}}
```

| HTTP | code | 含义 | CLI 退出码（§20.1） |
|---|---|---|---|
| 200/201/202 | — | 成功 | 0 |
| 400 / 422 | `bad_request` / `validation_error` | 参数错误、配置校验失败（`details` 中给出文件和字段路径） | 2 |
| 401 | `unauthorized` | 没有带 token、token 无效，或 Webhook 签名校验失败（§6.1） | 3 |
| 403 | `forbidden` | agent 令牌尝试执行 owner 才能做的操作（§20.1、v1.1·D21） | 3 |
| 404 | `not_found` | 资源不存在；Tunnel 上未公开的路径也返回 404（§17.5） | 4 |
| 409 | `conflict` | 配置的 `base_revision` 冲突（v1.1·D15），或任务状态不允许当前操作 | 2 |
| 423 | `paused` | 系统处于全局暂停状态，同时请求要求立即执行 [实现约定] | 2 |
| 429 | `rate_limited` | 来源 IP 被拉黑（同一来源 5 分钟内失败超过 20 次，拉黑 1 小时，§6.1） | 3 |
| 501 | `not_implemented` | 预留接口（`/lease`，v1.1·K11） | 2 |

## 2. REST API

### 2.1 健康

| 方法 | 路径 | 鉴权 | 响应 |
|---|---|---|---|
| GET | `/healthz` | 公开 | `200 ok`，只返回 200（§16.3） |
| GET | `/api/v1/health` | owner/agent | `{"checks":[{"name":"db","ok":true},{"name":"engine:codex_cli","ok":true},…],"problems":[…]}`。检查项为 §16.3 列出的 8 项；只有连续失败 3 次的项目才会出现在 `problems` 中（v1.1·D11） |

### 2.2 事件

**`POST /api/v1/events`**：手动触发一个事件

请求示例：

```http
POST /api/v1/events
Authorization: Bearer <owner-token>
Idempotency-Key: 01JC…           # 用作 manual 事件的 dedup_key（§5.2）
Content-Type: application/json

{"type": "manual.test", "summary": "测试直接处理", "payload": {"skill": "sample-direct", "text": "hello"}}
```

响应示例：

```json
{"event": {"id": "01JC8…", "source": "manual", "type": "manual.test", "category": "manual",
           "status": "accepted", "summary": "测试直接处理", "addressed": true},
 "tasks": ["01JC8…"]}
```

- 同一个 `Idempotency-Key` 再次提交，返回 200，`event.status = "duplicate"`。

**其他事件接口**

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/events?source=&category=&status=` | 事件列表 |
| GET | `/events/{id}` | 返回事件详情，以及它的去向：由哪条规则命中、是否被丢弃（rejected/duplicate），或进入了观察记录 |
| POST | `/events/{id}:reevaluate` | 仅 owner。把事件重新送进研判（v1.1） |

### 2.3 任务

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/tasks?status=&skill=` | 列表项包含 `stage`（来自 `task_stage` 视图） |
| GET | `/tasks/{id}` | 详情，内容见下方示例 |
| POST | `/tasks` | body：`{"skill":"sample-direct","input":{…},"priority":"P2"}`，等同于创建一个 manual 事件并直接指定 Skill [实现约定] |
| POST | `/tasks/{id}:cancel` | 仅 owner |
| POST | `/tasks/{id}:retry` | body：`{"engine":"<可选>"}`。指定的引擎写入 `engine_hint` |
| POST | `/tasks/{id}:pin` / `:unpin` | 置顶 / 取消置顶 |
| POST | `/tasks/{id}:stop` | 仅 owner。撤回授权记录，任务不再续接（`stopped_by_owner=1`） |
| PATCH | `/tasks/{id}` | body：`{"priority":"P1"}` |
| POST | `/tasks:reorder` | body：`{"id":"…","before":"…"}`，或用 `after`，或用 `position`（取值 `top` 或 `bottom`） |
| GET | `/tasks/{id}/subscriptions` | 返回该任务订阅的线程（v1.1·D7） |
| GET | `/queue` | 按实际执行顺序返回待执行任务 |

**`GET /tasks/{id}` 响应示例**

```json
{"id":"01JC…","skill_id":"sample-code","status":"awaiting_ci","stage":"working","priority":"P2",
 "mandate":{"policy_version":"p-3f2a9c","strength":"explicit","via":"rule","actor":"github:<user>",
            "matched_rules":["r-issue-sandbox"],"scope":"修复 repo#12 描述的问题","source_scope":"github:owner/repo:issues"},
 "timeline":[{"from":"queued","to":"leased","reason":[{"code":"…","text":"…"}],"at":"…"}],
 "runs":[{"attempt":1,"engine_id":"codex_cli","tier":"A","outcome":"succeeded","recovered":false,"receipt":{…}}],
 "pull_requests":[{"scm":"github","repo":"owner/repo","number":34,"ci_stage":"full_check","fix_round":1,"risk_final":"medium"}],
 "decisions":[], "cost":{"cost_cny":0.42,"input_tokens":51234,"output_tokens":8120},
 "subscriptions":[{"thread_ref":"github:owner/repo#12","own":true,"context_only":false}]}
```

### 2.4 Skill 与规则

| 方法 | 路径 | 说明 |
|---|---|---|
| GET / POST | `/skills` | 列表 / 新建 |
| GET / PUT | `/skills/{id}` | 详情 / 修改。PUT 的 body 为 `{"yaml":"…","base_revision":12}` |
| POST | `/skills/{id}:test` | body：`{"input":{…}}`。走一遍流程，返回将使用的引擎和组装好的提示词摘要，**不执行任何对外动作** [实现约定] |
| POST | `/skills/{id}:enable` / `:disable` | 启用 / 停用 |
| POST | `/skills:reorder` | body：`{"order":["a","b","c"]}` 或 `{"id":"a","before":"b"}` |
| GET / POST | `/rules` | 列表 / 新建 |
| PUT / DELETE | `/rules/{id}` | 修改 / 删除 |
| POST | `/rules:reorder` | 同 Skill 排序。排序结果写回 YAML，并记审计（§6.3） |
| POST | `/rules:test` | body：`{"event":{…}}`，见下方示例 |
| POST | `/rules/{id}:mode` | body：`{"mode":"off"\|"observe"\|"auto"}`（v1.1·D4） |
| GET | `/observations?rule=` | observe 模式下"本来会做什么"的记录 |

**`POST /rules:test` 响应示例**

```json
{"matched":[{"rule_id":"r-issue-sandbox","strength":"explicit","accept_mode":"auto","would":"create_task","skill":"sample-code"}],
 "policy_version":"p-3f2a9c"}
```

### 2.5 引擎、PR、决定、成本、记忆、先例

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/engines` | 返回 `[{id, tier, roles[], health, breaker_state, max_concurrency}]` |
| GET | `/engines/{id}/health` | 单个引擎的健康状态 |
| POST | `/engines/{id}:test` / `:reset-breaker` | 测试引擎 / 重置熔断 |
| GET | `/prs`、`/prs/{id}` | 详情包含 `reviews[]`（round、engine_id、model、verdict、issues、token_ratio、same_model_fallback）和 `ci_runs[]` |
| GET | `/decisions?status=pending` | 待处理的决定 |
| POST | `/decisions/{id}:answer` | 仅 owner。请求和响应见下方示例 |
| GET | `/cost/summary?group_by=skill\|engine\|day&from=&to=` | 成本汇总 |
| GET / PUT | `/budgets` | 预算。`limit_cny` 可以为 null，此时只记账、不拦截 |
| GET | `/memories?q=` | 检索记忆 |
| POST | `/memories` | 新建记忆 |
| DELETE | `/memories/{id}` | 删除记忆 |
| GET / POST | `/precedents` | 仅 owner 可写（v1.1·K9）。body：`{"kind":"decided"\|"rejected","quote":"…","scope":"…","decided_at":"…","expires_at":null}` |
| PUT / DELETE | `/precedents/{id}` | 仅 owner |

**`POST /decisions/{id}:answer`**

请求示例：

```json
{"option": "approve", "save_as_precedent": "decided", "note": "沙盒仓库这类文档改动以后都直接合"}
```

响应示例：

```json
{"decision":{"id":"…","status":"answered","answer":"approve","precedent_id":"01JC…"},
 "event_id":"01JC…"}
```

- `save_as_precedent` 可取 `null`、`decided` 或 `rejected`。
- 返回的 `event_id` 对应生成的 `decision.submitted` 事件。
- 可选的 `option`（§12.3）：`approve`、`reject`、`retry`、`retry_other_engine`、`take_over`、`extend_budget`、`snooze`、`one_more_round`。

### 2.6 配置、系统、发件箱、审计

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/config/{file}` | 返回 `{"file":"rules.yaml","revision":13,"yaml":"…","default_updated":["…"]}` |
| PUT | `/config/{file}` | body：`{"yaml":"…","base_revision":13}`。流程为先校验、再写入、再自动 git 提交，并记审计；冲突返回 409；agent 令牌只读，调用返回 403 |
| POST | `/config:validate` | body：`{"file":"…","yaml":"…"}`，返回 `{"ok":false,"errors":[{"path":"rules[0].accept_mode","msg":"…"}]}` |
| GET | `/config/{file}/versions` | 版本链（`config_versions` 表） |
| POST | `/system:pause` | body：`{"reason":"升级"}`，响应为 `{"paused":true,"paused_at":"…"}`（v1.1·D18） |
| POST | `/system:resume` | 恢复 |
| GET | `/system/state` | 返回 `{"paused":false,"policy_version":"p-…","core_alive":true}` |
| GET | `/effects?status=failed` | 发件箱条目 |
| POST | `/effects/{id}:retry` / `:drop` | 重试 / 丢弃 |
| DELETE | `/subscriptions?thread=<thread_ref>` | 删除某个线程上的全部订阅 |
| GET | `/audit` | 审计记录 |
| POST | `/lease?wait=50&caps=…` | **预留**（v1.1·K11），v1 返回 501 |

### 2.7 Webhook

| 路径 | 鉴权 | 处理 |
|---|---|---|
| `POST /hooks/github` | `X-Hub-Signature-256`（HMAC-SHA256）；请求时间戳不超过 5 分钟 | 用 `X-GitHub-Delivery` 做幂等；返回 202 |
| `POST /hooks/gitea` | `X-Gitea-Signature` + 来源 IP 白名单 | 用 `X-Gitea-Delivery` 做幂等；返回 202 |
| `POST /hooks/feishu` | 飞书事件解密、校验 token、核对点击人 | 长连接的备用通道；卡片回调还要检查 nonce 和是否过期 |

- **签名失败**：返回 401，写入 `events(status=rejected)`。
- **拉黑**：同一来源 5 分钟内失败超过 20 次，拉黑该 IP 1 小时，返回 429，并发 P0 通知。

## 3. CLI（`pa`）

### 3.1 通用约定

| 项目 | 约定 |
|---|---|
| 输出 | 所有命令都支持 `--json`，输出结构与对应 REST 接口的响应相同 [实现约定] |
| 退出码 | 0 成功；2 参数错误；3 权限不足；4 资源不存在 |
| 身份 | 默认为 owner；用 `PA_TOKEN=<agent-token>` 或 `pa --as agent` 切换为 agent |

### 3.2 命令清单

agent 列：✓ 表示 agent 可用，✗ 表示仅 owner。

| 命令 | 参数 | 说明 | agent |
|---|---|---|---|
| `pa init` | — | 生成 `~/.config/pa` 配置模板和 `~/pa-data` 数据目录 | ✗ |
| `pa migrate` | — | Alembic 迁移到最新版本 | ✗ |
| `pa doctor [--full]` | — | 检查依赖、权限和连通性 | ✓ |
| `pa service install\|start\|stop\|restart\|status\|logs <proc>` | proc：`server/core/worker/gui/sandbox/cloudflared/archive` | 管理 launchd 服务 | 只有 status 和 logs ✓ |
| `pa config validate\|show\|get\|set\|diff\|reload` | `get <file> <path>`、`set <file> <path> <value>` | `set` 会自动带上当前 `base_revision` | validate、show、get ✓ |
| `pa event list\|show <id>` | `--source --category --status` | 查看事件 | ✓ |
| `pa event emit <type> --payload @file.json` | — | 发出一个 manual 事件 | ✓ |
| `pa task run <skill> --input @file` | `--priority P0..P3` | 运行任务 | ✓ |
| `pa task list\|show <id>` | `--status --skill` | 查看任务 | ✓ |
| `pa task cancel <id>` | — | 取消任务 | ✗ |
| `pa task retry <id> [--engine <id>]` | — | 重派 | ✓ |
| `pa task pin\|unpin <id>` | — | 置顶 / 取消置顶 | ✓ |
| `pa task move <id> --before <id>\|--after <id>\|--to-top\|--to-bottom` | — | 调整队列顺序 | ✓ |
| `pa task priority <id> P0..P3` | — | 修改优先级 | ✓ |
| `pa task stop <id>` | — | 撤回授权记录 | ✗ |
| `pa task reevaluate <event>` | — | 重新研判 | ✗ |
| `pa skill list\|show\|new <id>\|validate\|test <id> --input @f` | — | 查看、新建、校验、测试 Skill | ✓ |
| `pa skill enable\|disable <id>` | — | 启用 / 停用 | ✗ |
| `pa skill reorder a,b,c` / `pa skill move <id> --before <id>` | — | 调整 Skill 顺序 | 视配置而定（PRD 原文） |
| `pa rule list\|show\|test --event @f` | — | 查看、测试规则 | ✓ |
| `pa rule add\|edit\|enable\|disable\|move <id> --before <id>` | — | 修改规则 | ✗ |
| `pa rule mode <id> off\|observe\|auto` | — | 修改接单模式 | ✗ |
| `pa observe list [--rule <id>]` | — | 查看观察记录 | ✓ |
| `pa engine list\|health` | — | 查看引擎 | ✓ |
| `pa engine test <id>` / `pa engine breaker reset <id>` | — | 测试引擎 / 重置熔断 | ✗ |
| `pa engine playbook record\|dry-run <id>` | — | 录制 / 试运行 GUI 剧本 | ✗ |
| `pa decision list\|show\|answer <id> <option>` | `--precedent decided\|rejected --note "…"` [实现约定，对应卡片上的"记为先例"] | 处理决定 | ✗ |
| `pa cost summary --by skill\|engine\|day` | — | 成本汇总 | ✓ |
| `pa budget list\|set` | — | 预算 | ✗ |
| `pa memory add\|search\|list` | — | 记忆 | ✓ |
| `pa memory delete <id>` | — | 删除记忆 | ✗ |
| `pa secret set\|check\|rotate\|list-meta <ref>` | `set --from-file <path>` | 密钥 | ✗ |
| `pa archive run` | — | 执行归档 | ✗ |
| `pa archive status` | — | 归档状态 | ✓ |
| `pa backup snapshot\|restore <file> [--dry-run]` | — | 快照 / 恢复 | ✗ |
| `pa audit list` | — | 审计记录 | ✗ |
| `pa pause [--reason]` / `pa resume` | — | 全局暂停 / 恢复 | ✗ |
| `pa status` | — | 系统状态 | ✓ |
| `pa precedent list` | — | 查看先例 | ✓ |
| `pa precedent add\|edit\|delete\|expire\|sync` | — | 修改先例；`sync` 与 `precedents/*.md` 双向同步 | ✗ |
| `pa sub list <task>` | — | 查看订阅 | ✓ |
| `pa sub drop <thread>` | — | 删除订阅 | ✗ |
| `pa effect list --status failed` | — | 查看发件箱 | ✓ |
| `pa effect retry\|drop <id>` | — | 重试 / 丢弃 | ✗ |
| `pa sandbox run -- <cmd>` | — | 供引擎调用 | ✓ |
| `pa sandbox images build` | — | 构建镜像 | ✗ |

### 3.3 调用示例

```bash
# 手动触发并查看结果
pa task run sample-direct --input @in.json --json
pa task show 01JC8… --json | jq .stage

# 调整队列顺序
pa task move 01JCA… --before 01JC9…
pa skill reorder sample-code,sample-direct

# 规则先观察，确认后切到 auto
pa observe list --rule r-alert-triage
pa rule mode r-alert-triage auto

# 处理决定，并把这次选择记为先例
pa decision answer 01JCD… approve --precedent decided --note "沙盒文档改动直接合"

# 升级前暂停，升级后恢复
pa pause --reason upgrade && git pull && uv sync --frozen && pa migrate && pa service restart && pa resume

# agent 身份越权，期望退出码为 3
PA_TOKEN=$AGENT_TOKEN pa rule mode r-x auto; echo $?   # 3
```
