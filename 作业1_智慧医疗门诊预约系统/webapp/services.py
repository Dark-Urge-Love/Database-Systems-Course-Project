# -*- coding: utf-8 -*-
"""
Service 层：业务逻辑与事务
------------------------------------------------------------------
两个核心事务：

  T1  患者挂号  —— 扣减号源 + 生成预约，必须原子完成，不能超卖
  T2  药房发药  —— 锁定库存 + 校验 + 扣减，多个窗口同时发同一种药不能超发
  T2' 并发发药  —— 多线程（多客户端）同时发药，验证行锁不超发，并记录每次访问耗时

事务要点（报告里可以直接讲）：
  · SELECT ... FOR UPDATE 对目标行加排他锁，第二个并发事务会阻塞等待
  · 锁内重新校验 remain_slots / stock，把"检查—使用"放进同一个原子区间
  · 任何一步失败 → 整个事务 ROLLBACK，不会出现"扣了库存却没发药"
  · 数据库的 CHECK 约束（remain_slots >= 0、stock >= 0）是最后一道防线
"""
import statistics
import threading
import time

from . import dao
from .db import transaction, BizError, get_cursor


# =====================================================================
# T1  患者挂号
# =====================================================================
def register(patient_id, schedule_id):
    """
    挂号事务。返回 dict；业务规则不满足时抛 BizError（会被回滚）。
    """
    with transaction() as cur:
        # ① 锁定该排班行：并发时其他事务在此排队，保证不会同时读到同一个 remain_slots
        cur.execute(
            "SELECT schedule_id, doctor_id, work_date, period, total_slots, "
            "remain_slots, reg_fee FROM schedule WHERE schedule_id = %s FOR UPDATE",
            (schedule_id,),
        )
        sch = cur.fetchone()
        if not sch:
            raise BizError("排班不存在（schedule_id=%s）" % schedule_id)
        remain_before = sch["remain_slots"]
        if remain_before <= 0:
            raise BizError("该时段号源已挂完（剩余 0）")

        # ② 生成就诊顺序号
        cur.execute(
            "SELECT COALESCE(MAX(queue_no), 0) + 1 AS next_no "
            "FROM appointment WHERE schedule_id = %s",
            (schedule_id,),
        )
        queue_no = cur.fetchone()["next_no"]

        # ③ 插入预约记录
        cur.execute(
            "INSERT INTO appointment (patient_id, schedule_id, appt_time, queue_no, status) "
            "VALUES (%s, %s, NOW(), %s, '已预约')",
            (patient_id, schedule_id, queue_no),
        )
        appt_id = cur.lastrowid

        # ④ 扣减号源
        cur.execute(
            "UPDATE schedule SET remain_slots = remain_slots - 1 WHERE schedule_id = %s",
            (schedule_id,),
        )

        return {
            "appt_id": appt_id,
            "queue_no": queue_no,
            "patient_id": patient_id,
            "schedule_id": schedule_id,
            "doctor_name": sch["doctor_id"],
            "work_date": str(sch["work_date"]),
            "period": sch["period"],
            "reg_fee": float(sch["reg_fee"]),
            "remain_before": remain_before,
            "remain_after": remain_before - 1,
        }


# =====================================================================
# T2  药房发药：锁库存 → 校验 → 扣减（与挂号同构，但业务一句话就能讲清）
# =====================================================================
def dispense(drug_id, qty=1):
    """
    一个药房窗口发一次药。

    业务规则：库存够就扣，不够就当场拒绝（"库存不足，请等补货"），
    绝不出现发出去的药比库存还多的情况。
    """
    with transaction() as cur:
        # ① 锁住这一行药品：并发的其他窗口在这里排队，拿到锁才继续
        cur.execute(
            "SELECT drug_id, drug_name, spec, unit, stock FROM drug "
            "WHERE drug_id = %s FOR UPDATE", (drug_id,))
        row = cur.fetchone()
        if not row:
            raise BizError("药品不存在")
        before = row["stock"]
        if before < qty:
            raise BizError("库存不足，只剩 %d %s" % (before, row["unit"] or ""))
        # ② 扣减库存（CHECK stock >= 0 是最后一道防线）
        cur.execute("UPDATE drug SET stock = stock - %s WHERE drug_id = %s", (qty, drug_id))
        return {
            "drug_id": drug_id,
            "drug_name": row["drug_name"],
            "spec": row["spec"],
            "unit": row["unit"],
            "qty": qty,
            "stock_before": before,
            "stock_after": before - qty,
        }


def dispense_concurrency(drug_id, clients=10, qty=1, reset_stock=None):
    """
    多客户端并发发药演示：clients 个窗口同时给病人发同一种药。

    · reset_stock 不为空时，演示前把库存改回这个数，保证每次演示都可复现
    · 用 Barrier 让所有线程卡在同一刻发起，尽量制造真实的并发竞争
    · 逐个记录每个客户端的响应耗时，演示结束后统一写入 access_log 表
      （日志放在业务事务之外写，避免日志插入反过来拖慢被测的操作）
    """
    if reset_stock is not None:
        with transaction() as cur:
            cur.execute("UPDATE drug SET stock = %s WHERE drug_id = %s",
                        (int(reset_stock), drug_id))

    with get_cursor() as cur:
        cur.execute("SELECT drug_id, drug_name, spec, unit, stock FROM drug WHERE drug_id = %s",
                    (drug_id,))
        drug = cur.fetchone()
    if not drug:
        raise BizError("药品不存在")
    stock_before = drug["stock"]

    barrier = threading.Barrier(clients)
    lock = threading.Lock()
    detail = []

    def worker(idx):
        try:
            barrier.wait(timeout=10)               # 让大家同一起跑线
        except threading.BrokenBarrierError:
            pass
        t0 = time.perf_counter()
        ok, reason = True, ""
        try:
            dispense(drug_id, qty)
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
        cur.execute("SELECT stock FROM drug WHERE drug_id = %s", (drug_id,))
        stock_after = cur.fetchone()["stock"]

    detail.sort(key=lambda x: x["client"])
    times = [d["elapsed_ms"] for d in detail]
    success = sum(1 for d in detail if d["ok"])
    failed = len(detail) - success

    # 逐条写日志：谁、什么时候、发了几盒、成功没有、花了多少毫秒
    dao.log_access_batch([("dispense", d["client"], drug_id, qty,
                           1 if d["ok"] else 0, d["elapsed_ms"]) for d in detail])

    return {
        "drug": {"id": drug["drug_id"], "name": drug["drug_name"],
                 "spec": drug["spec"], "unit": drug["unit"]},
        "clients": clients,
        "qty": qty,
        "stock_before": stock_before,
        "stock_after": stock_after,
        "success_count": success,
        "failed_count": failed,
        "consumed": stock_before - stock_after,
        # 每个成功窗口消耗 qty 盒，所以按"成功次数 × 每次数量"核对
        "oversold": (stock_after < 0) or (success * qty != stock_before - stock_after),
        "elapsed_ms": round(elapsed_ms, 2),
        "avg_ms": round(statistics.mean(times), 2) if times else 0,
        "min_ms": round(min(times), 2) if times else 0,
        "max_ms": round(max(times), 2) if times else 0,
        "qps": round(len(detail) / (elapsed_ms / 1000), 1) if elapsed_ms > 0 else 0,
        "detail": detail,
        "log_count": len(detail),
    }
