#!/usr/bin/env sh
set -eu

SERVICE_NAME="das_photo"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
RUN_SCRIPT="${PROJECT_DIR}/run.sh"

run_as_root() {
    if [ "$(id -u)" -eq 0 ]; then
        "$@"
    else
        sudo "$@"
    fi
}

check_environment() {
    if [ "$(uname -s)" != "Linux" ]; then
        echo "此脚本只能在 Linux 系统上运行。"
        exit 1
    fi
    if ! command -v systemctl >/dev/null 2>&1; then
        echo "当前系统没有 systemctl，无法安装 systemd 服务。"
        exit 1
    fi
    if [ ! -f "$RUN_SCRIPT" ]; then
        echo "没有找到启动脚本：${RUN_SCRIPT}"
        exit 1
    fi
    if [ "$(id -u)" -ne 0 ] && ! command -v sudo >/dev/null 2>&1; then
        echo "安装服务需要 root 权限，请使用 root 运行此脚本。"
        exit 1
    fi
}

install_service() {
    SERVICE_USER=${SUDO_USER:-$(id -un)}
    TEMP_FILE=$(mktemp)
    trap 'rm -f "$TEMP_FILE"' EXIT HUP INT TERM

    cat >"$TEMP_FILE" <<EOF
[Unit]
Description=DAS Photo Service
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
User=${SERVICE_USER}
WorkingDirectory="${PROJECT_DIR}"
ExecStart=/bin/sh "${RUN_SCRIPT}"
Restart=on-failure
RestartSec=5
Environment=PYTHONUNBUFFERED=1
Environment=DAS_PHOTO_HOST=0.0.0.0
Environment=DAS_PHOTO_PORT=8787
Environment=DAS_PHOTO_OPEN_BROWSER=0

[Install]
WantedBy=multi-user.target
EOF

    run_as_root install -m 0644 "$TEMP_FILE" "$SERVICE_FILE"
    run_as_root systemctl daemon-reload
    run_as_root systemctl enable "${SERVICE_NAME}.service"
    rm -f "$TEMP_FILE"
    trap - EXIT HUP INT TERM

    echo
    echo "服务安装完成：${SERVICE_NAME}.service"
    echo "运行用户：${SERVICE_USER}"
    echo "项目目录：${PROJECT_DIR}"
    echo
    echo "启动：sudo systemctl start ${SERVICE_NAME}"
    echo "停止：sudo systemctl stop ${SERVICE_NAME}"
    echo "重启：sudo systemctl restart ${SERVICE_NAME}"
    echo "状态：sudo systemctl status ${SERVICE_NAME}"
}

remove_service() {
    run_as_root systemctl disable --now "${SERVICE_NAME}.service" 2>/dev/null || true
    run_as_root rm -f "$SERVICE_FILE"
    run_as_root systemctl daemon-reload
    run_as_root systemctl reset-failed "${SERVICE_NAME}.service" 2>/dev/null || true

    echo
    echo "服务已停止并删除：${SERVICE_NAME}.service"
    echo "项目代码、数据库和照片没有删除。"
}

check_environment

echo "================================"
echo " DAS Photo 服务管理"
echo "================================"
echo "1. 安装服务并设置开机启动"
echo "2. 停止并删除服务"
echo "0. 退出"
printf "请选择 [0-2]："
IFS= read -r CHOICE

case "$CHOICE" in
    1) install_service ;;
    2) remove_service ;;
    0) exit 0 ;;
    *)
        echo "无效选项：${CHOICE}"
        exit 1
        ;;
esac
