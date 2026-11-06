# -*- coding: utf-8 -*-
"""
DAO 层：所有 SQL 语句集中在这里
------------------------------------------------------------------
分层：views(页面) → services(业务事务) → dao(SQL) → db(连接)
好处：SQL 与页面解耦，报告里可以直接引用本文件作为"实现代码"。
"""
from .db import get_cursor


# =====================================================================
# 通用查询封装
# =====================================================================
def query_all(sql, args=None):
    with get_cursor() as cur:
        cur.execute(sql, args or ())
        return cur.fetchall()


def query_one(sql, args=None):
    with get_cursor() as cur:
        cur.execute(sql, args or ())
        return cur.fetchone()


def execute(sql, args=None):
    """执行单条写语句，返回受影响行数。"""
    with get_cursor(commit=True) as cur:
        cur.execute(sql, args or ())
        return cur.rowcount


# =====================================================================
# 概览 / 统计
# =====================================================================
TABLES = [
    "department", "doctor", "patient", "schedule", "appointment",
    "visit", "prescription", "drug", "prescription_item", "payment",
]

TABLE_CN = {
    "department": "科室", "doctor": "医生", "patient": "患者", "schedule": "排班",
    "appointment": "预约挂号", "visit": "就诊记录", "prescription": "处方",
    "drug": "药品", "prescription_item": "处方明细", "payment": "缴费记录",
}


def table_row_counts():
    """逐表精确行数（不用 information_schema 的估算值）。"""
    rows = []
    total = 0
    for t in TABLES:
        n = query_one("SELECT COUNT(*) AS c FROM `%s`" % t)["c"]
        rows.append({"table": t, "cn": TABLE_CN.get(t, t), "rows": n})
        total += n
    return rows, total


def server_info():
    """取服务器版本 / 当前连接身份，用于证明"连的是远程 Linux 服务器"。"""
    return query_one(
        "SELECT VERSION() AS version, CURRENT_USER() AS current_user, "
        "@@hostname AS hostname, @@port AS port, @@datadir AS datadir"
    )


def appointment_status_stats():
    return query_all(
        "SELECT status, COUNT(*) AS cnt FROM appointment GROUP BY status ORDER BY cnt DESC"
    )


# =====================================================================
# 挂号相关
# =====================================================================
def list_departments():
    return query_all(
        "SELECT dept_id, dept_name, dept_type, location FROM department "
        "WHERE status = 1 ORDER BY dept_id"
    )


def list_doctors(dept_id=None):
    sql = ("SELECT d.doctor_id, d.doctor_name, d.title, d.dept_id, dp.dept_name "
           "FROM doctor d JOIN department dp ON d.dept_id = dp.dept_id "
           "WHERE d.status = 1 ")
    args = []
    if dept_id:
        sql += "AND d.dept_id = %s "
        args.append(dept_id)
    sql += "ORDER BY d.doctor_id LIMIT 200"
    return query_all(sql, args)


def list_schedules(doctor_id, only_available=True, limit=60):
    sql = ("SELECT s.schedule_id, s.work_date, s.period, s.total_slots, "
           "s.remain_slots, s.reg_fee, s.room, d.doctor_name, dp.dept_name "
           "FROM schedule s "
           "JOIN doctor d ON s.doctor_id = d.doctor_id "
           "JOIN department dp ON d.dept_id = dp.dept_id "
           "WHERE s.doctor_id = %s ")
    if only_available:
        sql += "AND s.remain_slots > 0 "
    sql += "ORDER BY s.work_date DESC, s.period LIMIT %s"
    return query_all(sql, [doctor_id, limit])


def get_schedule(schedule_id):
    return query_one(
        "SELECT s.*, d.doctor_name, d.dept_id, dp.dept_name "
        "FROM schedule s JOIN doctor d ON s.doctor_id = d.doctor_id "
        "JOIN department dp ON d.dept_id = dp.dept_id "
        "WHERE s.schedule_id = %s",
        [schedule_id],
    )


