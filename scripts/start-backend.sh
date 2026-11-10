#!/usr/bin/env bash
# 启动报表 agent 后端（FastAPI + uvicorn）。
#
# 用法：
#   ./scripts/start-backend.sh                 # 默认 http://127.0.0.1:8000
#   ./scripts/start-backend.sh --reload        # 改代码后自动重启（调试用）
#   PORT=8001 ./scripts/start-backend.sh       # 换端口
#   HOST=0.0.0.0 ./scripts/start-backend.sh    # 允许同一局域网的其他设备访问
#
# 停止：Ctrl + C

set -euo pipefail

# 脚本所在目录的上一级 = 项目根目录，不管从哪个目录调用都能找到
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND="$ROOT/backend"
PYTHON="$ROOT/.venv/bin/python"

HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8000}"

# ① 检查虚拟环境和代码目录，缺东西就早点报错，别等 uvicorn 启动到一半才崩
if [ ! -x "$PYTHON" ]; then
    echo "❌ 找不到虚拟环境里的 Python：$PYTHON" >&2
    echo "   请在项目根目录先执行：python3 -m venv .venv && .venv/bin/pip install -e backend -e backend/packages/harness" >&2
    exit 1
fi

if [ ! -d "$BACKEND/app" ]; then
    echo "❌ 找不到后端代码目录：$BACKEND/app" >&2
    exit 1
fi

# ② 打印关键信息，出问题时能一眼看出用的是哪个 Python / 端口
echo "Python : $PYTHON"
echo "目录   : $BACKEND"
echo "地址   : http://$HOST:$PORT"
echo "接口文档: http://$HOST:$PORT/docs"
echo "停止   : Ctrl + C"
echo

# ③ 必须在 backend/ 下启动，否则 import app 会失败
cd "$BACKEND"

# exec：用 uvicorn 替换当前 shell，Ctrl + C 能直接停掉，不留子进程
# "$@"：把命令行额外参数原样传下去，比如 --reload
exec "$PYTHON" -m uvicorn app.gateway.app:app \
    --host "$HOST" \
    --port "$PORT" \
    "$@"
