#!/bin/bash
# Goharu×DGM 进化直播:自动跟随最新容器,实时滚动 agent 的思考流
# 用法(在 Git Bash 里): bash E:/Goharu/watch_agent.sh
# 容器销毁(一题考完)后自动等下一题,Ctrl+C 退出
echo "=== 进化直播开始(Ctrl+C 退出)==="
while true; do
  CID=$(docker ps -q --latest)
  if [ -n "$CID" ]; then
    NAME=$(docker inspect -f '{{.Name}}' "$CID" | tr -d '/')
    echo "=== 接入容器 $NAME ($(date +%H:%M:%S)) ==="
    # 等 chat.md 出现(自我修改容器可能还没开始写)
    for i in $(seq 1 30); do
      docker exec "$CID" test -f /out/chat.md 2>/dev/null && break
      sleep 2
    done
    # 从头开始直播;容器消失后 tail 报错退出,循环自动找下一个
    docker exec "$CID" tail -n +1 -f /out/chat.md 2>/dev/null
    echo "=== 容器 $NAME 已销毁(本题结束),等待下一题... ==="
  else
    echo "(当前无运行中容器,5秒后重试)"
  fi
  sleep 5
done