def list_patients(keyword=None, limit=50):
    if keyword:
        return query_all(
            "SELECT patient_id, patient_name, gender, phone, medical_card_no "
            "FROM patient WHERE patient_name LIKE %s OR phone LIKE %s "
            "ORDER BY patient_id LIMIT %s",
            ["%" + keyword + "%", "%" + keyword + "%", limit],
        )
    return query_all(
        "SELECT patient_id, patient_name, gender, phone, medical_card_no "
        "FROM patient ORDER BY patient_id LIMIT %s",
        [limit],
    )


def list_appointments(status=None, limit=50):
    sql = ("SELECT a.appt_id, a.patient_id, p.patient_name, a.schedule_id, "
           "a.appt_time, a.queue_no, a.status, s.work_date, s.period, s.reg_fee, "
           "d.doctor_id, d.doctor_name, dp.dept_name "
           "FROM appointment a "
           "JOIN patient p ON a.patient_id = p.patient_id "
           "JOIN schedule s ON a.schedule_id = s.schedule_id "
           "JOIN doctor d ON s.doctor_id = d.doctor_id "
           "JOIN department dp ON d.dept_id = dp.dept_id ")
    args = []
    if status:
        sql += "WHERE a.status = %s "
        args.append(status)
    sql += "ORDER BY a.appt_id DESC LIMIT %s"
    args.append(limit)
    return query_all(sql, args)


def get_appointment(appt_id):
    return query_one(
        "SELECT a.appt_id, a.patient_id, p.patient_name, p.gender, a.schedule_id, "
        "a.appt_time, a.queue_no, a.status, s.work_date, s.period, s.reg_fee, "
        "s.doctor_id, d.doctor_name, dp.dept_name, v.visit_id "
        "FROM appointment a "
        "JOIN patient p ON a.patient_id = p.patient_id "
        "JOIN schedule s ON a.schedule_id = s.schedule_id "
        "JOIN doctor d ON s.doctor_id = d.doctor_id "
        "JOIN department dp ON d.dept_id = dp.dept_id "
        "LEFT JOIN visit v ON v.appt_id = a.appt_id "
        "WHERE a.appt_id = %s",
        [appt_id],
    )


# =====================================================================
# 药品 / 就诊 / 处方
# =====================================================================
def list_drugs(keyword=None, limit=50):
    if keyword:
        return query_all(
            "SELECT drug_id, drug_name, spec, unit, price, stock, is_rx "
            "FROM drug WHERE drug_name LIKE %s AND status = 1 "
            "ORDER BY drug_id LIMIT %s",
            ["%" + keyword + "%", limit],
        )
    return query_all(
        "SELECT drug_id, drug_name, spec, unit, price, stock, is_rx "
        "FROM drug WHERE status = 1 ORDER BY drug_id LIMIT %s",
        [limit],
    )


def get_visit_detail(visit_id):
    visit = query_one(
        "SELECT v.*, p.patient_name, p.gender, p.phone, d.doctor_name, dp.dept_name "
        "FROM visit v JOIN patient p ON v.patient_id = p.patient_id "
        "JOIN doctor d ON v.doctor_id = d.doctor_id "
        "JOIN department dp ON d.dept_id = dp.dept_id "
        "WHERE v.visit_id = %s",
        [visit_id],
    )
    if not visit:
        return None, [], []
    items = query_all(
        "SELECT i.item_id, i.drug_id, dr.drug_name, dr.spec, i.quantity, "
        "i.unit_price, (i.quantity * i.unit_price) AS subtotal, i.usage_note "
        "FROM prescription_item i "
        "JOIN prescription pr ON i.presc_id = pr.presc_id "
        "JOIN drug dr ON i.drug_id = dr.drug_id "
        "WHERE pr.visit_id = %s ORDER BY i.item_id",
        [visit_id],
    )
    pays = query_all(
        "SELECT pay_id, fee_type, amount, pay_method, pay_time, status "
        "FROM payment WHERE visit_id = %s ORDER BY pay_id",
        [visit_id],
    )
    return visit, items, pays


