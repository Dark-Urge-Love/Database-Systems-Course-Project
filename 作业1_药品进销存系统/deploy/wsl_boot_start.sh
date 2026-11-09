#!/bin/sh
# =====================================================================
# Ubuntu(WSL) 启动时自动执行：
#   1. 把 MySQL 拉起来（WSL 每次开机 MySQL 不会自己起来，必须显式启动）
#   2. 把自己的 IP 写到两个地方，方便 Windows 端自动找到服务器：
#        /home/liaoyilin/wsl_ip.txt                       （WSL 内）
#        D:\...\作业1_药品进销存系统\wsl_ip.txt      （Windows 项目目录）
#   3. 记录日志到 /var/log/drug-db-autostart.log
#
# 这个脚本由 /etc/wsl.conf 的 [boot] command 调用，开机自动运行，
# 不需要用户做任何操作。
# =====================================================================

LOG=/var/log/drug-db-autostart.log
HOME_DIR=/home/liaoyilin
WIN_DIR="/mnt/d/lyl coding/大三上/数据库课设/作业1_药品进销存系统"

# 由 /etc/wsl.conf 的 boot command 调用时是 root；由 ~/.bashrc 触发时是普通用户，需要 sudo
if [ "$(id -u)" = "0" ]; then
    SUDO=""
else
    SUDO="sudo"
    LOG="$HOME_DIR/drug-db-autostart.log"
fi

{
    echo "=============================================="
    echo "[$(date '+%F %T')] Ubuntu(WSL) 启动，开始检查 MySQL"

    # ---------- 1. 启动 MySQL（失败重试，最多等 30 秒）----------
    started=0
    i=1
    while [ $i -le 15 ]; do
        if pgrep -x mysqld >/dev/null 2>&1; then
            echo "  MySQL 已在运行（第 ${i} 次检查）"
            started=1
            break
        fi
        $SUDO service mysql start >/dev/null 2>&1
        sleep 2
        i=$((i + 1))
    done
    if [ $started -eq 0 ]; then
        if pgrep -x mysqld >/dev/null 2>&1; then
            echo "  MySQL 启动成功"
        else
            echo "  [警告] MySQL 启动失败，请手动执行： sudo service mysql start"
        fi
    fi

    # 等端口就绪
    sleep 2

    # ---------- 2. 记录 IP ----------
    IP=""
    if command -v hostname >/dev/null 2>&1; then
        IP=$(hostname -I 2>/dev/null | awk '{print $1}')
    fi
    [ -z "$IP" ] && IP=$(ip -4 addr show eth0 2>/dev/null | grep -oE 'inet [0-9.]+' | awk '{print $2}' | head -1)

    if [ -n "$IP" ]; then
        echo "$IP" > "$HOME_DIR/wsl_ip.txt"
        chown liaoyilin:liaoyilin "$HOME_DIR/wsl_ip.txt" 2>/dev/null
        echo "  本机 IP：$IP"
    else
        echo "  [警告] 未能取得 IP"
    fi

    # ---------- 3. 写一份到 Windows 项目目录（drvfs 可能挂载较慢，后台重试）----------
    (
        n=1
        while [ $n -le 30 ]; do
            if [ -d "$WIN_DIR" ]; then
                echo "$IP" > "$WIN_DIR/wsl_ip.txt" 2>/dev/null && break
            fi
            sleep 1
            n=$((n + 1))
        done
    ) &

    echo "  监听检查："
    ss -tln 2>/dev/null | grep 3306 || echo "    [警告] 3306 未在监听"
    echo "[$(date '+%F %T')] 完成"
} >> "$LOG" 2>&1
