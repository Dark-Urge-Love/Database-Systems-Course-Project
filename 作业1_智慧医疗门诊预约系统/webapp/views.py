# -*- coding: utf-8 -*-
"""
Views 层：页面路由与 Ajax 接口
------------------------------------------------------------------
对应课程要求的 B/S 结构：浏览器（客户端）→ Flask（本机）→ 通过 TCP/IP
访问 Linux 服务器上的 MySQL。页面顶部固定显示当前连接的目标服务器地址，
方便答辩时直接证明"数据不在本机"。
"""
import time
import traceback

import pymysql
from flask import Blueprint, jsonify, render_template, request

from . import config, dao, services
from .db import BizError, get_cursor, transaction

bp = Blueprint("main", __name__)


# =====================================================================
# 公共：把当前连接信息塞给所有模板
# =====================================================================
def connection_banner():
    try:
        info = dao.server_info()
        return {
            "ok": True,
            "host": config.DB_HOST,
            "port": config.DB_PORT,
            "user": config.DB_USER,
            "db": config.DB_NAME,
            "version": info["version"],
            "hostname": info["hostname"],
            "datadir": info["datadir"],
        }
    except Exception as e:                                     # noqa: BLE001
        return {"ok": False, "host": config.DB_HOST, "port": config.DB_PORT,
                "user": config.DB_USER, "db": config.DB_NAME, "error": str(e)}


@bp.app_context_processor
def inject_globals():
    return {"APP_TITLE": config.APP_TITLE, "conn": connection_banner()}


def _ok(data=None, **extra):
    payload = {"ok": True}
    if data is not None:
        payload["data"] = data
    payload.update(extra)
    return jsonify(payload)


def _err(message, **extra):
    payload = {"ok": False, "error": message}
    payload.update(extra)
    return jsonify(payload), 200


def _is_api():
    return request.path.startswith("/api/")


@bp.errorhandler(pymysql.MySQLError)
def handle_mysql_error(e):                                     # noqa: ANN001
    msg = "数据库错误（%s）：%s" % (e.args[0] if e.args else "?", str(e))
    if _is_api():
        return _err(msg)
    return render_template("error.html", message=msg), 500


@bp.errorhandler(Exception)
def handle_any_error(e):                                       # noqa: ANN001
    """页面出错时给一个能自助排查的页面，而不是 Flask 默认的报错页。"""
    from werkzeug.exceptions import HTTPException
    if isinstance(e, HTTPException):
        return e
    tb = traceback.format_exc()
    if _is_api():
        return _err("服务器内部错误：%s" % str(e), trace=tb.splitlines()[-4:])
    return render_template("error.html", message="%s: %s" % (type(e).__name__, e)), 500


# =====================================================================
# 首页：仪表盘
# =====================================================================
@bp.route("/")
def dashboard():
    try:
        rows, total = dao.table_row_counts()
        status_stats = dao.appointment_status_stats()
        dept_rank = dao.report_dept_visit_rank(6)
        drug_top = dao.report_drug_consumption(6)
        db_ok, db_err = True, None
    except Exception as e:                                     # noqa: BLE001
        rows, total, status_stats, dept_rank, drug_top = [], 0, [], [], []
        db_ok, db_err = False, str(e)
    return render_template("dashboard.html", rows=rows, total=total,
                           status_stats=status_stats, dept_rank=dept_rank,
                           drug_top=drug_top, db_ok=db_ok, db_err=db_err)


# =====================================================================
# 患者端：挂号（T1 事务）
# =====================================================================
@bp.route("/register")
def register_page():
    departments = dao.list_departments()
    return render_template("register.html", departments=departments)


@bp.route("/api/doctors")
def api_doctors():
    dept_id = request.args.get("dept_id", type=int)
    return _ok(dao.list_doctors(dept_id))


@bp.route("/api/schedules")
def api_schedules():
    doctor_id = request.args.get("doctor_id", type=int)
    if not doctor_id:
        return _err("缺少 doctor_id")
    only_available = request.args.get("all") != "1"
    return _ok(dao.list_schedules(doctor_id, only_available=only_available))


@bp.route("/api/patients")
def api_patients():
    return _ok(dao.list_patients(request.args.get("q"), limit=30))


