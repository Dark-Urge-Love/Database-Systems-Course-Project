# -*- coding: utf-8 -*-
"""
Service 层：业务逻辑与事务
------------------------------------------------------------------
三个核心事务：

  T1  采购入库  —— 采购单 + 明细 + 批次 + 库存 + 流水，一次原子写入（6 张表）
  T2  销售出库  —— 锁定库存行 + 校验 + 批次近效期先出 + 扣减 + 流水（5 张表）
  T2' 并发出库  —— 多终端同时卖同一种药，验证行锁不超卖，并记录每次访问耗时

事务要点（报告里可以直接讲）：
  · SELECT ... FOR UPDATE 对目标行加排他锁，第二个并发事务会阻塞等待
  · 锁内重新校验 quantity，把"检查—扣减"放进同一个原子区间
  · 任何一步失败 → 整个事务 ROLLBACK，不会出现"扣了库存却没有出库单"
  · 数据库的 CHECK 约束（quantity >= 0、after_qty = before_qty + change_qty）
    是最后一道防线
"""
import random
import statistics
import threading
import time
from datetime import date, datetime

from . import dao
from .db import transaction, BizError, get_cursor


def _no(prefix):
    """生成单号：前缀 + 日期 + 时分秒 + 2 位随机，避免并发下重号。"""
    now = datetime.now()
    return "%s%s%s%02d" % (prefix, now.strftime("%Y%m%d"), now.strftime("%H%M%S"),
                           random.randint(0, 99))


# =====================================================================
# T1  采购入库
# =====================================================================
def receive_purchase(supplier_id, warehouse_id, employee_id, drug_id,
                     qty, unit_price, batch_no=None, prod_date=None, expiry_date=None):
    """
    库房到货登记：一张采购单 → 明细 → 入库批次 → 库存增加 → 库存流水。

    所有写入在同一个事务里完成，任何一步失败（比如药品不存在、批号重复）
    都会整体回滚，不会出现"库存加了但没有采购单"这种账实不符。
    """
    po_no = _no("PO")
    today = date.today()
    if not batch_no:
        batch_no = "B%s%03d" % (today.strftime("%y%m%d"), random.randint(1, 999))

    with transaction() as cur:
        # ① 药品必须存在且在售
        cur.execute("SELECT drug_id, drug_name, spec, unit, shelf_life_months "
                    "FROM drug WHERE drug_id = %s AND status = 1", (drug_id,))
        drug = cur.fetchone()
        if not drug:
            raise BizError("药品不存在或已停用，请刷新后重选")

        amount = round(qty * unit_price, 2)

        # ② 采购单（状态直接为已入库，本演示不做"先下单后到货"的两阶段）
        cur.execute(
            "INSERT INTO purchase_order (po_no, supplier_id, warehouse_id, employee_id, "
            "order_date, expect_date, total_amount, status) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, '已入库')",
            (po_no, supplier_id, warehouse_id, employee_id, today, today, amount))
        po_id = cur.lastrowid

        # ③ 采购明细（批号 / 生产日期 / 有效期）
        cur.execute(
            "INSERT INTO purchase_item (po_id, drug_id, quantity, unit_price, amount, "
            "batch_no, production_date, expiry_date) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (po_id, drug_id, qty, unit_price, amount, batch_no, prod_date, expiry_date))
        item_id = cur.lastrowid

        # ④ 入库批次（与采购明细 1-1）
        cur.execute(
            "INSERT INTO stock_batch (purchase_item_id, drug_id, warehouse_id, batch_no, "
            "production_date, expiry_date, quantity, purchase_price, in_date) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (item_id, drug_id, warehouse_id, batch_no, prod_date, expiry_date,
             qty, unit_price, today))

        # ⑤ 库存：有则加，无则建（先锁行，保证并发到货时不会互相覆盖）
        cur.execute("SELECT stock_id, quantity FROM stock "
                    "WHERE drug_id = %s AND warehouse_id = %s FOR UPDATE",
                    (drug_id, warehouse_id))
        row = cur.fetchone()
        if row:
            before = row["quantity"]
            cur.execute("UPDATE stock SET quantity = quantity + %s, update_time = NOW() "
                        "WHERE stock_id = %s", (qty, row["stock_id"]))
        else:
            before = 0
            cur.execute("INSERT INTO stock (drug_id, warehouse_id, quantity, safety_stock, "
                        "update_time) VALUES (%s, %s, %s, 50, NOW())",
                        (drug_id, warehouse_id, qty))
        after = before + qty

        # ⑥ 库存流水：正数入库
        cur.execute(
            "INSERT INTO stock_movement (drug_id, warehouse_id, move_type, ref_no, "
            "change_qty, before_qty, after_qty, batch_no, employee_id, move_time) "
            "VALUES (%s, %s, '采购入库', %s, %s, %s, %s, %s, %s, NOW())",
            (drug_id, warehouse_id, po_no, qty, before, after, batch_no, employee_id))

        return {
            "po_no": po_no, "batch_no": batch_no, "amount": amount,
            "drug_name": drug["drug_name"], "spec": drug["spec"], "unit": drug["unit"],
            "qty": qty, "stock_before": before, "stock_after": after,
        }


