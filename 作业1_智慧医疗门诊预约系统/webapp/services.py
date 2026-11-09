# -*- coding: utf-8 -*-
"""
Service 层：业务逻辑与事务
------------------------------------------------------------------
本文件是报告里"标志性实践 2（挂号事务与号源并发控制）"的核心代码。

核心事务：
  T1  患者挂号  —— 扣减号源 + 生成预约，必须原子完成，不能超卖
  T1' 并发抢号  —— 多线程同时挂号，验证 FOR UPDATE 行锁不会超卖

事务要点（报告里可以直接讲）：
  · SELECT ... FOR UPDATE 对目标行加排他锁，第二个并发事务会阻塞等待
  · 锁内重新校验 remain_slots / stock，把"检查—使用"放进同一个原子区间
  · 任何一步失败 → 整个事务 ROLLBACK，不会出现"扣了号源却没生成预约"
  · 数据库的 CHECK 约束（remain_slots >= 0、stock >= 0）是最后一道防线
"""
import threading

from .db import transaction, BizError


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


def concurrency_register_demo(schedule_id, thread_count=10, isolation=None):
    """
    并发抢号演示（标志性实践 2 的"超卖对比"证据）。

    用 thread_count 个线程 + Barrier 让它们尽量同时发起挂号，
    统计成功/失败数量，并与排班剩余号源核对，证明"没有超卖"。
    """
    with transaction() as cur:
        cur.execute(
            "SELECT remain_slots, total_slots, doctor_id FROM schedule "
            "WHERE schedule_id = %s",
            (schedule_id,),
        )
        sch = cur.fetchone()
    if not sch:
        raise BizError("排班不存在")
    remain_before = sch["remain_slots"]

    barrier = threading.Barrier(thread_count)
    lock = threading.Lock()
    results = {"success": [], "failed": []}

    def worker(idx):
        patient_id = 1 + (idx * 137) % 5000        # 固定可复现
        try:
            barrier.wait(timeout=10)               # 让大家同一起跑线
        except threading.BrokenBarrierError:
            pass
        try:
            info = register(patient_id, schedule_id)
            with lock:
                results["success"].append({"thread": idx, "appt_id": info["appt_id"],
                                           "queue_no": info["queue_no"]})
        except BizError as e:
            with lock:
                results["failed"].append({"thread": idx, "reason": str(e)})
        except Exception as e:                     # noqa: BLE001
            with lock:
                results["failed"].append({"thread": idx, "reason": "系统异常：" + str(e)})

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(thread_count)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    with transaction() as cur:
        cur.execute("SELECT remain_slots FROM schedule WHERE schedule_id = %s", (schedule_id,))
        remain_after = cur.fetchone()["remain_slots"]

    expect_success = min(thread_count, remain_before)
    return {
        "schedule_id": schedule_id,
        "thread_count": thread_count,
        "remain_before": remain_before,
        "remain_after": remain_after,
        "success_count": len(results["success"]),
        "failed_count": len(results["failed"]),
        "consumed": remain_before - remain_after,
        "expect_success": expect_success,
        "oversold": (remain_after < 0) or (len(results["success"]) != remain_before - remain_after),
        "success_detail": results["success"][:20],
        "failed_detail": results["failed"][:20],
    }