@bp.route("/api/register", methods=["POST"])
def api_register():
    body = request.get_json(silent=True) or {}
    try:
        patient_id = int(body.get("patient_id") or 0)
        schedule_id = int(body.get("schedule_id") or 0)
    except (TypeError, ValueError):
        return _err("参数格式不正确")
    if not patient_id or not schedule_id:
        return _err("请先选择患者和排班")
    t0 = time.time()
    try:
        info = services.register(patient_id, schedule_id)
        info["elapsed_ms"] = round((time.time() - t0) * 1000, 2)
        info["sql"] = (
            "START TRANSACTION;\n"
            "SELECT remain_slots FROM schedule WHERE schedule_id=%s FOR UPDATE;\n"
            "INSERT INTO appointment(patient_id, schedule_id, appt_time, queue_no, status) "
            "VALUES (%s, %s, NOW(), %s, '已预约');\n"
            "UPDATE schedule SET remain_slots = remain_slots - 1 WHERE schedule_id=%s;\n"
            "COMMIT;" % (schedule_id, patient_id, info["queue_no"], schedule_id)
        )
        return _ok(info)
    except BizError as e:
        return _err(str(e))
    except pymysql.MySQLError as e:
        return _err("数据库错误：%s" % str(e))


@bp.route("/api/cancel", methods=["POST"])
def api_cancel():
    body = request.get_json(silent=True) or {}
    try:
        appt_id = int(body.get("appt_id") or 0)
    except (TypeError, ValueError):
        return _err("参数格式不正确")
    try:
        return _ok(services.cancel_appointment(appt_id))
    except BizError as e:
        return _err(str(e))


@bp.route("/api/concurrency_demo", methods=["POST"])
def api_concurrency_demo():
    """多线程并发抢号，证明不会超卖（标志性实践 2）。"""
    body = request.get_json(silent=True) or {}
    try:
        schedule_id = int(body.get("schedule_id") or 0)
        thread_count = min(int(body.get("thread_count") or 10), 30)
    except (TypeError, ValueError):
        return _err("参数格式不正确")
    if not schedule_id:
        return _err("请选择一个排班")
    t0 = time.time()
    try:
        result = services.concurrency_register_demo(schedule_id, thread_count)
        result["elapsed_ms"] = round((time.time() - t0) * 1000, 2)
        return _ok(result)
    except BizError as e:
        return _err(str(e))


@bp.route("/api/appointments")
def api_appointments():
    status = request.args.get("status") or None
    return _ok(dao.list_appointments(status, limit=request.args.get("limit", 30, type=int)))


# =====================================================================
# 医生端：接诊 + 开方（T2 事务）
# =====================================================================
@bp.route("/clinic")
def clinic_page():
    visit_id = request.args.get("visit_id", type=int)
    pending = dao.list_appointments("已预约", limit=30)
    visit = items = pays = None
    if visit_id:
        visit, items, pays = dao.get_visit_detail(visit_id)
    return render_template("clinic.html", pending=pending, visit=visit,
                           items=items or [], pays=pays or [], visit_id=visit_id)


@bp.route("/api/appointment_detail")
def api_appointment_detail():
    appt_id = request.args.get("appt_id", type=int)
    if not appt_id:
        return _err("缺少 appt_id")
    info = dao.get_appointment(appt_id)
    if not info:
        return _err("预约不存在")
    return _ok(info)


@bp.route("/api/visit", methods=["POST"])
def api_visit():
    body = request.get_json(silent=True) or {}
    try:
        appt_id = int(body.get("appt_id") or 0)
    except (TypeError, ValueError):
        return _err("参数格式不正确")
    t0 = time.time()
    try:
        info = services.create_visit(
            appt_id,
            chief_complaint=body.get("chief_complaint") or "",
            diagnosis=body.get("diagnosis") or "",
            advice=body.get("advice") or "",
        )
        info["elapsed_ms"] = round((time.time() - t0) * 1000, 2)
        return _ok(info)
    except BizError as e:
        return _err(str(e))
    except pymysql.MySQLError as e:
        return _err("数据库错误：%s" % str(e))


@bp.route("/api/drugs")
def api_drugs():
    return _ok(dao.list_drugs(request.args.get("q"), limit=40))


