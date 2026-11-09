#!/usr/bin/env bash
# =====================================================================
# 药品进销存管理系统 · Ubuntu 服务器端一键配置脚本
# 作用：安装 MySQL 8.0 → 开放远程访问 → 创建应用账号 → 配置防火墙
# 适用：Ubuntu 22.04 / 24.04（server 或 desktop 版均可）
# 用法：在 Ubuntu 终端执行
#       chmod +x ubuntu_mysql_setup.sh
#       ./ubuntu_mysql_setup.sh
# 自定义密码（可选）：
#       ROOT_PW='你的密码' APP_PW='你的密码' ./ubuntu_mysql_setup.sh
# =====================================================================
set -euo pipefail

ROOT_PW="${ROOT_PW:-Root@123456}"
APP_USER="${APP_USER:-druguser}"
APP_PW="${APP_PW:-Drug@123456}"
DB_NAME="${DB_NAME:-drug_db}"

echo "=============================================="
echo " 药品进销存管理系统 · Ubuntu 服务器端配置"
echo "=============================================="

# ---------- 1. 安装 MySQL ----------
echo ""
echo "[1/6] 安装 MySQL Server ..."
sudo apt-get update -y
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y mysql-server
echo "      已安装：$(mysql --version)"

# ---------- 2. 启动服务 ----------
echo ""
echo "[2/6] 启动 MySQL 并设置开机自启 ..."
sudo systemctl enable --now mysql
sudo systemctl --no-pager status mysql | head -3 || true

# ---------- 3. 设置 root 密码 + 创建应用账号 ----------
echo ""
echo "[3/6] 设置 root 密码并创建远程访问账号 ${APP_USER} ..."
# Ubuntu 下 MySQL root 默认走 auth_socket，需用 sudo mysql 免密进入
sudo mysql <<SQL
ALTER USER 'root'@'localhost' IDENTIFIED WITH mysql_native_password BY '${ROOT_PW}';
CREATE USER IF NOT EXISTS '${APP_USER}'@'%' IDENTIFIED WITH mysql_native_password BY '${APP_PW}';
ALTER USER '${APP_USER}'@'%' IDENTIFIED WITH mysql_native_password BY '${APP_PW}';
CREATE DATABASE IF NOT EXISTS \`${DB_NAME}\` DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci;
GRANT ALL PRIVILEGES ON \`${DB_NAME}\`.* TO '${APP_USER}'@'%';
FLUSH PRIVILEGES;
SQL
echo "      root@localhost  密码已设置"
echo "      ${APP_USER}@%      可远程连接，权限限于 ${DB_NAME}.*"

# ---------- 4. 开放远程监听 ----------
echo ""
echo "[4/6] 修改 bind-address 为 0.0.0.0（允许远程连接）..."
CONF=""
for f in /etc/mysql/mysql.conf.d/mysqld.cnf /etc/mysql/my.cnf; do
  [ -f "$f" ] && CONF="$f" && break
done
if [ -z "$CONF" ]; then
  echo "      [警告] 未找到 MySQL 配置文件，请手动确认 bind-address"
else
  sudo cp "$CONF" "${CONF}.bak.$(date +%s)"
  if grep -qE '^\s*bind-address' "$CONF"; then
    sudo sed -i -E 's/^\s*bind-address.*/bind-address = 0.0.0.0/' "$CONF"
  else
    printf '\n[mysqld]\nbind-address = 0.0.0.0\n' | sudo tee -a "$CONF" > /dev/null
  fi
  echo "      已修改 $CONF"
  sudo systemctl restart mysql
fi

# ---------- 5. 防火墙 ----------
echo ""
echo "[5/6] 配置防火墙 ..."
if command -v ufw > /dev/null 2>&1; then
  if sudo ufw status | grep -q "Status: active"; then
    sudo ufw allow 3306/tcp
    echo "      ufw 已放行 3306/tcp"
  else
    echo "      ufw 未启用，无需放行"
  fi
else
  echo "      未安装 ufw，跳过"
fi

# ---------- 6. 输出结果 ----------
VM_IP="$(hostname -I | awk '{print $1}')"
echo ""
echo "[6/6] 配置完成，自检结果："
echo "----------------------------------------------"
echo " 虚拟机 IP        : ${VM_IP}"
echo " MySQL 监听状态   :"
sudo ss -tlnp 2>/dev/null | grep 3306 || echo "   （未检测到 3306 监听，请检查 mysql 服务）"
echo "----------------------------------------------"
echo ""
echo "【请记录以下信息，Windows 端要用】"
echo "  DB_HOST = ${VM_IP}"
echo "  DB_PORT = 3306"
echo "  DB_USER = ${APP_USER}"
echo "  DB_PASS = ${APP_PW}"
echo "  DB_NAME = ${DB_NAME}"
echo "  ROOT_PW = ${ROOT_PW}"
echo ""
echo "下一步：在 Windows 端运行 tools/check_conn.py 验证连通性"
echo "        python tools/check_conn.py --host ${VM_IP} --user ${APP_USER} --password ${APP_PW}"
echo ""
