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
