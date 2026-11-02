# SETUP.md：由 Agent 带着部署 PA（v1.1·D20，远期 F-02 的骨架）

> 用法：对 Codex 或 Claude Code 说"读一下 `~/pa/SETUP.md`，带我把 PA 部署起来。"

## 给 Agent 的规则
1. **一次只问一个问题**，问题按下面"参数清单"的顺序依次提出。
2. 所有参数问完后，**先展示完整计划**，包括要执行的命令和要写入的配置文件，等用户确认后再执行。
3. 按 `05-部署与运维指南.md` 的 §2.1 到 §2.4 顺序执行。
4. **涉及密钥的步骤，由用户亲自在终端输入**（`pa secret set …`）。Agent 不要读取、回显或保存任何密钥。
5. 每一步执行完，都运行对应的检查命令，比如 `pa config validate`、`pa doctor`。失败时停下来，告诉用户原因，不要跳过。

## 参数清单（PRD 第 24 章，共 14 项）
1. Gitea 的局域网地址、端口、版本；Bot 账号；Mac mini 的固定局域网 IP
2. GitHub 用 App 还是 PAT；App ID；sandbox、personal、prod 三类各包含哪些仓库
3. 发布和线上核实的方式（tag、workflow_dispatch、script、http_check、version_endpoint）
4. Cloudflare 上托管的域名；控制台是否需要外网访问
5. WebDAV 的服务类型、地址、路径和保留期
6. 密钥后端（默认用钥匙串）
7. 飞书 App ID；卡片发给你本人还是发到群；你的 open_id
8. 各引擎的版本、模型名、接口地址；豆包和 Workbuddy 有没有命令行或本地接口
9. 开机自动登录，还是开启 FileVault（二选一）
10. 暂缓的业务项（可以跳过）
11. 合并身份（merger）；分支保护是否已经开启
12. 容器运行时（Apple Containerization / Tart / Colima）；基础镜像；安装依赖用的网络白名单
13. 需要做版本核实的服务：版本接口地址，以及返回结果中提交 SHA 所在的字段路径
14. 要不要先写入一批先例
