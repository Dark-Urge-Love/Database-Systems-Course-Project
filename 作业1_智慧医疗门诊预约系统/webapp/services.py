# -*- coding: utf-8 -*-
"""
Service 层：业务逻辑与事务
------------------------------------------------------------------
本文件是报告里"标志性实践 2（挂号事务与号源并发控制）"的核心代码。

两个关键事务：
  T1  患者挂号        —— 扣减号源 + 生成预约，必须原子完成，不能超卖
  T2  医生接诊开方    —— 生成就诊记录(1-1) + 处方 + 明细(N-M) + 扣药品库存 + 缴费

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


def cancel_appointment(appt_id):
    """取消预约：把号源还回去（同样是事务）。"""
    with transaction() as cur:
        cur.execute(
            "SELECT appt_id, schedule_id, status FROM appointment "
            "WHERE appt_id = %s FOR UPDATE",
            (appt_id,),
        )
        ap = cur.fetchone()
        if not ap:
            raise BizError("预约不存在")
        if ap["status"] == "已取消":
            raise BizError("该预约已经是取消状态")
        if ap["status"] == "已就诊":
            raise BizError("已就诊的预约不能取消")
        cur.execute("UPDATE appointment SET status = '已取消' WHERE appt_id = %s", (appt_id,))
        cur.execute(
            "UPDATE schedule SET remain_slots = remain_slots + 1 WHERE schedule_id = %s",
            (ap["schedule_id"],),
        )
        return {"appt_id": appt_id, "schedule_id": ap["schedule_id"]}


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


# =====================================================================
# T2  医生接诊 + 开处方
# =====================================================================
def create_visit(appt_id, chief_complaint="", diagnosis="", advice=""):
    """接诊：把「已预约」的预约变成「已就诊」，同时生成 1-1 的就诊记录。"""
    with transaction() as cur:
        cur.execute(
            "SELECT a.appt_id, a.patient_id, a.status, a.schedule_id, s.doctor_id, s.reg_fee "
            "FROM appointment a JOIN schedule s ON a.schedule_id = s.schedule_id "
            "WHERE a.appt_id = %s FOR UPDATE",
            (appt_id,),
        )
        ap = cur.fetchone()
        if not ap:
            raise BizError("预约不存在")
        if ap["status"] != "已预约":
            raise BizError("该预约当前状态为「%s」，只有「已预约」的才能接诊" % ap["status"])

        # 1-1 联系：一个预约最多一条就诊记录（表上有 UNIQUE(appt_id) 兜底）
        cur.execute("SELECT visit_id FROM visit WHERE appt_id = %s", (appt_id,))
        if cur.fetchone():
            raise BizError("该预约已存在就诊记录（1-1 约束）")

        cur.execute(
            "INSERT INTO visit (appt_id, patient_id, doctor_id, visit_time, "
            "chief_complaint, diagnosis, advice) VALUES (%s, %s, %s, NOW(), %s, %s, %s)",
            (appt_id, ap["patient_id"], ap["doctor_id"],
             chief_complaint or "发热咳嗽 3 天", diagnosis or "上呼吸道感染", advice or "多饮水，注意休息"),
        )
        visit_id = cur.lastrowid

        cur.execute("UPDATE appointment SET status = '已就诊' WHERE appt_id = %s", (appt_id,))

        # 挂号费缴费流水
        cur.execute(
            "INSERT INTO payment (visit_id, fee_type, amount, pay_method, status) "
            "VALUES (%s, '挂号费', %s, '微信', '已支付')",
            (visit_id, ap["reg_fee"]),
        )
        return {"visit_id": visit_id, "appt_id": appt_id, "appt_id_val": ap["appt_id"],
                "reg_fee": float(ap["reg_fee"]), "doctor_id": ap["doctor_id"]}


def prescribe(visit_id, items, presc_type="西药", pay_method="微信"):
    """
    开处方事务：处方表 + 明细表 + 扣药品库存 + 药费缴费，四步一体。
    items: [(drug_id, quantity), ...]
    """
    if not items:
        raise BizError("请至少选择一种药品")

    # 合并同一种药品，并按 drug_id 排序，避免并发开方时出现循环等待死锁
    merged = {}
    for drug_id, qty in items:
        merged[int(drug_id)] = merged.get(int(drug_id), 0) + int(qty)

    with transaction() as cur:
        cur.execute(
            "SELECT visit_id, patient_id, doctor_id FROM visit WHERE visit_id = %s FOR UPDATE",
            (visit_id,),
        )
        if not cur.fetchone():
            raise BizError("就诊记录不存在（visit_id=%s）" % visit_id)

        cur.execute(
            "INSERT INTO prescription (visit_id, presc_time, presc_type, status) "
            "VALUES (%s, NOW(), %s, '已缴费')",
            (visit_id, presc_type),
        )
        presc_id = cur.lastrowid

        total = 0.0
        detail = []
        for drug_id in sorted(merged):
            qty = merged[drug_id]
            cur.execute(
                "SELECT drug_name, spec, price, stock FROM drug WHERE drug_id = %s FOR UPDATE",
                (drug_id,),
            )
            dr = cur.fetchone()
            if not dr:
                raise BizError("药品不存在（drug_id=%s）" % drug_id)
            if dr["stock"] < qty:
                raise BizError(
                    "药品「%s」库存不足：现有 %d，需要 %d —— 事务已整体回滚"
                    % (dr["drug_name"], dr["stock"], qty)
                )
            cur.execute(
                "INSERT INTO prescription_item (presc_id, drug_id, quantity, usage_note, unit_price) "
                "VALUES (%s, %s, %s, %s, %s)",
                (presc_id, drug_id, qty, "遵医嘱", dr["price"]),
            )
            cur.execute("UPDATE drug SET stock = stock - %s WHERE drug_id = %s", (qty, drug_id))
            subtotal = float(dr["price"]) * qty
            total += subtotal
            detail.append({"drug_id": drug_id, "drug_name": dr["drug_name"],
                           "spec": dr["spec"], "quantity": qty,
                           "unit_price": float(dr["price"]), "subtotal": round(subtotal, 2)})

        cur.execute(
            "INSERT INTO payment (visit_id, fee_type, amount, pay_method, status) "
            "VALUES (%s, '药费', %s, %s, '已支付')",
            (visit_id, round(total, 2), pay_method),
        )
        return {"presc_id": presc_id, "visit_id": visit_id, "items": detail,
                "total": round(total, 2), "pay_method": pay_method}
