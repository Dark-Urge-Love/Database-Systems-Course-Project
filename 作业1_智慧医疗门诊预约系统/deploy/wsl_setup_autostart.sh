#!/bin/bash
# =====================================================================
# 一次性安装：让 Ubuntu(WSL) 每次启动都自动拉起 MySQL
# ---------------------------------------------------------------------
# 用法（在 Ubuntu 终端里粘贴执行）：
#       bash ~/setup_autostart.sh
#
# 做的事：
#   1. 写 /etc/wsl.conf 的 [boot] command —— WSL 官方的开机自启机制
#   2. 立刻启动 MySQL（这次就能用）
#   3. 把本机 IP 写到 ~/wsl_ip.txt 和 Windows 项目目录，供客户端自动定位
#
# 装完后：以后每次重启电脑，只要打开一次 Ubuntu 图标，MySQL 就自己起来了。
# =====================================================================
set -u

PROJ="/mnt/d/lyl coding/大三上/数据库课设/作业1_智慧医疗门诊预约系统"

echo "=============================================="
echo " 门诊预约系统 · 安装 MySQL 开机自启"
echo "=============================================="
echo ""

# ---------- 1. /etc/wsl.conf ----------
if [ -f /etc/wsl.conf ] && [ ! -f /etc/wsl.conf.wbk.bak ]; then
    sudo cp /etc/wsl.conf /etc/wsl.conf.wbk.bak
    echo "[1/3] 已备份原配置 → /etc/wsl.conf.wbk.bak"
fi

sudo tee /etc/wsl.conf > /dev/null <<'EOF'
[boot]
systemd=true
command = /bin/sh /home/liaoyilin/clinic-db-up.sh

[user]
default=liaoyilin
EOF
echo "[1/3] 已写入 /etc/wsl.conf（WSL 启动时会自动执行 clinic-db-up.sh）"

# ---------- 2. 立刻启动 MySQL ----------
echo "[2/3] 正在启动 MySQL ..."
sudo service mysql start > /dev/null 2>&1 || true
for i in 1 2 3 4 5 6 7 8 9 10; do
    pgrep -x mysqld > /dev/null 2>&1 && break
    sleep 2
done
if pgrep -x mysqld > /dev/null 2>&1; then
    echo "      MySQL 已在运行"
else
    echo "      [警告] MySQL 未能启动，请手动执行： sudo service mysql start"
    echo "             然后查看错误日志： tail -30 /var/log/mysql/error.log"
fi

# ---------- 3. 记录 IP ----------
IP=$(hostname -I 2>/dev/null | awk '{print $1}')
if [ -n "$IP" ]; then
    echo "$IP" > "$HOME/wsl_ip.txt"
    if [ -d "$PROJ" ]; then
        echo "$IP" > "$PROJ/wsl_ip.txt" 2>/dev/null && echo "[3/3] 已把 IP 同步到 Windows 项目目录"
    fi
    echo "[3/3] 本机 IP = $IP"
else
    echo "[3/3] [警告] 未能取得 IP"
fi

echo ""
echo "=============================================="
echo " 完成"
echo "=============================================="
echo " MySQL 状态 : $(pgrep -x mysqld > /dev/null 2>&1 && echo 运行中 || echo 未运行)"
echo " 监听端口   : $(ss -tln 2>/dev/null | grep 3306 | awk '{print $4}' | head -1)"
echo " 服务器 IP  : ${IP:-未知}"
echo ""
echo " 以后每次重启电脑，只要打开一次 Ubuntu 图标即可，MySQL 会自动启动。"
echo "=============================================="
