# 执行额度与适配器边界

对应 M1-13–15、PRD §8.1/§15/§19.1 及 R-23。额度不依赖金额预算；货币预算现已接入派单和原生调用预留；金额计量不替代 token/轮数/时间上限。所有工作区升级后先执行 `pa migrate`；轮数额度从 `0005_turn_allowance`
引入，当前 head 为 `0008_review_repairs`。

## 跨调用计量

- token 根据适配器报告的输入/输出累计，包含失败、修正与重派；不是流式硬截断。
- 时长累计输入准备和引擎执行，包含 URL 读取，不含排队和退避。
- 轮数先按 `min(引擎 max_turns, Skill 剩余轮数)` 在 runs 中持久预留，之后才调用引擎。
  适配器可靠报告迭代数后返还未用部分；没有报告、正常取消或突然退出时保留整笔额度。
  新 worker 从 runs 恢复，修正、换引擎、重派和租约回收都不清零。
- 达到额度或引擎报告超过本次限额时转人工决定，写入原因码和 P1 通知；不再继续调用。
- 旧版本没有记录轮数的 run 在迁移时标为 unknown；未完成任务重新执行前请 owner
  核实，不把历史未知用量当作零。

`runs.turns_charged` 是已占用额度，`turns_estimated` 表示含保守预留，不能展示成
精确的实际模型轮数；`turns_unknown` 表示迁移前没有可核实的历史记录。
这些是适配器/执行器元数据，不新增模型回执字段，不采用模型自报的轮数。

## 原生限制

Hermes 使用显式 `--max-turns`（优先于用户配置）和 `HERMES_MAX_ITERATIONS`，
从 usage 文件的 `api_calls` 读取主循环调用数；WorkBuddy 使用 `--max-turns`，
从 CLI JSON 的 `num_turns` 读取。失败退出也先提取已报告用量，再按错误类型处理。
CLI 能力仍需要对应安装版本和真实登录的验收，不能仅凭参数构造测试宣称实调用完成。

Codex `exec --json` 的 `turn.started/completed` 是对话轮次，不能当作内部所有模型/
工具迭代数。本实现只累计其 token 事件，对未报告的内部轮数保留整笔预留额度；
尚未证明 Codex 单次调用内的精确轮数上限。因此完整 R-23 仍待核实其内部可控执行
接口，不能用任务调用次数或预留额度代替该项验收。官方事件定义见
[OpenAI Docs：非交互模式](https://developers.openai.com/codex/noninteractive/)。

## 验证依据

`tests/test_turn_allowance.py`：跨重试/修正/故障转移/重派累计，取消后租约恢复不清零，
未知报告保守记账、已报告用量返还、原生请求剩余限额、失败用量提取、旧库升级及
Codex 不伪造内部轮数。`tests/test_execution_limits.py` 保留此前 token/超时回归。
