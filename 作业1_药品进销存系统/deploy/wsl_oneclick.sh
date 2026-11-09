#!/usr/bin/env bash
# =====================================================================
# 药品进销存管理系统 · WSL(Ubuntu) 一键部署脚本
# ---------------------------------------------------------------------
# 作用：在 WSL 的 Ubuntu 里一次性完成
#       环境自检 → 装 MySQL 8.0 → 开放远程访问 → 建应用账号
#       → 建 13 张表 → 生成约 15 万条数据 → 建索引 → 数据校验
# 用法（在 Ubuntu 终端里粘贴执行）：
#       bash ~/setup_db.sh
# 可选参数：
#       USE_MIRROR=0 bash ~/setup_db.sh    # 不改 apt 镜像源
#       ROOT_PW=xxx APP_PW=yyy bash ~/setup_db.sh
# 日志：~/db_deploy.log
# =====================================================================
set -uo pipefail

# ---------- 可配置项 ----------
# 项目目录：优先用 Linux 原生目录（读写快、无中文路径问题），
# 找不到时回退到 Windows 挂载路径 /mnt/d/...
PROJ="${PROJ:-}"
if [ -z "$PROJ" ]; then
  if [ -d "$HOME/drug_project/sql" ]; then
    PROJ="$HOME/drug_project"
  else
    PROJ="/mnt/d/lyl coding/大三上/数据库课设/作业1_药品进销存系统"
  fi
fi

ROOT_PW="${ROOT_PW:-Root@123456}"
APP_USER="${APP_USER:-druguser}"
APP_PW="${APP_PW:-Drug@123456}"
DB_NAME="${DB_NAME:-drug_db}"
SEED="${SEED:-2026}"
USE_MIRROR="${USE_MIRROR:-1}"
LOG="${LOG:-$HOME/db_deploy.log}"

TABLES="drug_category supplier warehouse employee drug purchase_order purchase_item stock stock_batch sale_order sale_item stock_movement access_log"

step() { echo; echo "=================================================="; echo " $*"; echo "=================================================="; }
die()  { echo; echo "[失败] $*"; echo; echo "日志已保存：$LOG"; exit 1; }

# ---------- 全程写入日志（tee 同时输出到屏幕）----------
exec > >(tee "$LOG") 2>&1

echo "药品进销存管理系统 · WSL 一键部署"
echo "开始时间：$(date '+%Y-%m-%d %H:%M:%S')"

# =====================================================================
step "0/5  环境自检"
# =====================================================================
if [ -r /etc/os-release ]; then
  . /etc/os-release; echo " 发行版 : ${PRETTY_NAME:-unknown}"
fi
echo " 内核   : $(uname -r)"
if grep -qi microsoft /proc/version 2>/dev/null; then
  echo " 平台   : WSL（$(grep -oiE 'WSL[0-9]*' /proc/version | head -1)）"
else
  echo " 平台   : 非 WSL（普通 Ubuntu 也能跑）"
fi
echo " 用户   : $(whoami)"

