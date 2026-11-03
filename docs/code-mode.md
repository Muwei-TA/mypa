# 代码模式与 Mac 验证边界

`sample-code` 和 `weekly-cleanup` 现在可被 worker 领取。只有 owner 在 `code.yaml`
明确配置的仓库才允许执行；缺失配置会转人工并给出 `code_setup_failed`，不会无限排队。

最小本地配置：

```yaml
# code.yaml（remote 指向你准备的本地测试仓库，路径不来自任务输入）
repos:
  verify/repo:
    scm: local
    remote: /absolute/path/to/test-repo
    base: main
```

规则还需指定该仓库：

```yaml
rules:
  - id: manual-code
    rank: 2000
    match: {source: manual, skill: sample-code}
    skill: sample-code
    repo: verify/repo
```

通过原有 `pa event emit` 提交 `{"skill":"sample-code","text":"具体文件修改目标"}`。
任务在 `<data>/workspaces/<task_id>` 的 `pa/<task_id>` 分支执行，模型只编辑文件，
PA 负责 Git 提交和 `<data>/code-artifacts/<task_id>/changes.patch`。
源仓库的工作分支不被改写。local 模式停在 `awaiting_decision`，没有假造的 PR URL。

Codex 代码命令采用显式 permission profile，默认拒绝工作区之外的文件读取，只保留
最小工具运行路径和该任务 Git 元数据的只读访问，禁止工具联网；不与旧 `--sandbox`
参数混用。仓库内的 `.codex/config.toml` 被拒绝，避免覆盖运行策略；旧 CLI 如果不支持
严格配置会失败。配置依据为 [OpenAI 权限文档](https://learn.chatgpt.com/docs/permissions)。
Mac 实测涵盖允许工作区写入、拒绝宿主哨兵文件读取和拒绝本机网络连接；不据此声称是 VM。

可选 `check_command: ["/path/to/tool", "arguments"]` 由真实隔离后端在只读工作区执行，
只允许 artifacts 与临时目录写入。没有可用隔离后端则检查失败，保留补丁并转人工。

平台模式还需 scm=github/gitea、可信 remote、agent_token 的 secret 引用及可用 Git
传输身份；Gitea 需 api_url。运行时创建或续用任务 PR，等待 `required_checks`
（默认 `pa/ai-review` 和 `ci`），缺失/失败不能自动结案。平台适配、独立审查模型、
检查修复、发布和部署验证的完整闭环尚未通过真实外部平台验收，不应按历史进度表视为完成。

金额预算在每次原生调用前按剩余 token/时间保守预留。预算不足或单价未知会拒绝有限
金额预算的调用；完成后按实际报告用量结算，重试和修正不清零。被中断、未结算的预留
保持占用，需核对后恢复；不是流式金额硬截断。

规则与人员准入按文件变化刷新；服务绑定、认证服务等启动配置仍需重启服务。
移动 data_dir 应先停止运行时，不支持在活动接入过程中直接切换数据库。

复现真机检查（仅使用隔离实例，不发送飞书消息）：

```sh
uv run --frozen python scripts/verify-macos.py --output .pa-verification/model-new
uv run --frozen python scripts/verify-http-macos.py --output .pa-verification/http-new
```
