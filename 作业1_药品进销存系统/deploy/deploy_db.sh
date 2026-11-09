#!/usr/bin/env bash
# =====================================================================
# 药品进销存管理系统 · 数据库部署脚本
# 作用：建库建表(schema.sql) → 生成约 15 万条数据(data_gen.py)
#       → 建索引(index.sql) → 校验(verify.sql)
# 运行位置：Ubuntu 虚拟机内（需已执行过 ubuntu_mysql_setup.sh）
# 用法：
#       chmod +x deploy_db.sh
#       ./deploy_db.sh
# 自定义：
#       ROOT_PW='你的密码' APP_PW='你的密码' SEED=2026 ./deploy_db.sh
# =====================================================================
set -euo pipefail

PROJ_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ROOT_PW="${ROOT_PW:-Root@123456}"
APP_USER="${APP_USER:-druguser}"
APP_PW="${APP_PW:-Drug@123456}"
DB_NAME="${DB_NAME:-drug_db}"
SEED="${SEED:-2026}"

echo "=============================================="
echo " 药品进销存管理系统 · 数据库部署"
echo " 项目根目录：${PROJ_ROOT}"
echo "=============================================="

# ---------- 0. 准备 Python 依赖 ----------
echo ""
echo "[0/4] 检查 Python 与 pymysql ..."
if ! command -v python3 > /dev/null 2>&1; then
  echo "      [错误] 未找到 python3，请先安装：sudo apt-get install -y python3 python3-pip"
  exit 1
fi
if python3 -c "import pymysql" 2>/dev/null; then
  echo "      pymysql 已就绪"
else
  echo "      安装 pymysql ..."
  python3 -m pip install pymysql -q 2>/dev/null \
    || python3 -m pip install pymysql -q --break-system-packages \
    || { sudo apt-get install -y python3-pymysql && echo "      已通过 apt 安装 python3-pymysql"; }
fi

# ---------- 1. 建库建表 ----------
echo ""
echo "[1/4] 执行 schema.sql（建库建表，含外键/唯一/CHECK 约束）..."
mysql --default-character-set=utf8mb4 -h 127.0.0.1 -u root -p"${ROOT_PW}" < "${PROJ_ROOT}/sql/schema.sql"
echo "      完成：13 张表已创建"

# ---------- 2. 生成数据 ----------
echo ""
echo "[2/4] 生成初始数据（约 15 万条：药品/采购/批次/库存/销售/流水）..."
python3 "${PROJ_ROOT}/sql/data_gen.py" \
  --host 127.0.0.1 --user "${APP_USER}" --password "${APP_PW}" \
  --db "${DB_NAME}" --seed "${SEED}"

# ---------- 3. 创建索引 ----------
echo ""
echo "[3/4] 执行 index.sql（创建查询性能索引）..."
mysql --default-character-set=utf8mb4 -h 127.0.0.1 -u root -p"${ROOT_PW}" < "${PROJ_ROOT}/sql/index.sql" > /dev/null
echo "      完成"

# ---------- 4. 校验 ----------
echo ""
echo "[4/4] 执行 verify.sql（数据量 + 账实自洽 + 约束统计 + 索引对比）..."
mysql --default-character-set=utf8mb4 -h 127.0.0.1 -u root -p"${ROOT_PW}" < "${PROJ_ROOT}/sql/verify.sql"

echo ""
echo "=============================================="
echo " 部署完成！"
echo " 虚拟机 IP：$(hostname -I | awk '{print $1}')"
echo " 下一步：在 Windows 端跑 tools/check_conn.py 验证连通性，"
echo "         然后启动 Flask 客户端。"
echo "=============================================="
