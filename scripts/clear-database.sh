#!/usr/bin/env bash
# 清空本项目配置的 PostgreSQL 数据库中的业务和会话数据。
# 用法：./scripts/clear-database.sh（先停止后端，执行时输入库名确认）

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

exec "$PROJECT_ROOT/.venv/bin/python" "$SCRIPT_DIR/clear-database.py"
