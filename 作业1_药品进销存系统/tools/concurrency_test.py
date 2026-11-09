# -*- coding: utf-8 -*-
"""
多客户端并发访问与性能测试
=====================================================================
对应课程要求中的"多用户并发访问并记录访问性能"，以及报告里的
「标志性实践 4：多客户端并发访问与性能记录」。

它模拟 N 个客户端同时连接 Linux 服务器上的 MySQL 并持续发起请求，
统计：吞吐量 TPS、平均/中位/P95/最大响应时间、成功率，并按秒采样，
结果同时打印到屏幕、写入 CSV，便于在报告里画折线图。

三种场景（写场景与页面的「并发出库」同一套业务）：
  read   纯查询（单表索引查询 + 多表 join 统计）
  write  纯写事务（销售出库 T2：SELECT ... FOR UPDATE + UPDATE stock）
  mixed  读写混合（默认，7:3）

用法示例：
  python tools/concurrency_test.py                     # 自动找服务器，默认参数
  python tools/concurrency_test.py --clients 30 --duration 20
  python tools/concurrency_test.py --scenario write --clients 20
  python tools/concurrency_test.py --host 192.168.150.181 --user druguser --password Drug@123456
"""
import argparse
import csv
import os
import random
import statistics
import sys
import threading
import time

import pymysql
from pymysql.cursors import DictCursor

PROJ_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ_ROOT)


# =====================================================================
# 连接
# =====================================================================
def make_conn(args):
    return pymysql.connect(
        host=args.host, port=args.port, user=args.user, password=args.password,
        database=args.db, charset="utf8mb4", autocommit=True,
        connect_timeout=10, cursorclass=DictCursor,
    )


# =====================================================================
# 压测数据准备 / 清理
# =====================================================================
def prepare(args, conn):
    """选一个库存最多的 (药品, 仓库) 组合，把库存临时抬到一百万，
       让写事务不会因缺货而失败。返回 (drug_id, wh_id, 原始库存, 销售员ID)。"""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT s.drug_id, s.warehouse_id, s.quantity FROM stock s "
            "JOIN drug d ON d.drug_id = s.drug_id AND d.status = 1 "
            "ORDER BY s.quantity DESC LIMIT 1")
        row = cur.fetchone()
        if not row:
            raise RuntimeError("stock 表没有库存数据，无法压测")
        drug_id, wh_id, stock_orig = row["drug_id"], row["warehouse_id"], row["quantity"]
        cur.execute("UPDATE stock SET quantity = 1000000, update_time = NOW() "
                    "WHERE drug_id = %s AND warehouse_id = %s", (drug_id, wh_id))
        cur.execute("SELECT employee_id FROM employee WHERE position = '销售员' "
                    "AND warehouse_id = %s LIMIT 1", (wh_id,))
        emp = cur.fetchone()
        if not emp:
            raise RuntimeError("该仓库没有销售员")
        cur.execute("SELECT MIN(drug_id) AS p, MAX(drug_id) AS q FROM drug")
        pr = cur.fetchone()
    return drug_id, wh_id, stock_orig, emp["employee_id"], pr["p"], pr["q"]


def cleanup(conn, drug_id, wh_id, stock_orig):
    """恢复库存原值，并清掉压测期间写入的流水，不留任何脏数据。"""
    with conn.cursor() as cur:
        cur.execute("DELETE FROM stock_movement WHERE ref_no = 'BENCH'")
        cur.execute("UPDATE stock SET quantity = %s, update_time = NOW() "
                    "WHERE drug_id = %s AND warehouse_id = %s",
                    (stock_orig, drug_id, wh_id))


# =====================================================================
# 单次操作
# =====================================================================
def op_index_query(conn, ctx):
    """单表索引查询：按药品查库存流水（走复合索引 idx_move_drug_time）"""
    did = random.randint(ctx["pmin"], ctx["pmax"])
    with conn.cursor() as cur:
        cur.execute(
            "SELECT move_id, move_time, move_type, change_qty FROM stock_movement "
            "WHERE drug_id = %s ORDER BY move_time DESC LIMIT 20", (did,),
        )
        cur.fetchall()


