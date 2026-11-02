# 我定的事（precedent_decided，PRD v1.1 §13.2、§11.1，v1.1·K9）

> 本文件与数据库双向同步：`pa precedent sync`。
> - 来源：卡片上勾选"记为先例"，或执行 `pa precedent add`。
> - 匹配条件：相似度 ≥ 0.85，并且适用范围一致。
> - 下面 4 类**永远不按先例自动处理**，即使写在这里也会照常问你：`delete_data`、`spend_money`、`secret_change`、`permission_change`。
>
> 每条一个二级标题，字段固定如下。`decision_id` 只在由卡片生成时才有。

## <简短标题，例如：沙盒仓库的文档改动直接合并>
- id: <自动生成>
- scope: <适用范围，例如 repo_class=sandbox; skill=sample-code; 只改文档>
- quote: "<你的原话>"
- decided_at: <YYYY-MM-DD>
- decision_id: <可选>
- expires_at: <可选，到期后提醒你复核>
