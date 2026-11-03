{# skills/sample-code/prompt.md（Jinja2 模板，PRD §7.1）#}
本任务为代码平台闭环示例（sample-code）。

## 输入
{{ inputs }}

## 要求
1. 在 PA 指定工作区中按任务描述完成文件修改；
2. 分支名必须为 pa/<task_id>，不得直接改目标分支；不自行提交或推送；
3. PA 负责创建 PR，描述第一行固定为"本内容由自动化 Agent 生成"；
4. 审查意见 blocker/major 必须修复，minor 只需说明；检查及推送由 PA 负责；
5. 不合并自己的 PR；合并由 merger 身份按合并权限矩阵执行；
6. 诚实交付：未完成时标注 draft，失败就报失败。

## 输出
按回执 schema 返回 JSON，code_changed 按实际改动填写，artifacts 指向实际修改文件。
在本任务中，仅缺少尚由 PA 负责创建的 PR 不表示文件修改失败；不得编造 PR URL。