if [ ! -d "$PROJ" ]; then
  echo
  echo " [错误] 找不到项目目录："
  echo "        $PROJ"
  echo " 当前 /mnt 下可用："
  ls -d /mnt/*/ 2>/dev/null | sed 's/^/   /'
  die "项目目录不存在"
fi
echo " 项目   : $PROJ"

if sudo -n true 2>/dev/null; then
  echo " sudo   : 免密可用"
else
  echo " [提示] 需要 sudo 权限。若下面提示输入密码，请输入你在 Ubuntu 里设的密码"
  echo "        （输入时屏幕不显示字符是正常的）"
  sudo true || die "sudo 验证失败"
fi

# =====================================================================
step "1/5  配置 apt 镜像源（加速下载）"
# =====================================================================
SRC_DEB822="/etc/apt/sources.list.d/ubuntu.sources"
SRC_LEGACY="/etc/apt/sources.list"
BAK=".wbk.bak"
CHANGED_SRC=""

TONA="mirrors.tuna.tsinghua.edu.cn"
if [ "$USE_MIRROR" = "1" ]; then
  if grep -qs "$TONA" "$SRC_DEB822" "$SRC_LEGACY"; then
    echo " 检测到已使用国内镜像源（清华），无需修改"
  elif [ -f "$SRC_DEB822" ] && [ ! -f "${SRC_DEB822}${BAK}" ]; then
    sudo cp "$SRC_DEB822" "${SRC_DEB822}${BAK}"
    sudo sed -i -E 's|https?://[a-z.]*(archive|security)\.ubuntu\.com/ubuntu|https://mirrors.tuna.tsinghua.edu.cn/ubuntu|g' "$SRC_DEB822"
    CHANGED_SRC="$SRC_DEB822"
    echo " 已切换为清华源：$SRC_DEB822"
  elif [ -f "$SRC_LEGACY" ] && [ ! -f "${SRC_LEGACY}${BAK}" ]; then
    sudo cp "$SRC_LEGACY" "${SRC_LEGACY}${BAK}"
    sudo sed -i -E 's|https?://[a-z.]*(archive|security)\.ubuntu\.com/ubuntu|https://mirrors.tuna.tsinghua.edu.cn/ubuntu|g' "$SRC_LEGACY"
    CHANGED_SRC="$SRC_LEGACY"
    echo " 已切换为清华源：$SRC_LEGACY"
  else
    echo " 未找到源文件，跳过"
  fi
else
  echo " USE_MIRROR=0，保持原始源不变"
fi

echo " 执行 apt-get update ..."
if ! sudo apt-get update -y > /dev/null 2>&1; then
  echo " [警告] 换源后 apt update 失败，还原原始源后重试 ..."
  for f in "$SRC_DEB822" "$SRC_LEGACY"; do
    [ -f "${f}${BAK}" ] && sudo cp "${f}${BAK}" "$f"
  done
  sudo apt-get update -y > /dev/null 2>&1 || die "apt-get update 失败：网络不通（请在浏览器验证能否上网）"
  echo " 已还原原始源，update 成功"
else
  echo " apt 源可用"
fi

# =====================================================================
step "2/5  安装 MySQL Server 与 Python 依赖"
# =====================================================================
if command -v mysqld > /dev/null 2>&1; then
  echo " 已安装：$(mysql --version 2>/dev/null | head -1)"
else
  echo " 正在安装 mysql-server（首次约需 1~3 分钟）..."
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y mysql-server || die "mysql-server 安装失败"
  echo " 已安装：$(mysql --version 2>/dev/null | head -1)"
fi

command -v python3 > /dev/null 2>&1 || sudo apt-get install -y python3 || die "python3 安装失败"
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y python3-pip > /dev/null 2>&1 || true
echo " Python : $(python3 --version 2>&1)"

# =====================================================================
step "3/5  启动 MySQL 并配置远程访问账号"
# =====================================================================
echo " 启动服务 ..."
if systemctl list-unit-files 2>/dev/null | grep -q '^mysql\.service'; then
  sudo systemctl enable --now mysql || sudo service mysql start || die "MySQL 启动失败"
  sleep 3
  sudo systemctl --no-pager status mysql 2>/dev/null | head -3 || true
else
  echo " [提示] systemd 未生效，改用 service 启动"
  sudo service mysql start || die "MySQL 启动失败"
  sleep 3
fi

gen_sql() {
  cat <<SQL
ALTER USER 'root'@'localhost' IDENTIFIED WITH $1 BY '${ROOT_PW}';
CREATE USER IF NOT EXISTS 'root'@'127.0.0.1' IDENTIFIED WITH $1 BY '${ROOT_PW}';
ALTER USER 'root'@'127.0.0.1' IDENTIFIED WITH $1 BY '${ROOT_PW}';
GRANT ALL PRIVILEGES ON *.* TO 'root'@'127.0.0.1' WITH GRANT OPTION;
CREATE USER IF NOT EXISTS '${APP_USER}'@'%' IDENTIFIED WITH $1 BY '${APP_PW}';
ALTER USER '${APP_USER}'@'%' IDENTIFIED WITH $1 BY '${APP_PW}';
CREATE DATABASE IF NOT EXISTS \`${DB_NAME}\` DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci;
GRANT ALL PRIVILEGES ON \`${DB_NAME}\`.* TO '${APP_USER}'@'%';
FLUSH PRIVILEGES;
SQL
}

echo " 创建 root 密码与远程账号 ${APP_USER}@'%' ..."

# 探测管理员连接方式：首次运行 root 走 auth_socket（sudo mysql 免密）；
# 已设置过密码后必须用 TCP + 密码，否则重跑会失败
ADMIN_MODE=""
if sudo -n mysql -e "SELECT 1" > /dev/null 2>&1; then
  ADMIN_MODE="socket"
  echo " 管理员连接：sudo mysql（auth_socket）"
elif mysql -h 127.0.0.1 -u root -p"${ROOT_PW}" -e "SELECT 1" > /dev/null 2>&1; then
  ADMIN_MODE="tcp"
  echo " 管理员连接：root@127.0.0.1（已存在密码，重复运行）"
else
  die "无法以管理员身份连接 MySQL。可能 root 密码不是 ${ROOT_PW}，请用 ROOT_PW=你的密码 bash ~/setup_db.sh 重跑"
fi
admin_sql() {
  if [ "$ADMIN_MODE" = "socket" ]; then
    sudo mysql "$@"
  else
    mysql --default-character-set=utf8mb4 -h 127.0.0.1 -u root -p"${ROOT_PW}" "$@"
  fi
}

if ! gen_sql mysql_native_password | admin_sql; then
  echo " [提示] 该 MySQL 版本已禁用 mysql_native_password，改用 caching_sha2_password"
  gen_sql caching_sha2_password | admin_sql || die "账号配置失败"
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y python3-cryptography > /dev/null 2>&1 || true
  echo " 已补装 python3-cryptography（pymysql 走 sha2 认证需要）"
fi
echo " 账号就绪：root@localhost / ${APP_USER}@'%'"

echo " 修改 bind-address = 0.0.0.0（允许从 Windows 侧连接）..."
CONF=""
for f in /etc/mysql/mysql.conf.d/mysqld.cnf /etc/mysql/my.cnf; do
  [ -f "$f" ] && CONF="$f" && break
done
if [ -n "$CONF" ]; then
  [ -f "${CONF}${BAK}" ] || sudo cp "$CONF" "${CONF}${BAK}"
  if grep -qE '^[[:space:]]*bind-address' "$CONF"; then
    sudo sed -i -E 's/^[[:space:]]*bind-address.*/bind-address = 0.0.0.0/' "$CONF"
  else
    printf '\n[mysqld]\nbind-address = 0.0.0.0\n' | sudo tee -a "$CONF" > /dev/null
  fi
  echo " 已修改：$CONF（原文件备份 ${CONF}${BAK}）"
  sudo systemctl restart mysql 2>/dev/null || sudo service mysql restart
  sleep 3