# =====================================================================
# T2  销售出库
# =====================================================================
def sell_out(warehouse_id, employee_id, drug_id, qty, customer_name, customer_type="连锁药店"):
    """
    销售出库：锁定库存 → 校验余额 → 按批次近效期先出扣减 → 出库单 + 明细 + 流水。
    """
    so_no = _no("SO")
    today = date.today()

    with transaction() as cur:
        cur.execute("SELECT drug_id, drug_name, spec, unit, retail_price "
                    "FROM drug WHERE drug_id = %s", (drug_id,))
        drug = cur.fetchone()
        if not drug:
            raise BizError("药品不存在")

        # ① 锁住该药品在该仓库的库存行
        cur.execute("SELECT stock_id, quantity FROM stock "
                    "WHERE drug_id = %s AND warehouse_id = %s FOR UPDATE",
                    (drug_id, warehouse_id))
        row = cur.fetchone()
        if not row:
            raise BizError("该仓库没有这种药品的库存，请先做采购入库")
        before = row["quantity"]
        if before < qty:
            raise BizError("库存不足：该仓库只剩 %d %s" % (before, drug["unit"] or ""))

        price = float(drug["retail_price"])
        amount = round(qty * price, 2)

        # ② 批次近效期先出：锁住仍在有效期内的批次，按到期日升序扣减；
        #    已过期批次不参与出库（等"效期预警"报表提示后做下架处理）
        cur.execute("SELECT batch_id, batch_no, quantity FROM stock_batch "
                    "WHERE drug_id = %s AND warehouse_id = %s AND quantity > 0 "
                    "AND expiry_date > CURDATE() "
                    "ORDER BY expiry_date, batch_id FOR UPDATE",
                    (drug_id, warehouse_id))
        batches = cur.fetchall()
        left = qty
        used_batch = None
        for b in batches:
            if left <= 0:
                break
            take = min(b["quantity"], left)
            cur.execute("UPDATE stock_batch SET quantity = quantity - %s WHERE batch_id = %s",
                        (take, b["batch_id"]))
            if used_batch is None:
                used_batch = b["batch_no"]
            left -= take
        batch_short = left > 0        # 批次账不足（人为改过库存时才会出现）

        # ③ 出库单 + 明细
        cur.execute(
            "INSERT INTO sale_order (so_no, customer_name, customer_type, warehouse_id, "
            "employee_id, sale_date, total_amount, status) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, '已出库')",
            (so_no, customer_name, customer_type, warehouse_id, employee_id, today, amount))
        so_id = cur.lastrowid
        cur.execute(
            "INSERT INTO sale_item (so_id, drug_id, quantity, unit_price, amount) "
            "VALUES (%s, %s, %s, %s, %s)", (so_id, drug_id, qty, price, amount))

        # ④ 扣减库存
        cur.execute("UPDATE stock SET quantity = quantity - %s, update_time = NOW() "
                    "WHERE stock_id = %s", (qty, row["stock_id"]))
        after = before - qty

        # ⑤ 库存流水：负数出库
        cur.execute(
            "INSERT INTO stock_movement (drug_id, warehouse_id, move_type, ref_no, "
            "change_qty, before_qty, after_qty, batch_no, employee_id, move_time) "
            "VALUES (%s, %s, '销售出库', %s, %s, %s, %s, %s, %s, NOW())",
            (drug_id, warehouse_id, so_no, -qty, before, after, used_batch, employee_id))

        return {
            "so_no": so_no, "amount": amount, "drug_name": drug["drug_name"],
            "spec": drug["spec"], "unit": drug["unit"], "qty": qty,
            "stock_before": before, "stock_after": after,
            "batch_no": used_batch, "batch_short": batch_short,
        }


