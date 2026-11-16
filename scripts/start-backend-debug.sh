#!/usr/bin/env bash
# 开启 DEBUG 日志启动后端，包含 Journal 记录的完整模型输入。
#
# 用法：
#   ./scripts/start-backend-debug.sh
#   ./scripts/start-backend-debug.sh --reload  # 改代码后自动重启
#
# HOST、PORT 和其他启动参数沿用 start-backend.sh。

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# 应用日志使用 LOG_LEVEL；Uvicorn 自身的日志使用 --log-level。
export LOG_LEVEL=DEBUG
exec "$SCRIPT_DIR/start-backend.sh" --log-level debug "$@"
