{# skills/sample-direct/prompt.md（Jinja2 模板，PRD §7.1）#}
你负责处理一条输入：先读取原文，再核实，最后给出结论。

## 任务
- 读取输入文本；URL 的原文由系统 `read_url` 工具先行读取并提供。不能凭 URL 猜测未提供的内容。
- 核实其中的关键事实。每条结论都要附上依据，写入 `evidence`。
- 本 Skill 不改代码：`code_changed` 必须为 false。

## 输出
按统一回执格式输出 JSON（§7.3）：
- `summary`：一句话结论。
- 不需要人决定时，不要填 `human_action`。
- 确实需要人决定时，`human_action` 只放**一个具体问题**，附推荐选项，以及不超过 60 字的理由。
- 核实后发现无需处理时，`status` 填 `dropped`。
- 没有完成时，`draft` 填 true。不要把未完成写成已完成。

## 输入
{{ inputs }}
