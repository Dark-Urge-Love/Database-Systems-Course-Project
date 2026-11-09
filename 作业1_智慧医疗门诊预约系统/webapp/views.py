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
# 药房采购工作台：把课程要求的六项基本操作融合进一个连贯的业务剧本
# ------------------------------------------------------------------
# 剧本：某医药供应商向药房提交了一份药品报价单，采购员按流程处理——
#   建报价登记表 → 录入报价并上架新药 → 按报价调价 → 滞销药品淘汰
#   → 报价单完成使命后销毁临时表 → 管理层查看运营报表（跨表 JOIN）
# 每一步都有真实的业务动机，同时一一对应课程要求：
#   ① CREATE TABLE  ② INSERT  ③ UPDATE  ④ DELETE  ⑤ DROP TABLE  ⑥⑦ JOIN
# =====================================================================
STEPS = {
    "create": {
        "tag": "课程要求 ① CREATE TABLE",
        "title": "收到供应商报价单，建立报价登记表",
        "desc": "采购员收到「华润医药」提交的药品报价单。按采购制度，报价单需要先登记备案："
                "为本次报价创建一张临时报价登记表 drug_quote，记录报价药品、价格、可供量与报价日期。",
        "sql": [
            "DROP TABLE IF EXISTS drug_quote",
            """CREATE TABLE drug_quote (
                 quote_id      INT AUTO_INCREMENT PRIMARY KEY COMMENT '报价流水号',
                 drug_name     VARCHAR(100) NOT NULL COMMENT '药品名称',
                 spec          VARCHAR(50)  NOT NULL COMMENT '规格',
                 unit          VARCHAR(10)  COMMENT '单位',
                 manufacturer  VARCHAR(100) COMMENT '生产厂家',
                 quote_price   DECIMAL(8,2) NOT NULL COMMENT '报价单价(元)',
                 supply_stock  INT NOT NULL DEFAULT 0 COMMENT '可供数量',
                 is_new        TINYINT NOT NULL DEFAULT 0 COMMENT '1=新药推介 0=目录内品种调价',
                 quote_date    DATE NOT NULL COMMENT '报价日期',
                 quote_note    VARCHAR(100) COMMENT '备注'
               ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='供应商报价登记表(临时,流程结束后销毁)'""",
        ],
        "show": "SHOW FULL COLUMNS FROM drug_quote",
    },
    "insert": {
        "tag": "课程要求 ② INSERT（含跨表 INSERT…SELECT）",
        "title": "录入报价明细，新药批量上架",
        "desc": "把报价单的 8 条明细登记入表。其中 4 条是对目录内现有品种的调价，另外 4 条是新药推介——"
                "经药师初审合格后，新药直接批量上架进入药品目录 drug（INSERT…SELECT…WHERE NOT EXISTS 防重复上架）。",
        "sql": [
            """INSERT INTO drug_quote
               (drug_name, spec, unit, manufacturer, quote_price, supply_stock, is_new, quote_date, quote_note)
               VALUES ('阿莫西林胶囊',        '0.25g*24粒',   '盒', '华北制药', 15.80, 400, 0, '2026-11-09', '目录内品种调价'),
                      ('布洛芬缓释胶囊',      '0.3g*20粒',    '盒', '中美史克', 18.60, 350, 0, '2026-11-09', '目录内品种调价'),
                      ('奥美拉唑肠溶胶囊',    '20mg*14粒',    '盒', '阿斯利康', 22.40, 300, 0, '2026-11-09', '目录内品种调价'),
                      ('苯磺酸氨氯地平片',    '5mg*7片',      '盒', '辉瑞',    12.90, 500, 0, '2026-11-09', '目录内品种调价'),
                      ('磷酸奥司他韦颗粒',    '30mg*10袋',    '盒', '东阳光',   45.00, 200, 1, '2026-11-09', '新药推介'),
                      ('蒲地蓝消炎口服液',    '10ml*12支',    '盒', '济川药业', 32.50, 260, 1, '2026-11-09', '新药推介'),
                      ('依巴斯汀片',          '10mg*12片',    '盒', '杭州澳医', 25.80, 180, 1, '2026-11-09', '新药推介'),
                      ('瑞舒伐他汀钙片',      '10mg*7片',     '盒', '京新药业', 28.60, 220, 1, '2026-11-09', '新药推介')""",
            """INSERT INTO drug (drug_name, spec, unit, manufacturer, price, stock, is_rx, status)
               SELECT q.drug_name, q.spec, q.unit, q.manufacturer, q.quote_price, q.supply_stock, 1, 1
               FROM drug_quote q
               WHERE q.is_new = 1
                 AND NOT EXISTS (SELECT 1 FROM drug d
                                 WHERE d.drug_name = q.drug_name AND d.spec = q.spec)""",
        ],
        "show": "SELECT drug_id, drug_name, spec, manufacturer, price, stock "
                "FROM drug WHERE drug_id > 500 ORDER BY drug_id",
        "precheck": "SELECT 1 FROM information_schema.tables "
                    "WHERE table_schema=DATABASE() AND table_name='drug_quote'",
    },
    "update": {
        "tag": "课程要求 ③ UPDATE（跨表 UPDATE…JOIN 调价）",
        "title": "按最新报价调整目录内药品价格",
        "desc": "采购委员会审批通过：目录内 4 个品种按供应商最新报价执行调价。"
                "报价表与药品目录按「药品名 + 规格」关联（UPDATE…JOIN），一次更新同步生效，"
                "并输出调价前后对比。",
        "sql": [
            """UPDATE drug d
               JOIN drug_quote q ON d.drug_name = q.drug_name AND d.spec = q.spec
               SET d.price = q.quote_price
               WHERE q.is_new = 0""",
        ],
        "show": "SELECT d.drug_id, d.drug_name, d.spec, d.price AS 调价后单价, q.quote_price AS 报价单价 "
                "FROM drug d JOIN drug_quote q ON d.drug_name = q.drug_name AND d.spec = q.spec "
                "WHERE q.is_new = 0 ORDER BY d.drug_id",
        "precheck": "SELECT 1 FROM information_schema.tables "
                    "WHERE table_schema=DATABASE() AND table_name='drug_quote'",
    },
    "delete": {
        "tag": "课程要求 ④ DELETE（跨表条件删除，外键安全）",
        "title": "滞销与试销不达标药品淘汰下架",
        "desc": "药剂科季度清理：本次推介的新药试销期内没有任何处方销售记录，予以下架；"
                "目录内老药若库存为零且历史上也从未被开过处方（LEFT JOIN 处方明细判空），同样淘汰。"
                "凡被处方引用的药品受外键约束保护，一律不可删除。",
        "sql": [
            """DELETE d FROM drug d
               LEFT JOIN prescription_item i ON i.drug_id = d.drug_id
               WHERE i.presc_id IS NULL
                 AND (d.drug_id > 500 OR d.stock = 0)""",
        ],
        "show": "SELECT COUNT(*) AS 药品目录现存品种数 FROM drug",
        "precheck": "SELECT 1 FROM information_schema.tables "
                    "WHERE table_schema=DATABASE() AND table_name='drug_quote'",
    },
    "drop": {
        "tag": "课程要求 ⑤ DROP TABLE（临时表生命周期结束）",
        "title": "报价单归档，临时报价表完成使命销毁",
        "desc": "报价结果已全部落地到药品目录 drug，按采购制度报价登记表留存 30 天后销毁。"
                "临时表用完即删，不给主库留下垃圾数据——这正是业务中 DROP TABLE 的典型用途。",
        "sql": ["DROP TABLE IF EXISTS drug_quote"],
        "show": "SELECT COUNT(*) AS 表是否还存在 FROM information_schema.tables "
                "WHERE table_schema=DATABASE() AND table_name='drug_quote'",
    },
}

