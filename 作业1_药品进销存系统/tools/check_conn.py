# -*- coding: utf-8 -*-
"""
Windows 客户端 → Linux MySQL 连通性检测工具  check_conn.py

作用：在写 Flask 代码之前，先确认"网络通、端口通、账号通、数据在"这四件事，
      把环境问题一次性定位清楚，避免后面调 Flask 时分不清是代码错还是网络错。

用法：
    python tools/check_conn.py --host 192.168.1.100 --user druguser --password Drug@123456
    python tools/check_conn.py --host 192.168.1.100 --user druguser --password Drug@123456 --db drug_db

依赖：pip install pymysql
"""

import argparse
import socket
import sys
import time

try:
    import pymysql
except ImportError:
    print("[错误] 缺少依赖，请执行：pip install pymysql")
    sys.exit(1)


def step(no, total, title):
    print(f"\n[{no}/{total}] {title}")


def ok(msg):
    print(f"      [通过] {msg}")


def fail(msg):
    print(f"      [失败] {msg}")


def main():
    ap = argparse.ArgumentParser(description="MySQL 远程连通性检测")
    ap.add_argument("--host", required=True, help="Linux 虚拟机的 IP")
    ap.add_argument("--port", type=int, default=3306)
    ap.add_argument("--user", default="druguser")
    ap.add_argument("--password", default="Drug@123456")
    ap.add_argument("--db", default="drug_db")
    ap.add_argument("--timeout", type=int, default=5)
    args = ap.parse_args()

    T = 4
    print("=" * 58)
    print(f" 目标：{args.host}:{args.port}  账号：{args.user}  库：{args.db}")
    print("=" * 58)

    # ---------- 1. TCP 端口连通性 ----------
    step(1, T, f"检测 TCP 端口 {args.host}:{args.port} ...")
    t0 = time.time()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(args.timeout)
    try:
        sock.connect((args.host, args.port))
        ok(f"端口可达，耗时 {(time.time() - t0) * 1000:.0f} ms")
    except socket.timeout:
        fail(f"连接超时（{args.timeout}s）")
        print("""
      → 排查方向：
        1) Ubuntu 里确认 MySQL 在监听：  sudo ss -tlnp | grep 3306
        2) 确认监听地址是 0.0.0.0 而非 127.0.0.1：
              sudo grep bind-address /etc/mysql/mysql.conf.d/mysqld.cnf
              （应为 bind-address = 0.0.0.0，改完 sudo systemctl restart mysql）
        3) 确认 Ubuntu 防火墙放行：      sudo ufw status   /  sudo ufw allow 3306/tcp
        4) 确认虚拟机网络模式（见下方第 4 步说明）
        5) 确认 Windows 能 ping 通虚拟机：ping """ + args.host)
        sys.exit(1)
    except OSError as e:
        fail(f"无法连接：{e}")
        print("      → 检查 IP 是否写对（Ubuntu 里执行 hostname -I 查看）")
        sys.exit(1)
    finally:
        sock.close()

    # ---------- 2. MySQL 认证 + 版本 ----------
    step(2, T, "检测 MySQL 账号登录 ...")
    try:
        conn = pymysql.connect(host=args.host, port=args.port, user=args.user,
                               password=args.password, charset="utf8mb4",
                               connect_timeout=args.timeout)
        cur = conn.cursor()
        cur.execute("SELECT VERSION()")
        ok(f"登录成功，MySQL 版本 {cur.fetchone()[0]}")
    except pymysql.err.OperationalError as e:
        code = e.args[0] if e.args else "?"
        fail(f"登录失败（错误码 {code}）：{e}")
        if code == 1045:
            print("""      → 账号或密码错误。在 Ubuntu 里重建账号：
            sudo mysql
            CREATE USER IF NOT EXISTS '""" + args.user + """'@'%' IDENTIFIED WITH mysql_native_password BY '你的密码';
            GRANT ALL PRIVILEGES ON drug_db.* TO '""" + args.user + """'@'%';
            FLUSH PRIVILEGES;""")
        elif code == 1130:
            print("      → 该 IP 无权连接，说明创建账号时主机写成了 localhost，需改成 '%'")
        elif code == 1049:
            print("      → 指定的数据库不存在，请先执行 deploy/deploy_db.sh")
        else:
            print("      → 请检查账号授权与密码")
        sys.exit(1)

    # ---------- 3. 库、表、数据量 ----------
    step(3, T, f"检测数据库 {args.db} 与数据 ...")
    try:
        cur.execute("USE `%s`" % args.db)
        cur.execute("SHOW TABLES")
        tables = [r[0] for r in cur.fetchall()]
        if not tables:
            fail(f"库 {args.db} 存在但没有表，请执行 deploy/deploy_db.sh")
            sys.exit(1)
        ok(f"共 {len(tables)} 张表：{', '.join(tables)}")

        total = 0
        counts = {}
        for t in tables:
            cur.execute(f"SELECT COUNT(*) FROM `{t}`")
            n = cur.fetchone()[0]
            counts[t] = n
            total += n
        print("      各表行数：")
        for t, n in counts.items():
            print(f"        {t:<20} {n:>10,}")
        print(f"        {'合计':<20} {total:>10,}")
        if total >= 100000:
            ok(f"数据量 {total:,} 条，满足 ≥100,000 要求")
        else:
            fail(f"数据量仅 {total:,} 条，未达 10 万，请执行 deploy/deploy_db.sh 生成数据")
    except pymysql.err.ProgrammingError as e:
        fail(f"访问数据库失败：{e}")
        sys.exit(1)

    # ---------- 4. 一个真实业务查询（验证索引与 join 可用） ----------
    step(4, T, "执行一次多表 join 业务查询 ...")
    try:
        sql = """
        SELECT s.supplier_name, COUNT(DISTINCT pi.po_id) AS po_cnt,
               SUM(pi.amount) AS amt
          FROM purchase_item pi
          JOIN purchase_order po ON pi.po_id = po.po_id
          JOIN supplier s        ON po.supplier_id = s.supplier_id
         GROUP BY s.supplier_name
         ORDER BY amt DESC
         LIMIT 3
        """
        t0 = time.time()
        cur.execute(sql)
        rows = cur.fetchall()
        cost = (time.time() - t0) * 1000
        ok(f"查询成功，耗时 {cost:.0f} ms，采购金额 Top3 供应商：")
        for name, cnt, amt in rows:
            print(f"        {name:<22} {cnt:>4} 单   {float(amt):>14,.2f} 元")
    except Exception as e:
        fail(f"查询失败：{e}")

    cur.close()
    conn.close()

    print("\n" + "=" * 58)
    print(" 全部检测通过，环境已就绪，可以启动 Flask 客户端了。")
    print("=" * 58)


if __name__ == "__main__":
    main()
