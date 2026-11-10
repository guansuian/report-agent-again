#!/usr/bin/env bash
# 从任意目录启动 React 开发服务：http://127.0.0.1:5173
set -euo pipefail
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT/frontend"
if [ ! -d node_modules ]; then
    echo "请先安装前端依赖：cd $PROJECT_ROOT/frontend && npm install" >&2
    exit 1
fi
exec npm run dev -- "$@"