# =====================================================================
# T2' 并发出库（多终端同时卖同一种药）
# =====================================================================
def sale_concurrency(drug_id, warehouse_id, clients=10, qty=1, reset_stock=None):
    """
    多客户端并发出库演示。

    · reset_stock 不为空时，演示前把该仓库库存改回这个数（同时对齐批次，保证账实一致）
    · 用 Barrier 让所有终端卡在同一刻发起，尽量制造真实的并发竞争
    · 逐个记录每个终端的响应耗时，演示结束后统一写入 access_log 表
      （日志放在业务事务之外写，避免日志插入反过来拖慢被测的操作）
    """
    if reset_stock is not None:
        with transaction() as cur:
            cur.execute("SELECT stock_id FROM stock WHERE drug_id = %s AND warehouse_id = %s "
                        "FOR UPDATE", (drug_id, warehouse_id))
            row = cur.fetchone()
            if row:
                cur.execute("UPDATE stock SET quantity = %s, update_time = NOW() "
                            "WHERE stock_id = %s", (reset_stock, row["stock_id"]))
            else:
                cur.execute("INSERT INTO stock (drug_id, warehouse_id, quantity, "
                            "safety_stock, update_time) VALUES (%s, %s, %s, 50, NOW())",
                            (drug_id, warehouse_id, reset_stock))
            # 批次与库存对齐：清空后把最近一个批次的数量设为重置值
            cur.execute("SELECT batch_id FROM stock_batch WHERE drug_id = %s "
                        "AND warehouse_id = %s ORDER BY expiry_date DESC, batch_id DESC LIMIT 1",
                        (drug_id, warehouse_id))
            b = cur.fetchone()
            if b:
                cur.execute("UPDATE stock_batch SET quantity = 0 "
                            "WHERE drug_id = %s AND warehouse_id = %s",
                            (drug_id, warehouse_id))
                cur.execute("UPDATE stock_batch SET quantity = %s WHERE batch_id = %s",
                            (reset_stock, b["batch_id"]))

    with get_cursor() as cur:
        cur.execute("SELECT d.drug_id, d.drug_name, d.spec, d.unit, "
                    "COALESCE(s.quantity, 0) AS stock, w.warehouse_name "
                    "FROM drug d "
                    "LEFT JOIN stock s ON s.drug_id = d.drug_id AND s.warehouse_id = %s "
                    "JOIN warehouse w ON w.warehouse_id = %s "
                    "WHERE d.drug_id = %s", (warehouse_id, warehouse_id, drug_id))
        drug = cur.fetchone()
        cur.execute("SELECT employee_id FROM employee WHERE position = '销售员' "
                    "AND warehouse_id = %s LIMIT 1", (warehouse_id,))
        emp = cur.fetchone()
    if not drug:
        raise BizError("药品不存在")
    if not emp:
        raise BizError("该仓库还没有销售员，无法出库")
    stock_before = drug["stock"]

    barrier = threading.Barrier(clients)
    lock = threading.Lock()
    detail = []

    def worker(idx):
        try:
            barrier.wait(timeout=10)
        except threading.BrokenBarrierError:
            pass
        t0 = time.perf_counter()
        ok, reason = True, ""
        try:
            sell_out(warehouse_id, emp["employee_id"], drug_id, qty,
                     "并发演示客户-%02d" % (idx + 1), "个人")
        except BizError as e:
            ok, reason = False, str(e)
        except Exception as e:                     # noqa: BLE001
            ok, reason = False, "系统异常：" + str(e)
        elapsed = (time.perf_counter() - t0) * 1000
        with lock:
            detail.append({"client": idx + 1, "ok": ok,
                           "elapsed_ms": round(elapsed, 2), "reason": reason})

    t_start = time.perf_counter()
    threads = [threading.Thread(target=worker, args=(i,)) for i in range(clients)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    elapsed_ms = (time.perf_counter() - t_start) * 1000

    with get_cursor() as cur:
        cur.execute("SELECT quantity FROM stock WHERE drug_id = %s AND warehouse_id = %s",
                    (drug_id, warehouse_id))
        r = cur.fetchone()
        stock_after = r["quantity"] if r else 0

    detail.sort(key=lambda x: x["client"])
    times = [d["elapsed_ms"] for d in detail]
    success = sum(1 for d in detail if d["ok"])
    failed = len(detail) - success

    dao.log_access_batch([("sale_out", d["client"], drug_id, qty,
                           1 if d["ok"] else 0, d["elapsed_ms"]) for d in detail])

    return {
        "drug": {"id": drug["drug_id"], "name": drug["drug_name"],
                 "spec": drug["spec"], "unit": drug["unit"]},
        "warehouse": drug["warehouse_name"],
        "clients": clients,
        "qty": qty,
        "stock_before": stock_before,
        "stock_after": stock_after,
        "success_count": success,
        "failed_count": failed,
        "consumed": stock_before - stock_after,
        "oversold": (stock_after < 0) or (success * qty != stock_before - stock_after),
        "elapsed_ms": round(elapsed_ms, 2),
        "avg_ms": round(statistics.mean(times), 2) if times else 0,
        "min_ms": round(min(times), 2) if times else 0,
        "max_ms": round(max(times), 2) if times else 0,
        "p95_ms": round(sorted(times)[int(len(times) * 0.95) - 1], 2) if times else 0,
        "qps": round(len(detail) / (elapsed_ms / 1000), 1) if elapsed_ms > 0 else 0,
        "detail": detail,
        "log_count": len(detail),
    }