@bp.route("/api/prescribe", methods=["POST"])
def api_prescribe():
    body = request.get_json(silent=True) or {}
    try:
        visit_id = int(body.get("visit_id") or 0)
    except (TypeError, ValueError):
        return _err("参数格式不正确")
    raw_items = body.get("items") or []
    items = []
    for it in raw_items:
        try:
            items.append((int(it.get("drug_id")), int(it.get("quantity") or 1)))
        except (TypeError, ValueError):
            return _err("药品参数格式不正确")
    t0 = time.time()
    try:
        info = services.prescribe(visit_id, items,
                                  presc_type=body.get("presc_type") or "西药",
                                  pay_method=body.get("pay_method") or "微信")
        info["elapsed_ms"] = round((time.time() - t0) * 1000, 2)
        info["sql"] = (
            "START TRANSACTION;\n"
            "SELECT * FROM visit WHERE visit_id=%s FOR UPDATE;\n"
            "INSERT INTO prescription(visit_id, presc_time, presc_type, status) VALUES (...);\n"
            " -- 对每一种药品：\n"
            "SELECT price, stock FROM drug WHERE drug_id=%s FOR UPDATE;   -- 校验库存\n"
            "INSERT INTO prescription_item(presc_id, drug_id, quantity, usage_note, unit_price) ...;\n"
            "UPDATE drug SET stock = stock - %s WHERE drug_id=%s;\n"
            "INSERT INTO payment(visit_id, fee_type, amount, pay_method, status) VALUES (..., '药费', ...);\n"
            "COMMIT;" % visit_id
        )
        return _ok(info)
    except BizError as e:
        return _err(str(e))
    except pymysql.MySQLError as e:
        return _err("数据库错误：%s" % str(e))


# =====================================================================
# 基本操作台：建表 / 插入 / 修改 / 删除 / 删表 / join
# =====================================================================
OPS = {
    "create": {
        "title": "① 建表 CREATE TABLE",
        "desc": "创建一张药品归档表（结构与 drug 一致，另加归档时间与原因）",
        "sql": [
            "DROP TABLE IF EXISTS drug_archive",
            """CREATE TABLE drug_archive (
                 archive_id    INT AUTO_INCREMENT PRIMARY KEY,
                 drug_id       INT NOT NULL,
                 drug_name     VARCHAR(100) NOT NULL,
                 spec          VARCHAR(50),
                 price         DECIMAL(8,2) NOT NULL,
                 stock         INT NOT NULL,
                 archive_time  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                 archive_reason VARCHAR(50)
               ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='药品归档表（演示建表）'""",
        ],
        "show": "SHOW COLUMNS FROM drug_archive",
    },
    "insert": {
        "title": "② 插入 INSERT",
        "desc": "把库存少于 800 的药品批量归档写入 drug_archive",
        "sql": [
            "INSERT INTO drug_archive (drug_id, drug_name, spec, price, stock, archive_reason) "
            "SELECT drug_id, drug_name, spec, price, stock, '低库存' "
            "FROM drug WHERE stock < 800 ORDER BY drug_id LIMIT 20"
        ],
        "show": "SELECT archive_id, drug_id, drug_name, spec, price, stock, archive_reason "
                "FROM drug_archive ORDER BY archive_id LIMIT 20",
        "precheck": "SELECT 1 FROM information_schema.tables "
                    "WHERE table_schema=DATABASE() AND table_name='drug_archive'",
    },
    "update": {
        "title": "③ 修改 UPDATE",
        "desc": "把归档表中单价低于 30 元的记录，归档原因改为「低价药」",
        "sql": [
            "UPDATE drug_archive SET archive_reason = '低价药' WHERE price < 30"
        ],
        "show": "SELECT archive_id, drug_name, price, archive_reason FROM drug_archive "
                "ORDER BY archive_id LIMIT 20",
        "precheck": "SELECT 1 FROM information_schema.tables "
                    "WHERE table_schema=DATABASE() AND table_name='drug_archive'",
    },
    "delete": {
        "title": "④ 删除 DELETE",
        "desc": "删除归档表中单价低于 5 元的记录",
        "sql": ["DELETE FROM drug_archive WHERE price < 5"],
        "show": "SELECT COUNT(*) AS 剩余归档行数 FROM drug_archive",
        "precheck": "SELECT 1 FROM information_schema.tables "
                    "WHERE table_schema=DATABASE() AND table_name='drug_archive'",
    },
    "drop": {
        "title": "⑤ 删表 DROP TABLE",
        "desc": "删除演示用的 drug_archive 表",
        "sql": ["DROP TABLE IF EXISTS drug_archive"],
        "show": "SELECT COUNT(*) AS 表是否还存在 FROM information_schema.tables "
                "WHERE table_schema=DATABASE() AND table_name='drug_archive'",
    },
    "join": {
        "title": "⑥ 连接查询 JOIN（≥3 表）",
        "desc": "医生开药金额 Top10：prescription_item + prescription + visit + doctor + department（5 表）",
        "special": "doctor_revenue",
    },
    "join2": {
        "title": "⑦ 连接查询 JOIN（患者全流水，7 表）",
        "desc": "某患者的完整就诊流水：appointment + patient + schedule + doctor + department + visit + prescription_item + drug",
        "special": "patient_flow",
    },
}


