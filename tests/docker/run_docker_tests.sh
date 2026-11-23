#!/usr/bin/env bash
# Goharu Linux Docker 安装/卸载测试编排器。
#   ./run_docker_tests.sh                 # 默认测 ubuntu:24.04 和 debian:12
#   ./run_docker_tests.sh ubuntu:22.04 rockylinux:9   # 自定义镜像列表
set -uo pipefail
cd "$(dirname "$0")"

BASES=("$@")
if [ ${#BASES[@]} -eq 0 ]; then BASES=(ubuntu:24.04 debian:12); fi

TOTAL_PASS=0; TOTAL_FAIL=0
for base in "${BASES[@]}"; do
  tag="goharu-test:$(echo "$base" | tr ':/.' '--')"
  echo ""
  echo "############################################################"
  echo "# 发行版: $base"
  echo "############################################################"
  if ! docker build --build-arg BASE_IMAGE="$base" -t "$tag" -f Dockerfile . >/tmp/build_"$tag".log 2>&1; then
    echo "  [构建失败] 见 /tmp/build_$tag.log"; tail -5 /tmp/build_"$tag".log; TOTAL_FAIL=$((TOTAL_FAIL+1)); continue
  fi
  docker run --rm "$tag"; rc=$?
  if [ "$rc" = "0" ]; then TOTAL_PASS=$((TOTAL_PASS+1)); else TOTAL_FAIL=$((TOTAL_FAIL+1)); fi
done

echo ""
echo "============================================================"
echo "发行版汇总: $TOTAL_PASS 通过, $TOTAL_FAIL 失败"
[ "$TOTAL_FAIL" = "0" ]
