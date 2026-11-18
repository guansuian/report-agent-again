#!/usr/bin/env bash
# 使用局域网 IP 启动后端，供同一网络中的同事访问。
#
# 用法：
#   ./scripts/start-backend-lan.sh
#   ./scripts/start-backend-lan.sh --reload
#
# 默认访问地址：http://10.5.2.10:8000
# PORT 和其他启动参数沿用 start-backend.sh。

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

export HOST=10.5.2.10
exec "$SCRIPT_DIR/start-backend.sh" "$@"
