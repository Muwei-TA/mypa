# pa-node22 基础镜像（依据 PRD v1.1 §9.1.1、§19.1 sandbox.yaml.images.node）
# 约束与 pa-py312 相同：不放密钥、默认无网络、工作区只读挂载、只有 /out 可写、用完即销毁。
FROM docker.io/library/node:22-slim

ENV NPM_CONFIG_UPDATE_NOTIFIER=false \
    NPM_CONFIG_FUND=false

RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && corepack enable

RUN useradd -m -u 10001 runner && mkdir -p /work /out && chown runner:runner /out
USER runner
WORKDIR /work
