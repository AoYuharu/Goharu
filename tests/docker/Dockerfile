# Goharu 安装/卸载测试镜像（模拟一台装了基础工具的用户 Linux 机）。
# 基础工具齐备（git/python3/venv/node/npm），install.py 的环境检查才放行——
# 这正是"普通用户装好前置依赖后"的状态。仓库代码由 inside_test.sh 在容器内
# 从 GitHub master 克隆，保证测的是真实对外代码。
ARG BASE_IMAGE=ubuntu:24.04
FROM ${BASE_IMAGE}

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_MIRROR=https://pypi.tuna.tsinghua.edu.cn/simple

RUN set -eux; \
    if command -v apt-get >/dev/null 2>&1; then \
      apt-get update; \
      apt-get install -y --no-install-recommends \
        git curl ca-certificates python3 python3-venv python3-pip nodejs npm; \
      rm -rf /var/lib/apt/lists/*; \
    elif command -v dnf >/dev/null 2>&1; then \
      dnf install -y git curl ca-certificates python3 python3-pip nodejs npm; \
      dnf clean all; \
    else \
      echo "unsupported base" >&2; exit 1; \
    fi

WORKDIR /work
COPY inside_test.sh /work/inside_test.sh
RUN chmod +x /work/inside_test.sh
CMD ["/work/inside_test.sh"]