@bp.route("/ops")
def ops_page():
    return render_template("ops.html", ops=OPS)


@bp.route("/api/ops", methods=["POST"])
def api_ops():
    body = request.get_json(silent=True) or {}
    key = body.get("op")
    if key not in OPS:
        return _err("未知操作")
    op = OPS[key]

    # 前置检查：依赖的表必须已创建
    if op.get("precheck"):
        with get_cursor() as cur:
            cur.execute(op["precheck"])
            if not cur.fetchone():
                return _err("请先执行「① 建表」，再执行本操作")

    steps = []
    t0 = time.time()
    try:
        # 特殊：复杂 join 查询（只读）
        if op.get("special") == "doctor_revenue":
            rows = dao.report_doctor_revenue(10)
            steps.append({"sql": "SELECT d.doctor_name, COUNT(DISTINCT pr.presc_id) ... "
                                 "FROM prescription_item i JOIN prescription pr ... "
                                 "JOIN visit v ... JOIN doctor d ... JOIN department dp ... "
                                 "GROUP BY ... ORDER BY 开药总额 DESC LIMIT 10",
                          "rowcount": len(rows), "elapsed_ms": 0})
            return _ok({"steps": steps, "rows": rows, "columns": list(rows[0].keys()) if rows else [],
                        "elapsed_ms": round((time.time() - t0) * 1000, 2)})
        if op.get("special") == "patient_flow":
            pid = int(body.get("patient_id") or 1)
            rows = dao.report_patient_flow(pid, 20)
            steps.append({"sql": "7 表 LEFT JOIN，查询 patient_id=%d 的完整就诊流水" % pid,
                          "rowcount": len(rows), "elapsed_ms": 0})
            return _ok({"steps": steps, "rows": rows, "columns": list(rows[0].keys()) if rows else [],
                        "elapsed_ms": round((time.time() - t0) * 1000, 2)})

        # DDl/DML：在同一个事务里执行，成功提交、失败回滚
        with transaction() as cur:
            for sql in op["sql"]:
                s0 = time.time()
                cur.execute(sql)
                steps.append({"sql": sql.strip(),
                              "rowcount": cur.rowcount if cur.rowcount != -1 else 0,
                              "elapsed_ms": round((time.time() - s0) * 1000, 2)})

        rows, columns = [], []
        if op.get("show"):
            with get_cursor() as cur:
                cur.execute(op["show"])
                rows = cur.fetchall()
                columns = list(rows[0].keys()) if rows else [c[0] for c in (cur.description or [])]

        return _ok({"steps": steps, "rows": rows, "columns": columns,
                    "elapsed_ms": round((time.time() - t0) * 1000, 2)})
    except pymysql.MySQLError as e:
        return _err("SQL 执行失败：%s" % str(e), steps=steps)
    except Exception as e:                                     # noqa: BLE001
        return _err("执行异常：%s" % str(e), steps=steps,
                    trace=traceback.format_exc().splitlines()[-3:])


# =====================================================================
# 索引与性能对比（标志性实践 3）
# =====================================================================
PERF_CASES = {
    "appointment_patient": {
        "title": "按患者查预约（40000 行表）",
        "table": "appointment",
        "index": "idx_appt_patient",
        "with_sql": "SELECT SQL_NO_CACHE a.appt_id, a.appt_time, a.queue_no, a.status "
                    "FROM appointment a WHERE a.patient_id = %s",
        "without_sql": "SELECT SQL_NO_CACHE a.appt_id, a.appt_time, a.queue_no, a.status "
                       "FROM appointment a IGNORE INDEX (idx_appt_patient, idx_appt_patient_time) "
                       "WHERE a.patient_id = %s",
        "param": "patient_id",
    },
    "visit_doctor": {
        "title": "按医生统计就诊量（30051 行表 join doctor）",
        "table": "visit",
        "index": "idx_visit_doctor",
        "with_sql": "SELECT SQL_NO_CACHE v.doctor_id, COUNT(*) AS cnt FROM visit v "
                    "WHERE v.doctor_id = %s GROUP BY v.doctor_id",
        "without_sql": "SELECT SQL_NO_CACHE v.doctor_id, COUNT(*) AS cnt "
                       "FROM visit v IGNORE INDEX (idx_visit_doctor) "
                       "WHERE v.doctor_id = %s GROUP BY v.doctor_id",
        "param": "doctor_id",
    },
    "drug_name": {
        "title": "按药品名模糊查询（500 行表，数据量小对比不明显）",
        "table": "drug",
        "index": "idx_drug_name",
        "with_sql": "SELECT SQL_NO_CACHE drug_id, drug_name, price FROM drug WHERE drug_name LIKE %s",
        "without_sql": "SELECT SQL_NO_CACHE drug_id, drug_name, price FROM drug "
                       "IGNORE INDEX (idx_drug_name) WHERE drug_name LIKE %s",
        "param": "keyword",
    },
}


