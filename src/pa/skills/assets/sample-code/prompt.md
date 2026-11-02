{# skills/sample-code/prompt.md（Jinja2 模板，PRD §7.1）#}
本任务为代码平台闭环示例（sample-code）。

## 输入
{{ inputs }}

## 要求
1. 在沙盒仓库中按 Issue 描述完成代码修改；
2. 分支名必须为 pa/<task_id>，不得直接改目标分支；
3. PR 描述第一行固定为"本内容由自动化 Agent 生成"，包含任务链接、风险自评与改动摘要；
4. 审查意见 blocker/major 必须修复后重推，minor 只需说明；
5. 不合并自己的 PR；合并由 merger 身份按合并权限矩阵执行；
6. 诚实交付：未完成时标注 draft，失败就报失败。

## 输出
按回执 schema 返回 JSON，code_changed=true，artifacts 指向 PR。
