#!/usr/bin/env bash
# 容器化便捷脚本：统一管理「开发模式」和「生产模式」两套 compose。
#
# 用法:
#   ./compose.sh dev    up|down|restart|build|logs|ps|config [服务名]
#   ./compose.sh prod   up|down|restart|build|logs|ps|config [服务名]
#   ./compose.sh mw     up|down|restart|logs|ps              # 只操作中间件（等价于容器化之前）
#   ./compose.sh help
#
# 示例:
#   ./compose.sh dev up
#   ./compose.sh dev logs backend
#   ./compose.sh dev restart backend
#   IMAGE_TAG=v0.5 ./compose.sh prod up build
#   ./compose.sh prod up            # 用同一 IMAGE_TAG 重新拉起（回滚时用）

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

BASE_FILE="docker-compose.yml"
DEV_FILE="docker-compose.dev.yml"
PROD_FILE="docker-compose.prod.yml"
IMAGE_TAG="${IMAGE_TAG:-latest}"

BLUE='\033[0;34m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
info()  { echo -e "${BLUE}[INFO]${NC} $*"; }
ok()    { echo -e "${GREEN}[OK]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
die()   { echo -e "${RED}[ERR]${NC} $*" >&2; exit 1; }

# 统一入口：compose <mode> <action> [args...]
compose() {
  local mode="$1"; shift
  case "$mode" in
    dev)  docker compose -f "$BASE_FILE" -f "$DEV_FILE" "$@" ;;
    prod) docker compose -f "$BASE_FILE" -f "$PROD_FILE" "$@" ;;
    mw)   docker compose -f "$BASE_FILE" "$@" ;;
    *)    die "未知模式: $mode（可选 dev / prod / mw）" ;;
  esac
}

mode_desc() {
  case "$1" in
    dev)  echo "开发模式（源码挂载 + 热重载）" ;;
    prod) echo "生产模式（镜像固化，IMAGE_TAG=$IMAGE_TAG）" ;;
    mw)   echo "仅中间件" ;;
  esac
}

run_action() {
  local mode="$1" action="$2"; shift 2
  local extra=("$@")

  case "$action" in
    up)
      # prod 允许 up build 一次构建并启动
      if [ "$mode" = "prod" ] && [ "${extra[0]:-}" = "build" ]; then
        info "构建并启动 $(mode_desc "$mode")"
        compose "$mode" up -d --build "${extra[@]:1}"
      else
        info "启动 $(mode_desc "$mode")"
        compose "$mode" up -d "${extra[@]}"
      fi
      ok "已启动"
      compose "$mode" ps
      ;;
    down)
      info "停止 $(mode_desc "$mode") 的业务容器"
      if [ "$mode" = "mw" ]; then
        compose "$mode" down "${extra[@]}"
      else
        # 只停业务容器，保留中间件继续跑
        local services=(backend frontend)
        [ "$mode" = "prod" ] && services+=(worker)
        compose "$mode" stop "${services[@]}" 2>/dev/null || true
        compose "$mode" rm -f "${services[@]}" 2>/dev/null || true
        compose "mw" ps
      fi
      ok "已停止"
      ;;
    restart)
      [ $# -eq 0 ] || true
      info "重启 $(mode_desc "$mode") ${extra[*]:-(全部)}"
      compose "$mode" up -d --force-recreate --no-deps "${extra[@]}"
      ;;
    build)
      info "构建 $(mode_desc "$mode")"
      compose "$mode" build "${extra[@]}"
      ok "构建完成"
      ;;
    logs)
      compose "$mode" logs -f --tail=100 "${extra[@]}"
      ;;
    ps|status)
      compose "$mode" ps
      ;;
    config)
      compose "$mode" config
      ;;
    *)
      die "未知操作: $action（可选 up / down / restart / build / logs / ps / config）"
      ;;
  esac
}

check_docker() {
  docker info >/dev/null 2>&1 || die "Docker 未运行，请先启动 Docker Desktop"
}

help() {
  cat <<'EOF'
容器化便捷脚本（两套模式并存）

用法:
  ./compose.sh <mode> <action> [服务名...]

mode:
  dev    开发模式——源码挂载、热重载，改代码秒级生效
  prod   生产模式——代码固化进镜像，restart: always，含采集 Worker
  mw     只操作中间件（PG/Redis/Milvus/ES/MinIO），等价于容器化之前

action:
  up              启动（prod 支持 `up build` 一并构建）
  down            停止业务容器（中间件保持运行）
  restart [服务]  重建并重启指定服务
  build [服务]    构建镜像
  logs [服务]     跟踪日志
  ps              查看状态
  config          打印合并后的完整配置（排错用）

环境变量:
  IMAGE_TAG   镜像标签，默认 latest。部署时用它做版本标记，便于回滚：
              IMAGE_TAG=v0.5 ./compose.sh prod up build
              IMAGE_TAG=v0.4 ./compose.sh prod up        # 回滚
  WEB_PORT    生产模式前端对外端口，默认 5183

常见用法:
  ./compose.sh dev up                  # 日常开发
  ./compose.sh dev logs backend        # 看后端日志
  ./compose.sh dev restart backend     # 改了 .env 之后
  ./compose.sh dev build backend       # 改了依赖清单之后
  ./compose.sh mw up                   # 只想起数据库等中间件
EOF
}

main() {
  local mode="${1:-}"; local action="${2:-}"
  if [ -z "$mode" ] || [ "$mode" = "help" ] || [ "$mode" = "-h" ] || [ "$mode" = "--help" ]; then
    help; exit 0
  fi
  [ -n "$action" ] || die "缺少操作参数，试试 ./compose.sh $mode up"
  check_docker
  shift 2
  run_action "$mode" "$action" "$@"
}

main "$@"