def _explain(cur, sql, args):
    cur.execute("EXPLAIN " + sql, args)
    row = cur.fetchone() or {}
    return {k: row.get(k) for k in ("type", "key", "rows", "possible_keys", "Extra") if k in row}


def _timed(cur, sql, args, repeat=5):
    durations = []
    for _ in range(repeat):
        t0 = time.perf_counter()
        cur.execute(sql, args)
        cur.fetchall()
        durations.append((time.perf_counter() - t0) * 1000)
    durations.sort()
    return {
        "avg_ms": round(sum(durations) / len(durations), 3),
        "min_ms": round(durations[0], 3),
        "max_ms": round(durations[-1], 3),
        "samples": [round(d, 3) for d in durations],
    }


@bp.route("/perf")
def perf_page():
    return render_template("perf.html", cases=PERF_CASES)


@bp.route("/api/perf", methods=["POST"])
def api_perf():
    body = request.get_json(silent=True) or {}
    key = body.get("case")
    if key not in PERF_CASES:
        return _err("未知用例")
    case = PERF_CASES[key]

    # 参数
    if case["param"] == "patient_id":
        param = int(body.get("patient_id") or 1234)
        args = (param,)
    elif case["param"] == "doctor_id":
        param = int(body.get("doctor_id") or 5)
        args = (param,)
    else:
        param = body.get("keyword") or "阿"
        args = ("%" + param + "%",)

    repeat = min(int(body.get("repeat") or 5), 20)

    # 索引是否存在
    with get_cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) AS c FROM information_schema.statistics "
            "WHERE table_schema = DATABASE() AND table_name = %s AND index_name = %s",
            (case["table"], case["index"]),
        )
        index_exists = cur.fetchone()["c"] > 0

    try:
        with get_cursor() as cur:
            plan_without = _explain(cur, case["without_sql"], args)
            time_without = _timed(cur, case["without_sql"], args, repeat)
        with get_cursor() as cur:
            plan_with = _explain(cur, case["with_sql"], args)
            time_with = _timed(cur, case["with_sql"], args, repeat)
    except pymysql.MySQLError as e:
        return _err("执行失败：%s" % str(e))

    speedup = None
    if time_with["avg_ms"] > 0:
        speedup = round(time_without["avg_ms"] / time_with["avg_ms"], 2)

    return _ok({
        "title": case["title"],
        "index": case["index"],
        "index_exists": index_exists,
        "param": param,
        "repeat": repeat,
        "without": {"plan": plan_without, "time": time_without, "sql": case["without_sql"]},
        "with": {"plan": plan_with, "time": time_with, "sql": case["with_sql"]},
        "speedup": speedup,
    })


@bp.route("/api/index_toggle", methods=["POST"])
def api_index_toggle():
    """真正地删除/重建索引，用于报告里更严格的对照实验。"""
    body = request.get_json(silent=True) or {}
    table = body.get("table")
    index = body.get("index")
    column = body.get("column")
    action = body.get("action")
    if not table or not index or action not in ("drop", "create"):
        return _err("参数不完整")
    if action == "create" and not column:
        return _err("创建索引需要提供 column")
    try:
        with transaction() as cur:
            cur.execute(
                "SELECT COUNT(*) AS c FROM information_schema.statistics "
                "WHERE table_schema = DATABASE() AND table_name = %s AND index_name = %s",
                (table, index),
            )
            exists = cur.fetchone()["c"] > 0
            if action == "drop" and not exists:
                raise BizError("索引 %s 不存在，无需删除" % index)
            if action == "create" and exists:
                raise BizError("索引 %s 已存在" % index)
            if action == "drop":
                cur.execute("DROP INDEX %s ON %s" % (index, table))
                msg = "已删除索引 %s ON %s" % (index, table)
            else:
                cur.execute("CREATE INDEX %s ON %s (%s)" % (index, table, column))
                msg = "已重建索引 %s ON %s(%s)" % (index, table, column)
        return _ok({"message": msg})
    except BizError as e:
        return _err(str(e))
    except pymysql.MySQLError as e:
        return _err("操作失败：%s" % str(e))
