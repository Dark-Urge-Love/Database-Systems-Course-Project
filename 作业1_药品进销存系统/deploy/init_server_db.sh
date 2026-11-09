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
# 常见情况：
#       # root 已设过密码（脚本不猜密码，必须显式给）
#       ROOT_PW='你的root密码' bash deploy/init_server_db.sh
#
#       # 只想补账号，数据已经灌过
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

# =====================================================================
# 0. 找到一种能用的管理员登录方式
# ---------------------------------------------------------------------
# Ubuntu 新装的 MySQL，root 走 auth_socket —— 只要 sudo 能过，`sudo mysql`
# 就免密进得去。注意不能用 `sudo -n`（-n = 绝不询问密码），
# 而 Ubuntu 的 sudo 通常要输密码，加了 -n 必然失败。
# 顺序：sudo 免密 → sudo 输密码 → root 密码方式
# =====================================================================
if ! command -v mysql > /dev/null 2>&1; then
  echo "[0/6] [错误] 找不到 mysql 客户端，请先安装：sudo apt-get install -y mysql-server"
  exit 1
fi

ADMIN_CMD=""

# ---------- 方式 1：sudo 免密（有 NOPASSWD 规则的机器） ----------
if sudo -n true 2> /dev/null && sudo -n mysql -e "SELECT 1" > /dev/null 2>&1; then
  ADMIN_CMD="sudo mysql"
  echo "[0/6] 管理员连接：sudo mysql（免密 auth_socket）"
fi

# ---------- 方式 2：sudo + 输入密码（最常见） ----------
if [ -z "$ADMIN_CMD" ]; then
  echo "[0/6] 需要管理员权限访问 MySQL。"
  echo "      若下面出现 [sudo] password for ...，请输入你登录 Ubuntu 的密码。"
  echo "      （输入时屏幕上不会显示字符，这是正常的）"
  if sudo mysql -e "SELECT 1" > /dev/null 2>&1; then
    ADMIN_CMD="sudo mysql"
    echo "      管理员连接：sudo mysql（auth_socket）"
  fi
fi

# ---------- 方式 3：root + 密码（root 已改成密码登录的情况） ----------
if [ -z "$ADMIN_CMD" ]; then
  if [ -z "${ROOT_PW:-}" ]; then
    printf "      请输入 MySQL 的 root 密码（直接回车跳过）："
    read -r -s ROOT_PW
    echo ""
  fi
  if [ -n "${ROOT_PW:-}" ] && mysql -h 127.0.0.1 -u root -p"${ROOT_PW}" -e "SELECT 1" > /dev/null 2>&1; then
    ADMIN_CMD="mysql --default-character-set=utf8mb4 -h 127.0.0.1 -u root -p${ROOT_PW}"
    echo "      管理员连接：root@127.0.0.1（密码方式）"
  fi
fi

if [ -z "$ADMIN_CMD" ]; then
  cat <<'TIP'
[0/6] [错误] 三种管理员登录方式都失败了，请按顺序排查：

  1) MySQL 服务没启动
       sudo service mysql start          # 或 sudo systemctl start mysql
       sudo ss -tlnp | grep 3306

  2) 你的账号没 sudo 权限，或密码输错
       sudo -v                           # 单独验证一次 sudo

  3) root 已经设了密码，但脚本不知道
       ROOT_PW='你的root密码' bash deploy/init_server_db.sh

  4) 忘记 root 密码 —— Ubuntu 默认允许用 auth_socket 直接进：
       sudo mysql
     进入后执行：ALTER USER 'root'@'localhost' IDENTIFIED WITH auth_socket;
     退出后重跑本脚本即可。
TIP
  exit 1
fi

# =====================================================================
# 1. 建账号 + 建库
# =====================================================================
echo ""
echo "[1/6] 创建账号 ${APP_USER} 与数据库 ${DB_NAME} ..."
if ! eval "$ADMIN_CMD" < "${PROJ_ROOT}/deploy/create_app_user.sql" > /dev/null 2>&1; then
  echo "      [提示] mysql_native_password 不可用（MySQL 8.4+ 已移除），改用 caching_sha2_password ..."
  eval "$ADMIN_CMD" <<SQL || { echo "      [错误] 账号创建失败"; exit 1; }