def op_join_query(conn, ctx):
    """多表 join 统计：供应商供货排行（采购明细 → 采购单 → 供应商 → 仓库 → 员工，5 表）"""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT s.supplier_name, COUNT(DISTINCT po.po_id) AS cnt, "
            "ROUND(SUM(pi.amount), 2) AS amt FROM purchase_item pi "
            "JOIN purchase_order po ON pi.po_id = po.po_id "
            "JOIN supplier s ON po.supplier_id = s.supplier_id "
            "JOIN warehouse w ON po.warehouse_id = w.warehouse_id "
            "JOIN employee e ON po.employee_id = e.employee_id "
            "GROUP BY s.supplier_id, s.supplier_name ORDER BY amt DESC LIMIT 5"
        )
        cur.fetchall()


def op_range_query(conn, ctx):
    """范围查询：统计近期销售出库量（销售明细 + 销售单）"""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) AS c, COALESCE(SUM(si.quantity), 0) AS qty "
            "FROM sale_item si JOIN sale_order so ON si.so_id = so.so_id "
            "WHERE so.sale_date >= %s",
            ("2026-09-01",),
        )
        cur.fetchall()


def op_dispense(conn, ctx):
    """
    写事务 T2：销售出库（与页面「销售出库」同一套核心逻辑）。
    SELECT ... FOR UPDATE 锁住库存行 → 校验 → 扣减 → 写流水，全程一个事务。
    压测为了隔离变量只扣库存总额，不逐批扣减批次。
    """
    drug_id, wh_id = ctx["drug_id"], ctx["wh_id"]
    conn.begin()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT stock_id, quantity FROM stock "
                "WHERE drug_id = %s AND warehouse_id = %s FOR UPDATE",
                (drug_id, wh_id))
            row = cur.fetchone()
            if not row or row["quantity"] <= 0:
                conn.rollback()
                raise RuntimeError("库存不足")
            cur.execute(
                "UPDATE stock SET quantity = quantity - 1, update_time = NOW() "
                "WHERE stock_id = %s", (row["stock_id"],))
            cur.execute(
                "INSERT INTO stock_movement (drug_id, warehouse_id, move_type, ref_no, "
                "change_qty, before_qty, after_qty, employee_id) "
                "VALUES (%s, %s, '销售出库', 'BENCH', -1, %s, %s, %s)",
                (drug_id, wh_id, row["quantity"], row["quantity"] - 1, ctx["emp"]))
        conn.commit()
        return drug_id
    except Exception:
        try:
            conn.rollback()
        except Exception:                                      # noqa: BLE001
            pass
        raise


def op_restock(conn, ctx):
    """写事务：补货入库（和出库配对，保证压测期间库存不会被耗尽）"""
    drug_id, wh_id = ctx["drug_id"], ctx["wh_id"]
    conn.begin()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT stock_id, quantity FROM stock "
                "WHERE drug_id = %s AND warehouse_id = %s FOR UPDATE",
                (drug_id, wh_id))
            row = cur.fetchone()
            cur.execute(
                "UPDATE stock SET quantity = quantity + 10, update_time = NOW() "
                "WHERE stock_id = %s", (row["stock_id"],))
            cur.execute(
                "INSERT INTO stock_movement (drug_id, warehouse_id, move_type, ref_no, "
                "change_qty, before_qty, after_qty, employee_id) "
                "VALUES (%s, %s, '采购入库', 'BENCH', 10, %s, %s, %s)",
                (drug_id, wh_id, row["quantity"], row["quantity"] + 10, ctx["emp"]))
        conn.commit()
        return drug_id
    except Exception:
        try:
            conn.rollback()
        except Exception:                                      # noqa: BLE001
            pass
        raise


# 场景定义：操作 → 权重
SCENARIOS = {
    "read":  [("index_query", 55, op_index_query), ("join_query", 30, op_join_query),
              ("range_query", 15, op_range_query)],
    "write": [("dispense", 70, op_dispense), ("restock", 30, op_restock)],
    "mixed": [("index_query", 40, op_index_query), ("join_query", 20, op_join_query),
              ("range_query", 10, op_range_query), ("dispense", 20, op_dispense),
              ("restock", 10, op_restock)],
}


# =====================================================================
# 压测主体
# =====================================================================
class Collector:
    def __init__(self):
        self.lock = threading.Lock()
        self.latencies = []          # [(name, ms, ok)]
        self.per_second = {}         # 秒索引 -> 完成数
        self.errors = {}
        self.start = None

    def add(self, name, ms, ok, err=None):
        with self.lock:
            self.latencies.append((name, ms, ok))
            if ok:
                bucket = int(time.time() - self.start)
                self.per_second[bucket] = self.per_second.get(bucket, 0) + 1
            else:
                self.errors[str(err)[:120]] = self.errors.get(str(err)[:120], 0) + 1


