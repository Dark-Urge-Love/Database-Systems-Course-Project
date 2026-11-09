#!/usr/bin/env bash
# =====================================================================
# 药品进销存管理系统 · 在 Linux 服务器上初始化正式环境（一条命令搞定）
# ---------------------------------------------------------------------
# 做的事：
#   1. 建应用账号 druguser / Drug@123456（远程可连）
#   2. 建库 drug_db
#   3. 建 13 张表（sql/schema.sql）
#   4. 生成 15.3 万条数据（sql/data_gen.py）
#   5. 建索引（sql/index.sql）
#   6. 校验数据量与账实自洽（sql/verify.sql）
#
# 用法（在 Ubuntu 里，项目根目录下）：
#       cd ~/作业1_药品进销存系统      # 或 /mnt/d/.../作业1_药品进销存系统
#       bash deploy/init_server_db.sh
#
# 如果之前已经灌过数据、只想补账号：
#       SKIP_DATA=1 bash deploy/init_server_db.sh
# =====================================================================
set -uo pipefail

PROJ_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP_USER="${APP_USER:-druguser}"
APP_PW="${APP_PW:-Drug@123456}"
DB_NAME="${DB_NAME:-drug_db}"
SEED="${SEED:-2026}"
SKIP_DATA="${SKIP_DATA:-0}"

echo "=============================================="
echo " 药品进销存 · Linux 服务器环境初始化"
echo " 项目目录：${PROJ_ROOT}"
echo " 目标库  ：${DB_NAME}    账号：${APP_USER}"
echo "=============================================="

# ---------- 0. 管理员连接方式探测 ----------
# 首次安装的 MySQL：root 走 auth_socket，用 sudo mysql 免密进入
# 已设过密码：必须用 TCP + 密码
ADMIN_CMD=""
if sudo -n mysql -e "SELECT 1" > /dev/null 2>&1; then
  ADMIN_CMD="sudo mysql"
  echo "[0/5] 管理员连接：sudo mysql（auth_socket）"
elif [ -n "${ROOT_PW:-}" ] && mysql -h 127.0.0.1 -u root -p"${ROOT_PW}" -e "SELECT 1" > /dev/null 2>&1; then
  ADMIN_CMD="mysql --default-character-set=utf8mb4 -h 127.0.0.1 -u root -p${ROOT_PW}"
  echo "[0/5] 管理员连接：root@127.0.0.1（密码方式）"
else
  echo "[0/5] [错误] 无法以管理员身份连接 MySQL。"
  echo "        若 root 已设密码，请这样运行："
  echo "        ROOT_PW='你的root密码' bash deploy/init_server_db.sh"
  exit 1
fi

# ---------- 1. 建账号 + 建库 ----------
echo ""
echo "[1/5] 创建账号 ${APP_USER} 与数据库 ${DB_NAME} ..."
if ! eval "$ADMIN_CMD" < "${PROJ_ROOT}/deploy/create_app_user.sql"; then
  echo "      [提示] mysql_native_password 不可用，改用 caching_sha2_password 重试 ..."
  eval "$ADMIN_CMD" <<SQL || { echo "      [错误] 账号创建失败"; exit 1; }
CREATE USER IF NOT EXISTS '${APP_USER}'@'%' IDENTIFIED WITH caching_sha2_password BY '${APP_PW}';
ALTER  USER '${APP_USER}'@'%' IDENTIFIED WITH caching_sha2_password BY '${APP_PW}';
CREATE DATABASE IF NOT EXISTS \`${DB_NAME}\` DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci;
GRANT ALL PRIVILEGES ON \`${DB_NAME}\`.* TO '${APP_USER}'@'%';
FLUSH PRIVILEGES;
SQL
fi
echo "      完成"

# ---------- 2. 建表 ----------
echo ""
echo "[2/5] 执行 schema.sql（建 13 张表，19 外键 / 13 CHECK）..."
eval "$ADMIN_CMD" < "${PROJ_ROOT}/sql/schema.sql" || { echo "      [错误] 建表失败"; exit 1; }
echo "      完成"

if [ "$SKIP_DATA" = "1" ]; then
  echo ""
  echo "[3/5] SKIP_DATA=1，跳过数据生成"
else
  # ---------- 3. 生成数据 ----------
  echo ""
  echo "[3/5] 生成 15.3 万条数据（约 6~15 秒）..."
  if ! command -v python3 > /dev/null 2>&1; then
    echo "      [错误] 未找到 python3，请先安装：sudo apt-get install -y python3 python3-pip"
    exit 1
  fi
  python3 -c "import pymysql" 2>/dev/null || {
    echo "      安装 pymysql ..."
    python3 -m pip install pymysql -q 2>/dev/null \
      || python3 -m pip install pymysql -q --break-system-packages \
      || sudo apt-get install -y python3-pymysql
  }
  python3 "${PROJ_ROOT}/sql/data_gen.py" \
    --host 127.0.0.1 --user "${APP_USER}" --password "${APP_PW}" \
    --db "${DB_NAME}" --seed "${SEED}" || { echo "      [错误] 数据生成失败"; exit 1; }
fi

# ---------- 4. 建索引 ----------
echo ""
echo "[4/5] 执行 index.sql（建索引，幂等）..."
eval "$ADMIN_CMD" < "${PROJ_ROOT}/sql/index.sql" > /dev/null || { echo "      [错误] 建索引失败"; exit 1; }
echo "      完成"

# ---------- 5. 校验 ----------
echo ""
echo "[5/5] 执行 verify.sql（数据量 / 账实自洽 / 结构统计）..."
eval "$ADMIN_CMD" < "${PROJ_ROOT}/sql/verify.sql"

# ---------- 结果汇总 ----------
VM_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
echo ""
echo "=============================================="
echo " 初始化完成"
echo " 服务器 IP   : ${VM_IP}"
echo " 客户端启动用：set DB_HOST=${VM_IP} && python run.py"
echo "             （默认配置已指向该地址，通常不用手动设）"
echo "=============================================="
