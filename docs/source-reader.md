# URL 原文读取

对应 M1-17、PRD §7.2、§9.2，以及 R-04/R-12/R-23。
`sample-direct` 支持 `payload.url`，也支持 `payload.text` 为单个完整 HTTP(S) URL。
系统先通过注册的 `read_url` 工具读取原文，再将原始请求、最终来源和原文一起
放入 `<untrusted>` 区。引擎只分析已提供的原文，不能凭链接猜测内容。

读取必须同时满足 Skill 的 `tools` 和网络权限。`sample-direct` 当前许可
`github.com` 与 `*.github.com`，其他域名不会自动放行；通配符不包含域名自身，
因此包内资产明确列出 GitHub 根域名。权限来自 Skill，不来自用户提交的 payload。

资源边界是读取工具的实现默认值：20 秒总时限、最多 3 次重定向、1 MiB 响应体、
100000 字符。读取与提示词准备也纳入单任务累计时长。支持文本、JSON 和静态 HTML；
HTML 只取文本并忽略 script/style 等内容，不运行 JavaScript，不读取图片或 PDF。
超过限制会报告失败，不静默截断后宣称完整阅读。

只连接公共地址，拒绝本机、私网、链路本地及其他非公共地址；DNS 回答中出现任何
非公共地址都拒绝。每次跳转重新校验 URL 和地址；校验后直接连接确定的 IP，保留
原始 Host 和 TLS SNI 验证，避免连接时再次解析改变目标。关闭隐式代理，不携带
登录凭证、Cookie 或 Referer，不接受 HTTPS 降级。凭证 URL 和非 80/443 端口拒绝。
要求未压缩响应以保证读取大小边界。

来源无法读取时，任务直接转人工决定并写入 P1 通知；不会调用模型生成虚构摘要。
运行中的 owner 取消和全局执行超时仍会取消读取并释放连接。

验证：`tests/test_read_url.py` 的 32 项行为测试；真实网络已通过公共
`https://github.com/robots.txt` 读取（14540 字符），使用地址固定及正常 TLS 验证。
GitHub API 仓库端点在该次匿名请求返回非成功状态，按读取失败处理，没有改用
未经验证的内容。模型摘要链路当前使用假引擎回归，真实模型的 URL 摘要验收仍待完成。

地址固定使用官方 [HTTPCore SNI 扩展](https://www.encode.io/httpcore/extensions/#sni-hostname)，
HTTP 客户端依然为既有的 httpx，没有增加新的抓取依赖。