else
  echo " [警告] 未找到 mysqld.cnf，需手动确认 bind-address"
fi

if ! sudo ss -tlnp 2>/dev/null | grep -q ':3306'; then
  echo " [错误] 3306 未监听，MySQL 错误日志末尾："
  sudo tail -20 /var/log/mysql/error.log 2>/dev/null || true
  die "MySQL 未正常监听 3306"
fi
echo " 3306 已监听"

# =====================================================================
step "4/5  建表 → 生成数据 → 建索引 → 校验"
# =====================================================================
[ -f "$PROJ/deploy/deploy_db.sh" ] || die "找不到 $PROJ/deploy/deploy_db.sh"

ROOT_PW="$ROOT_PW" APP_USER="$APP_USER" APP_PW="$APP_PW" \
DB_NAME="$DB_NAME" SEED="$SEED" \
  bash "$PROJ/deploy/deploy_db.sh" || die "数据库部署失败（详见上方输出）"

# =====================================================================
step "5/5  部署结果汇总"
# =====================================================================
WSL_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
echo " MySQL 版本 : $(mysql --version 2>/dev/null | head -1)"
echo " 监听       : $(sudo ss -tlnp 2>/dev/null | grep ':3306' | awk '{print $4}' | head -1)"
echo " Ubuntu IP  : ${WSL_IP}     <-- Windows 客户端用这个地址连接"
echo ""
echo " 各表精确行数："
TOTAL=0
for t in $TABLES; do
  n=$(mysql --default-character-set=utf8mb4 -h 127.0.0.1 -u root -p"${ROOT_PW}" -N \
        -e "SELECT COUNT(*) FROM \`${DB_NAME}\`.\`${t}\`;" 2>/dev/null)
  n=${n:-0}
  TOTAL=$((TOTAL + n))
  printf "   %-20s %8s\n" "$t" "$n"
done
printf "   %-20s %8s\n" "合计" "$TOTAL"
echo ""
echo " 客户端连接参数："
echo "   DB_HOST = ${WSL_IP}"
echo "   DB_PORT = 3306"
echo "   DB_USER = ${APP_USER}"
echo "   DB_PASS = ${APP_PW}"
echo "   DB_NAME = ${DB_NAME}"
echo ""
echo " 结束时间：$(date '+%Y-%m-%d %H:%M:%S')"
if [ "${TOTAL:-0}" -ge 100000 ]; then
  echo " >>> 全部完成：共 ${TOTAL} 条，满足 >=100000 条要求 <<<"
else
  echo " >>> 完成，但总行数 ${TOTAL} 未达 10 万，请检查上方输出 <<<"
fi
echo " 日志文件：$LOG"