REPORTS = {
    "join": {
        "tag": "课程要求 ⑥ JOIN（5 表连接 + 聚合）",
        "title": "医生开药金额 Top10（药剂科绩效月报）",
        "desc": "药剂科每月统计各医生开药金额用于合理用药审查：prescription_item → prescription → visit → doctor → department 五表连接后分组汇总。",
        "special": "doctor_revenue",
    },
    "join2": {
        "tag": "课程要求 ⑥ JOIN（7 表连接，患者全流水）",
        "title": "患者完整就诊流水（客服查询）",
        "desc": "客服接到患者来电咨询历史就诊：从患者出发经预约、排班、医生、科室、就诊、处方、明细、药品七表连接，还原该患者每一次就诊的完整链路。",
        "special": "patient_flow",
    },
}

# 兼容旧接口名
OPS = {**STEPS, **REPORTS}


@bp.route("/ops")
def ops_page():
    return render_template("ops.html", steps=STEPS, reports=REPORTS)


@bp.route("/api/ops", methods=["POST"])
def api_ops():
    body = request.get_json(silent=True) or {}
    key = body.get("op")
    if key not in OPS:
        return _err("未知操作")
    op = OPS[key]

    # 前置检查：业务流程有先后依赖（如先有报价表才能录入报价）
    if op.get("precheck"):
        with get_cursor() as cur:
            cur.execute(op["precheck"])
            if not cur.fetchone():
                return _err("请先执行第 1 步「建立报价登记表」，报价表尚不存在")

    steps = []
    t0 = time.time()
    try:
        # 特殊：复杂 join 报表（只读）
        if op.get("special") == "doctor_revenue":
            rows = dao.report_doctor_revenue(10)
            steps.append({"sql": "SELECT d.doctor_name, dp.dept_name, COUNT(DISTINCT pr.presc_id) ... "
                                 "FROM prescription_item i JOIN prescription pr ... "
                                 "JOIN visit v ... JOIN doctor d ... JOIN department dp ... "
                                 "GROUP BY ... ORDER BY 开药金额 DESC LIMIT 10",
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

        # DDL/DML：在同一个事务里执行，成功提交、失败回滚
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
