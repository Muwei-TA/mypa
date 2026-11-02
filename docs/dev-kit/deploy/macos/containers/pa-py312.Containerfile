# pa-py312 基础镜像（依据 PRD v1.1 §9.1.1、§19.1 sandbox.yaml.images.python）
# 用于一次性容器：测试、构建、依赖安装、AI 审查。
# 约束：
#   - 镜像中不放任何密钥
#   - 运行时网络默认关闭（network: none），只有 install 阶段按白名单开放
#   - 工作区以只读方式挂载到 /work，只有 /out 可写
#   - 用完即销毁
# 构建命令：pa sandbox images build（由 sandbox.yaml.runtime 选择 container / tart / docker 的构建方式）
FROM docker.io/library/python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_CACHE_DIR=/tmp/uv-cache

RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates build-essential \
 && rm -rf /var/lib/apt/lists/* \
 && pip install --no-cache-dir uv

RUN useradd -m -u 10001 runner && mkdir -p /work /out && chown runner:runner /out
USER runner
WORKDIR /work
# 无入口程序：由 pa sandbox run -- <cmd> 指定要执行的命令
