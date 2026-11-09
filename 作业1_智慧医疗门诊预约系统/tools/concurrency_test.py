# -*- coding: utf-8 -*-
"""
多客户端并发访问与性能测试
=====================================================================
对应课程要求中的"多用户并发访问并记录访问性能"，以及报告里的
「标志性实践 4：多客户端并发访问与性能记录」。

它模拟 N 个客户端同时连接 Linux 服务器上的 MySQL 并持续发起请求，
统计：吞吐量 TPS、平均/中位/P95/最大响应时间、成功率，并按秒采样，
结果同时打印到屏幕、写入 CSV，便于在报告里画折线图。

三种场景（写场景与页面的「并发发药」同一套业务）：
  read   纯查询（单表索引查询 + 多表 join 统计）
  write  纯写事务（药房发药 T2：SELECT ... FOR UPDATE + UPDATE stock）
  mixed  读写混合（默认，7:3）

用法示例：
  python tools/concurrency_test.py                     # 自动找服务器，默认参数
  python tools/concurrency_test.py --clients 30 --duration 20
  python tools/concurrency_test.py --scenario write --clients 20
  python tools/concurrency_test.py --host 192.168.150.181 --user clinic --password Clinic@123456
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
    """选一种在用药品，把库存临时抬到一百万，让写事务不会因缺货而失败。
       返回 (drug_id, 原始库存, 患者 ID 范围)。"""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT drug_id, stock FROM drug WHERE status = 1 "
            "ORDER BY stock DESC LIMIT 1")
        row = cur.fetchone()
        if not row:
            raise RuntimeError("drug 表没有在用药品，无法压测")
        drug_id, stock_orig = row["drug_id"], row["stock"]
        cur.execute("UPDATE drug SET stock = 1000000 WHERE drug_id = %s", (drug_id,))
        cur.execute("SELECT MIN(patient_id) AS p, MAX(patient_id) AS q FROM patient")
        pr = cur.fetchone()
    return drug_id, stock_orig, pr["p"], pr["q"]


def cleanup(conn, drug_id, stock_orig):
    """把药品库存恢复成压测前的值，不留下任何脏数据。"""
    with conn.cursor() as cur:
        cur.execute("UPDATE drug SET stock = %s WHERE drug_id = %s", (stock_orig, drug_id))


# =====================================================================
# 单次操作
# =====================================================================
def op_index_query(conn, ctx):
    """单表索引查询：按 patient_id 查预约（走 idx_appt_patient）"""
    pid = random.randint(ctx["pmin"], ctx["pmax"])
    with conn.cursor() as cur:
        cur.execute(
            "SELECT appt_id, appt_time, queue_no, status FROM appointment "
            "WHERE patient_id = %s LIMIT 20", (pid,),
        )
        cur.fetchall()


def op_join_query(conn, ctx):
    """多表 join 统计：科室就诊量（visit + doctor + department）"""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT dp.dept_name, COUNT(*) AS cnt FROM visit v "
            "JOIN doctor d ON v.doctor_id = d.doctor_id "
            "JOIN department dp ON d.dept_id = dp.dept_id "
            "GROUP BY dp.dept_name ORDER BY cnt DESC LIMIT 5"
        )
        cur.fetchall()


def op_range_query(conn, ctx):
    """范围查询：按就诊时间统计"""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) AS c FROM prescription_item i "
            "JOIN prescription pr ON i.presc_id = pr.presc_id "
            "WHERE pr.presc_time >= %s",
            ("2026-01-01 00:00:00",),
        )
        cur.fetchall()


def op_dispense(conn, ctx):
    """
    写事务 T2：药房发药（与页面「并发发药」同一套逻辑）。
    SELECT ... FOR UPDATE 锁住药品行 → 校验库存 → 扣减，全程一个事务。
    """
    drug_id = ctx["drug_id"]
    conn.begin()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT stock FROM drug WHERE drug_id = %s FOR UPDATE", (drug_id,))
            row = cur.fetchone()
            if not row or row["stock"] <= 0:
                conn.rollback()
                raise RuntimeError("库存不足")
            cur.execute(
                "UPDATE drug SET stock = stock - 1 WHERE drug_id = %s", (drug_id,))
        conn.commit()
        return drug_id
    except Exception:
        try:
            conn.rollback()
        except Exception:                                      # noqa: BLE001
            pass
        raise


def op_restock(conn, ctx):
    """写事务：药房补货入库（和发药配对，保证压测期间库存不会被耗尽）"""
    drug_id = ctx["drug_id"]
    conn.begin()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT stock FROM drug WHERE drug_id = %s FOR UPDATE", (drug_id,))
            cur.fetchone()
            cur.execute(
                "UPDATE drug SET stock = stock + 10 WHERE drug_id = %s", (drug_id,))
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
    drug_id, stock_orig, pmin, pmax = prepare(args, conn)
    print(" [准备] 压测药品 drug_id=%s（库存临时抬到 1,000,000），患者 ID 范围 %s~%s"
          % (drug_id, pmin, pmax))
    print(" [准备] 测试结束后会自动恢复该药品的原有库存，不会污染业务数据")
    print("-" * 66)

    ctx = {"drug_id": drug_id, "pmin": pmin, "pmax": pmax}
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
    cleanup(conn, drug_id, stock_orig)
    print(" [清理] 药品 %s 的库存已恢复为 %s" % (drug_id, stock_orig))
    conn.close()
    print("=" * 66)
    return 0


def main():
    ap = argparse.ArgumentParser(description="门诊预约系统 · 多客户端并发性能测试")
    ap.add_argument("--host", default=None, help="数据库服务器地址（不填则自动探测）")
    ap.add_argument("--port", type=int, default=3306)
    ap.add_argument("--user", default="clinic")
    ap.add_argument("--password", default="Clinic@123456")
    ap.add_argument("--db", default="outpatient_db")
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