# =====================================================================
# 标志性实践 5：跨表复杂查询（≥3 表 join）
# =====================================================================
def report_dept_visit_rank(limit=10):
    """科室就诊量排行：visit + doctor + department（3 表）"""
    return query_all(
        "SELECT dp.dept_name AS 科室, COUNT(*) AS 就诊量, "
        "COUNT(DISTINCT v.doctor_id) AS 接诊医生数 "
        "FROM visit v "
        "JOIN doctor d ON v.doctor_id = d.doctor_id "
        "JOIN department dp ON d.dept_id = dp.dept_id "
        "GROUP BY dp.dept_name ORDER BY 就诊量 DESC LIMIT %s",
        [limit],
    )


def report_doctor_revenue(limit=10):
    """医生开药金额 Top10：visit + doctor + prescription + prescription_item（4 表）"""
    return query_all(
        "SELECT d.doctor_name AS 医生, dp.dept_name AS 科室, "
        "COUNT(DISTINCT pr.presc_id) AS 处方数, "
        "ROUND(SUM(i.quantity * i.unit_price), 2) AS 开药总额 "
        "FROM prescription_item i "
        "JOIN prescription pr ON i.presc_id = pr.presc_id "
        "JOIN visit v ON pr.visit_id = v.visit_id "
        "JOIN doctor d ON v.doctor_id = d.doctor_id "
        "JOIN department dp ON d.dept_id = dp.dept_id "
        "GROUP BY d.doctor_id, d.doctor_name, dp.dept_name "
        "ORDER BY 开药总额 DESC LIMIT %s",
        [limit],
    )


def report_drug_consumption(limit=10):
    """药品消耗统计：prescription_item + drug + prescription（3 表）"""
    return query_all(
        "SELECT dr.drug_name AS 药品, dr.spec AS 规格, "
        "SUM(i.quantity) AS 总消耗量, "
        "ROUND(SUM(i.quantity * i.unit_price), 2) AS 总销售额 "
        "FROM prescription_item i "
        "JOIN drug dr ON i.drug_id = dr.drug_id "
        "JOIN prescription pr ON i.presc_id = pr.presc_id "
        "GROUP BY dr.drug_id, dr.drug_name, dr.spec "
        "ORDER BY 总消耗量 DESC LIMIT %s",
        [limit],
    )


def report_patient_flow(patient_id, limit=20):
    """单个患者的完整就诊流水：patient + appointment + visit + prescription
       + prescription_item + drug + doctor（7 表 join）"""
    return query_all(
        "SELECT a.appt_id, a.appt_time, a.status AS 预约状态, "
        "d.doctor_name AS 医生, dp.dept_name AS 科室, "
        "v.visit_id, v.diagnosis AS 诊断, "
        "dr.drug_name AS 药品, i.quantity AS 数量, "
        "ROUND(i.quantity * i.unit_price, 2) AS 金额 "
        "FROM appointment a "
        "JOIN patient p ON a.patient_id = p.patient_id "
        "JOIN schedule s ON a.schedule_id = s.schedule_id "
        "JOIN doctor d ON s.doctor_id = d.doctor_id "
        "JOIN department dp ON d.dept_id = dp.dept_id "
        "LEFT JOIN visit v ON v.appt_id = a.appt_id "
        "LEFT JOIN prescription pr ON pr.visit_id = v.visit_id "
        "LEFT JOIN prescription_item i ON i.presc_id = pr.presc_id "
        "LEFT JOIN drug dr ON i.drug_id = dr.drug_id "
        "WHERE a.patient_id = %s "
        "ORDER BY a.appt_time DESC, i.item_id LIMIT %s",
        [patient_id, limit],
    )
