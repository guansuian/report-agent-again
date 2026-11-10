#!/usr/bin/env bash
# 构建 React 页面，输出到 backend/static，由后端服务提供访问。
set -euo pipefail
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT/frontend"
npm run build