def worker(args, ctx, stop_at, collector, worker_id):
    rng = random.Random(1000 + worker_id)
    try:
        conn = make_conn(args)
    except Exception as e:                                     # noqa: BLE001
        collector.add("connect", 0.0, False, e)
        return
    ops = SCENARIOS[args.scenario]
    total_weight = sum(w for _, w, _ in ops)
    try:
        while time.time() < stop_at:
            r = rng.uniform(0, total_weight)
            acc = 0
            picked = ops[-1]
            for name, weight, fn in ops:
                acc += weight
                if r <= acc:
                    picked = (name, weight, fn)
                    break
            name, _w, fn = picked
            t0 = time.perf_counter()
            try:
                fn(conn, ctx)
                ok, err = True, None
            except Exception as e:                             # noqa: BLE001
                ok, err = False, e
            collector.add(name, (time.perf_counter() - t0) * 1000, ok, err)
    finally:
        try:
            conn.close()
        except Exception:                                      # noqa: BLE001
            pass


def percentile(values, p):
    if not values:
        return 0.0
    values = sorted(values)
    k = (len(values) - 1) * p / 100.0
    lo, hi = int(k), min(int(k) + 1, len(values) - 1)
    return values[lo] + (values[hi] - values[lo]) * (k - lo)


def run(args):
    # ---------- 找服务器 ----------
    if not args.host:
        from webapp import config, netprobe
        config.DB_USER, config.DB_PASSWORD, config.DB_NAME = args.user, args.password, args.db
        args.host = netprobe.ensure_server(config, force=True, verbose=True)
        if not args.host:
            print("[错误] 没找到可用的数据库服务器。请确认 Ubuntu 正在运行且 MySQL 已启动，")
            print("       或用 --host 显式指定地址。")
            return 2

    print("=" * 66)
    print(" 多客户端并发性能测试")
    print("=" * 66)
    print(" 目标服务器 : %s:%d" % (args.host, args.port))
    print(" 数据库账号 : %s" % args.user)
    print(" 并发客户端 : %d" % args.clients)
    print(" 持续时长   : %d 秒" % args.duration)
    print(" 测试场景   : %s" % args.scenario)
    print("=" * 66)

    conn = make_conn(args)
    drug_id, wh_id, stock_orig, emp_id, pmin, pmax = prepare(args, conn)
    print(" [准备] 压测药品 drug_id=%s（库存临时抬到 1,000,000），患者 ID 范围 %s~%s"
          % (drug_id, pmin, pmax))
    print(" [准备] 测试结束后会自动恢复该药品的原有库存，不会污染业务数据")
    print("-" * 66)

    ctx = {"drug_id": drug_id, "wh_id": wh_id, "emp": emp_id, "pmin": pmin, "pmax": pmax}
    collector = Collector()
    collector.start = time.time()
    stop_at = collector.start + args.duration

    threads = [threading.Thread(target=worker, args=(args, ctx, stop_at, collector, i))
               for i in range(args.clients)]
    print(" [开始] %s" % time.strftime("%H:%M:%S"))
    t_start = time.perf_counter()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    elapsed = time.perf_counter() - t_start

    # ---------- 汇总 ----------
    rows = collector.latencies
    ok_rows = [(n, ms) for n, ms, ok in rows if ok]
    all_ms = [ms for _, ms in ok_rows]
    failed = len(rows) - len(ok_rows)
    tps = len(ok_rows) / elapsed if elapsed > 0 else 0

    print("-" * 66)
    print(" 测试完成，用时 %.2f 秒" % elapsed)
    print("=" * 66)
    print(" 总请求数        : %d" % len(rows))
    print(" 成功 / 失败     : %d / %d" % (len(ok_rows), failed))
    print(" 成功率          : %.2f%%" % (len(ok_rows) / len(rows) * 100 if rows else 0))
    print(" 吞吐量 TPS      : %.1f 次/秒" % tps)
    print(" 平均响应时间    : %.2f ms" % (statistics.mean(all_ms) if all_ms else 0))
    print(" 中位数 P50      : %.2f ms" % percentile(all_ms, 50))
    print(" P95            : %.2f ms" % percentile(all_ms, 95))
    print(" P99            : %.2f ms" % percentile(all_ms, 99))
    print(" 最大响应时间    : %.2f ms" % (max(all_ms) if all_ms else 0))
    print("-" * 66)

    # 分操作类型
    print(" 分操作统计：")
    by_op = {}
    for name, ms, ok in rows:
        d = by_op.setdefault(name, {"n": 0, "ok": 0, "ms": []})
        d["n"] += 1
        if ok:
            d["ok"] += 1
            d["ms"].append(ms)
    for name, d in sorted(by_op.items()):
        avg = statistics.mean(d["ms"]) if d["ms"] else 0
        print("   %-12s 次数 %-6d 成功 %-6d 平均 %7.2f ms  P95 %7.2f ms"
              % (name, d["n"], d["ok"], avg, percentile(d["ms"], 95)))
    if collector.errors:
        print("-" * 66)
        print(" 错误明细：")
        for msg, cnt in sorted(collector.errors.items(), key=lambda x: -x[1])[:5]:
            print("   [%d 次] %s" % (cnt, msg))

    # ---------- 按秒采样 ----------
    seconds = sorted(collector.per_second)
    print("-" * 66)
    print(" 每秒完成请求数（T 为第几秒）：")
    spark = ""
    maxv = max(collector.per_second.values()) if collector.per_second else 1
    for s in seconds:
        v = collector.per_second[s]
        bars = int(v / maxv * 40) if maxv else 0
        spark += "T%-3d %-5d %s\n" % (s + 1, v, "#" * bars)
    print("   " + spark.replace("\n", "\n   ").rstrip())

    # ---------- 写 CSV ----------
    stamp = time.strftime("%Y%m%d_%H%M%S")
    detail_path = os.path.join(PROJ_ROOT, "tools", "concurrency_detail_%s.csv" % stamp)
    summary_path = os.path.join(PROJ_ROOT, "tools", "concurrency_summary_%s.csv" % stamp)

    with open(detail_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["操作", "耗时ms", "是否成功"])
        for name, ms, ok in rows:
            w.writerow([name, "%.3f" % ms, "1" if ok else "0"])

    with open(summary_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["秒", "完成请求数"])
        for s in seconds:
            w.writerow([s + 1, collector.per_second[s]])
        w.writerow([])
        w.writerow(["指标", "数值"])
        w.writerow(["目标服务器", "%s:%d" % (args.host, args.port)])
        w.writerow(["并发客户端数", args.clients])
        w.writerow(["持续时长(秒)", "%.2f" % elapsed])
        w.writerow(["场景", args.scenario])
        w.writerow(["总请求数", len(rows)])
        w.writerow(["成功数", len(ok_rows)])
        w.writerow(["失败数", failed])
        w.writerow(["成功率(%)", "%.2f" % (len(ok_rows) / len(rows) * 100 if rows else 0)])
        w.writerow(["TPS", "%.2f" % tps])
        w.writerow(["平均响应时间(ms)", "%.2f" % (statistics.mean(all_ms) if all_ms else 0)])
        w.writerow(["P50(ms)", "%.2f" % percentile(all_ms, 50)])
        w.writerow(["P95(ms)", "%.2f" % percentile(all_ms, 95)])
        w.writerow(["P99(ms)", "%.2f" % percentile(all_ms, 99)])
        w.writerow(["最大响应时间(ms)", "%.2f" % (max(all_ms) if all_ms else 0)])

    print("-" * 66)
    print(" 详细记录已写入 : %s" % detail_path)
    print(" 汇总结果已写入 : %s" % summary_path)
    print(" （summary 里的「秒/完成请求数」两列可以直接在 Excel 里画 TPS 折线图）")

    # ---------- 清理 ----------
    cleanup(conn, drug_id, wh_id, stock_orig)
    print(" [清理] 药品 %s 的库存已恢复为 %s" % (drug_id, stock_orig))
    conn.close()
    print("=" * 66)
    return 0


def main():
    ap = argparse.ArgumentParser(description="药品进销存管理系统 · 多客户端并发性能测试")
    ap.add_argument("--host", default=None, help="数据库服务器地址（不填则自动探测）")
    ap.add_argument("--port", type=int, default=3306)
    ap.add_argument("--user", default="druguser")
    ap.add_argument("--password", default="Drug@123456")
    ap.add_argument("--db", default="drug_db")
    ap.add_argument("--clients", type=int, default=20, help="并发客户端（线程）数")
    ap.add_argument("--duration", type=int, default=15, help="持续秒数")
    ap.add_argument("--scenario", choices=list(SCENARIOS), default="mixed",
                    help="read / write / mixed")
    args = ap.parse_args()
    try:
        return run(args)
    except KeyboardInterrupt:
        print("\n已中断")
        return 130
    except pymysql.MySQLError as e:
        print("[数据库错误] %s" % e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
