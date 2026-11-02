# 定时事件

对应 M1-08，PRD §5.2、§19.4、§21。先运行 `pa migrate` 升级至
`0003_schedules`，再使用 `pa run`。单次 `pa run --once` 不启动后台调度器。
`schedules.yaml` 可省略；默认是空列表，不会替用户选定汇总时间。

配置示例：

```yaml
schedules:
  - id: daily-example
    cron: "0 9 * * *"
    event_type: cron.example
    payload:
      skill: sample-direct
      text: "待处理文本"
    enabled: true
```

事件仍经过 rules.yaml。规则应匹配 `source: cron` 或对应 `type`；默认主动接单
模式为 observe。明确设置规则的 `accept_mode: auto` 后才会自动创建任务。
全局暂停时事件照常入库，派单与 effects 保持暂停。

cron 为 APScheduler 3.x 的五字段表达式，使用 `pa.yaml.timezone`。
建议星期使用 `mon`–`sun`；数字星期遵循该版本的 `0=周一`。
支持的字段为 `id`、`cron`、`event_type`、`payload`、`enabled`；未知字段、无效
cron、重复 id、未知时区会被配置校验拒绝。`enabled` 默认 true 是启停的实现约定。

调度作业和下一触发点均保存在本地 SQLite。事件与下一触发点同事务提交；失败时
两者均回滚。重启或短暂错误后，按原触发时间逐条补接，每个调度每次同步最多接收
一个触发点，避免一次性扫描或投递无界积压。同一 id 与触发时间只接单一次。
修改配置或时区后重算下一触发点，不补旧配置；禁用/删除时移除作业与游标。
配置每个核心监控周期（约一秒）同步一次。

实现参考 [APScheduler 用户指南](https://apscheduler.readthedocs.io/en/3.x/userguide.html)
与 [cron API](https://apscheduler.readthedocs.io/en/3.x/modules/triggers/cron.html)。