CREATE USER IF NOT EXISTS '${APP_USER}'@'%' IDENTIFIED WITH caching_sha2_password BY '${APP_PW}';
ALTER  USER '${APP_USER}'@'%' IDENTIFIED WITH caching_sha2_password BY '${APP_PW}';
CREATE DATABASE IF NOT EXISTS \`${DB_NAME}\` DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci;
GRANT ALL PRIVILEGES ON \`${DB_NAME}\`.* TO '${APP_USER}'@'%';
FLUSH PRIVILEGES;
SQL
  # pymysql 走 sha2 认证需要 cryptography
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y python3-cryptography > /dev/null 2>&1 || true
  echo "      已补装 python3-cryptography（pymysql 走 sha2 认证需要）"
fi

# 回读确认（不打印密码）
eval "$ADMIN_CMD" -N -e "
SELECT CONCAT('      账号 ', user, '@', host, ' 认证方式 ', plugin) FROM mysql.user WHERE user='${APP_USER}';
SELECT CONCAT('      库   ', schema_name) FROM information_schema.schemata WHERE schema_name='${DB_NAME}';" \
  2> /dev/null || true

# =====================================================================
# 2. 建表
# =====================================================================
echo ""
echo "[2/6] 执行 schema.sql（建 13 张表，19 外键 / 13 CHECK）..."
eval "$ADMIN_CMD" < "${PROJ_ROOT}/sql/schema.sql" || { echo "      [错误] 建表失败"; exit 1; }
echo "      完成"

# =====================================================================
# 3. 生成数据
# =====================================================================
if [ "$SKIP_DATA" = "1" ]; then
  echo ""
  echo "[3/6] SKIP_DATA=1，跳过数据生成"
else
  echo ""
  echo "[3/6] 生成 15.3 万条数据（约 6~15 秒）..."
  if ! command -v python3 > /dev/null 2>&1; then
    echo "      [错误] 未找到 python3，请先安装：sudo apt-get install -y python3 python3-pip"
    exit 1
  fi
  if ! python3 -c "import pymysql" 2> /dev/null; then
    echo "      安装 pymysql ..."
    python3 -m pip install pymysql -q 2> /dev/null \
      || python3 -m pip install pymysql -q --break-system-packages \
      || sudo apt-get install -y python3-pymysql \
      || { echo "      [错误] pymysql 安装失败"; exit 1; }
  fi
  python3 "${PROJ_ROOT}/sql/data_gen.py" \
    --host 127.0.0.1 --user "${APP_USER}" --password "${APP_PW}" \
    --db "${DB_NAME}" --seed "${SEED}" || { echo "      [错误] 数据生成失败"; exit 1; }
fi

# =====================================================================
# 4. 建索引
# =====================================================================
echo ""
echo "[4/6] 执行 index.sql（建索引，幂等）..."
eval "$ADMIN_CMD" < "${PROJ_ROOT}/sql/index.sql" > /dev/null || { echo "      [错误] 建索引失败"; exit 1; }
echo "      完成"

# =====================================================================
# 5. 校验
# =====================================================================
echo ""
echo "[5/6] 执行 verify.sql（数据量 / 账实自洽 / 结构统计）..."
eval "$ADMIN_CMD" < "${PROJ_ROOT}/sql/verify.sql"

# =====================================================================
# 6. 结果汇总 + 把 IP 写给 Windows 客户端
# =====================================================================
VM_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
if [ -n "$VM_IP" ]; then
  # 客户端启动时优先读这个文件，能省掉几秒网段扫描
  echo "$VM_IP" > "${PROJ_ROOT}/wsl_ip.txt" 2> /dev/null && \
    echo "[6/6] 已把服务器 IP 写入 ${PROJ_ROOT}/wsl_ip.txt"
else
  echo "[6/6] [警告] 未能取得本机 IP，请在 Ubuntu 里执行 hostname -I 查看"
fi

echo ""
echo "=============================================="
echo " 初始化完成"
echo " 服务器 IP  : ${VM_IP:-未知}"
echo " 账号 / 库  : ${APP_USER} / ${DB_NAME}"
echo ""
echo " 回到 Windows，重启客户端即可（无需改配置）："
echo "   .venv\\Scripts\\python.exe run.py"
echo ""
echo " 自检命令："
echo "   python tools/check_conn.py --host ${VM_IP:-<IP>} --user ${APP_USER} --password ${APP_PW}"
echo "=============================================="
