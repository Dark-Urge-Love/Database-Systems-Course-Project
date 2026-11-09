# ~/.bash_aliases
# 由 Ubuntu 默认的 ~/.bashrc 自动加载（见 .bashrc 第 104-105 行）
# ---------------------------------------------------------------------
# 数据库课设用：打开 Ubuntu 终端时，自动确保 MySQL 已启动，
# 并把本机 IP 写到 ~/wsl_ip.txt 与 Windows 项目目录，
# 好让 Windows 上的 Flask 客户端能自动找到这台 Linux 数据库服务器。
# ---------------------------------------------------------------------

if [ -f "$HOME/drug-db-up.sh" ] && ! pgrep -x mysqld > /dev/null 2>&1; then
    nohup sh "$HOME/drug-db-up.sh" > /dev/null 2>&1 &
fi

alias dbup='sudo service mysql start'
alias dbstatus='pgrep -x mysqld > /dev/null 2>&1 && echo "MySQL 运行中 · $(hostname -I | awk "{print \$1}"):3306" || echo "MySQL 未运行，执行 dbup 启动"'

# ---------------------------------------------------------------------
# 项目目录（含空格与中文，直接 cd 很容易敲错），做成两个短命令
# ---------------------------------------------------------------------
PROJ_DIR="/mnt/d/lyl coding/大三上/数据库课设/作业1_药品进销存系统"

alias proj='cd "$PROJ_DIR" && pwd'
# 一条命令完成服务器端初始化：建 druguser / 建 drug_db / 建表 / 灌 15.3 万条 / 建索引 / 校验
alias dbinit='cd "$PROJ_DIR" && bash deploy/init_server_db.sh'
alias dbcheck='cd "$PROJ_DIR" && python3 tools/selfcheck_req.py'
