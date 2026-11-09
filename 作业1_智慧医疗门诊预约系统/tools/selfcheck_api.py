# -*- coding: utf-8 -*-
"""端到端接口自查：按课程要求逐项调用真实 HTTP 接口"""
import json, sys, time, urllib.request, urllib.error, urllib.parse

BASE = "http://127.0.0.1:5055"
ok_n = fail_n = 0
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

def call(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with OPENER.open(req, timeout=60) as r:
            raw = r.read().decode()
            try:
                return r.status, json.loads(raw)
            except ValueError:
                return r.status, raw
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:200]

def check(name, cond, detail=""):
    global ok_n, fail_n
    if cond:
        ok_n += 1
        print("  [PASS] %-34s %s" % (name, detail))
    else:
        fail_n += 1
        print("  [FAIL] %-34s %s" % (name, detail))

print("=" * 72)
print("A. 页面可访问性")
print("=" * 72)
for path, kw in [("/", "数据总量"), ("/ops", "药品"), ("/register", "挂号"), ("/perf", "索引")]:
    st, html = call("GET", path)
    raw = html if isinstance(html, str) else json.dumps(html, ensure_ascii=False)
    check("GET %s" % path, st == 200 and kw in raw, "HTTP %s / 关键字「%s」%s" % (st, kw, "命中" if kw in raw else "未命中"))

print()
print("=" * 72)
print("B. 六项基本操作（走界面接口）")
print("=" * 72)
stamp = str(int(time.time()))[-6:]
drug_name = "自查药品" + stamp

st, r = call("POST", "/api/pharmacy/drugs",
             {"name": drug_name, "spec": "0.25g*24", "unit": "盒", "maker": "自查药厂",
              "price": 18.5, "stock": 100, "rx": True})
check("1) 插入药品", r.get("ok") and r.get("data", {}).get("id"), "SQL=%s" % str(r.get("data", {}).get("sql"))[:60])
drug_id = (r.get("data") or {}).get("id")

st, r = call("POST", "/api/pharmacy/drugs",
             {"name": drug_name, "spec": "0.25g*24", "price": 19, "stock": 1})
check("2) 重复品种被拦截", not r.get("ok"), r.get("error", ""))

st, r = call("POST", "/api/pharmacy/drugs/%s" % drug_id,
             {"name": drug_name, "spec": "0.25g*24", "unit": "盒", "maker": "自查药厂",
              "price": 21.8, "stock": 88, "rx": True})
check("3) 修改药品（调价+盘点）", r.get("ok"), "SQL=%s" % str(r.get("data", {}).get("sql"))[:70])

st, r = call("GET", "/api/pharmacy/drugs?q=" + urllib.parse.quote(drug_name))
rows = (r.get("data") or {}).get("rows") or []
hit = [x for x in rows if x["id"] == drug_id]
check("   修改已生效", hit and abs(hit[0]["price"] - 21.8) < 0.01 and hit[0]["stock"] == 88,
      "price=%s stock=%s" % (hit[0]["price"], hit[0]["stock"]) if hit else "未查到")

st, r = call("POST", "/api/pharmacy/drugs/%s/remove" % drug_id)
check("4) 删除药品", r.get("ok"), r.get("data", {}).get("message") or r.get("error", ""))

# 已被处方引用的药品应被拦
st, r = call("POST", "/api/pharmacy/drugs/1/remove")
check("   已开方药品禁删", not r.get("ok"), str(r.get("error", ""))[:52])

print()
print("C. 药品台账（建表 / 按药登记）")
print("=" * 72)
ledger_name = "药品不良反应登记" + stamp
st, r = call("POST", "/api/archives", {"name": ledger_name, "fields": [
    {"label": "反应表现", "type": "long", "required": True},
    {"label": "发生日期", "type": "date", "required": True},
    {"label": "严重程度", "type": "text"},
    {"label": "处理措施", "type": "long"}]})
tbl = (r.get("data") or {}).get("table")
create_sql = str((r.get("data") or {}).get("sql") or "")
check("5) 新建台账（CREATE TABLE）", r.get("ok") and tbl, "表=%s" % tbl)
check("   自动挂 drug_id 外键",
      "drug_id" in create_sql and "REFERENCES drug" in create_sql,
      "FOREIGN KEY → drug(drug_id)")

st, r = call("GET", "/api/archives")
names = [x["name"] for x in (r.get("data") or {}).get("items", [])]
check("   台账出现在列表", ledger_name in names, "共 %d 个台账" % len(names))

st, r = call("POST", "/api/archives/%s/records" % tbl,
             {"values": {"f1": "皮疹", "f2": "2026-01-01"}})
check("   未选药时被拦截", not r.get("ok"), str(r.get("error", ""))[:44])

st, r = call("POST", "/api/pharmacy/drugs",
             {"name": "自查测试药" + stamp, "spec": "1mg*1片", "unit": "盒", "maker": "自查药厂",
              "price": 1.0, "stock": 10, "rx": True})
test_drug = (r.get("data") or {}).get("id")
check("   新药入库备用", r.get("ok") and test_drug, "id=%s" % test_drug)

st, r = call("POST", "/api/archives/%s/records" % tbl,
             {"drug_id": test_drug,
              "values": {"f1": "自查反应", "f2": "2026-01-01", "f3": "轻度", "f4": "观察"}})
check("6) 按药品登记（INSERT）", r.get("ok"), r.get("data", {}).get("message", ""))

st, r = call("POST", "/api/pharmacy/drugs/%s/remove" % test_drug)
check("   台账引用的药品禁删", not r.get("ok"), str(r.get("error", ""))[:52])

print()
print("=" * 72)
print("D. 跨表操作（JOIN 报表）")
print("=" * 72)
st, r = call("POST", "/api/report", {"name": "doctor_revenue"})
rows = (r.get("data") or {}).get("rows") or []
check("7) 5 表 JOIN 报表", r.get("ok") and len(rows) > 0, "%d 行，列=%s" % (len(rows), list(rows[0].keys())[:4] if rows else []))

st, r = call("POST", "/api/report", {"name": "drug_consume"})
rows3 = (r.get("data") or {}).get("rows") or []
check("   6 表 JOIN 药品消耗排行", r.get("ok") and len(rows3) > 0, "%d 行" % len(rows3))

st, r = call("POST", "/api/report", {"name": "drug_ledger", "table": tbl})
rows4 = (r.get("data") or {}).get("rows") or []
check("   台账 JOIN 药品目录", r.get("ok") and len(rows4) > 0,
      "%d 行，含药品列=%s" % (len(rows4), "药品" in ((r.get("data") or {}).get("columns") or [])))

st, r = call("POST", "/api/report", {"name": "patient_flow", "patient_id": 7})
rows2 = (r.get("data") or {}).get("rows") or []
check("8) 7 表 JOIN 报表", r.get("ok") and len(rows2) > 0, "%d 行" % len(rows2))

st, r = call("POST", "/api/archives/%s/remove" % tbl, {})
check("   删表前二次确认", (not r.get("ok")) and r.get("need_confirm"), str(r.get("error"))[:40])
st, r = call("POST", "/api/archives/%s/remove" % tbl, {"force": True})
check("   删除台账（DROP TABLE）", r.get("ok"), str(r.get("data", {}).get("sql"))[:40])
st, r = call("POST", "/api/pharmacy/drugs/%s/remove" % test_drug)
check("   清理测试药", r.get("ok"), r.get("data", {}).get("message") or r.get("error", ""))

print()
print("=" * 72)
print("E. 事务 / 并发 / 索引")
print("=" * 72)
st, r = call("GET", "/api/schedules?doctor_id=1&all=1")
sch = (r.get("data") or [])
sch = [s for s in sch if (s.get("remain_slots") or 0) > 12]
if sch:
    sid, before = sch[0]["schedule_id"], sch[0]["remain_slots"]
    st, r = call("POST", "/api/register", {"patient_id": 3, "schedule_id": sid})
    check("9) 挂号事务提交", r.get("ok"), "号源 %s -> %s" % (
        (r.get("data") or {}).get("remain_before"), (r.get("data") or {}).get("remain_after")))
    check("   事务 SQL 三段式", "FOR UPDATE" in str(r.get("data", {}).get("sql", "")) and "COMMIT" in str(r.get("data", {}).get("sql", "")),
          "含 SELECT FOR UPDATE / INSERT / UPDATE / COMMIT")
    st, r2 = call("GET", "/api/schedules?doctor_id=1&all=1")
    after = [s["remain_slots"] for s in r2.get("data", []) if s["schedule_id"] == sid]
    check("   号源原子扣减", after and after[0] == before - 1, "%d -> %s" % (before, after))
else:
    print("  [SKIP] 没有余量足够的排班")

st, r = call("POST", "/api/dispense_concurrency",
             {"drug_id": 2, "clients": 10, "qty": 1, "reset_stock": 5})
d = r.get("data") or {}
if r.get("ok"):
    check("10) 10 窗口并发发药", True,
          "成功 %s / 失败 %s / 耗时 %sms / 库存 %s->%s" % (
              d.get("success_count"), d.get("failed_count"), d.get("elapsed_ms"),
              d.get("stock_before"), d.get("stock_after")))
    check("    无超发（成功次数=库存减少）",
          d.get("oversold") is False and d.get("consumed") == d.get("success_count"),
          "成功 %s, 库存减少 %s, oversold=%s" % (
              d.get("success_count"), d.get("consumed"), d.get("oversold")))
    check("    逐次耗时已写入 access_log", d.get("log_count") == d.get("clients"),
          "本次写入 %s 条日志" % d.get("log_count"))
else:
    check("10) 10 窗口并发发药", False, str(r.get("error"))[:60])

st, r = call("GET", "/api/access_logs")
logs = (r.get("data") or {}).get("rows") or []
check("    日志表可查（JOIN 药品名）", r.get("ok") and len(logs) > 0,
      "共 %s 条，最近 1 条药品=%s" % (
          (r.get("data") or {}).get("total"),
          logs[0].get("drug_name") if logs else "—"))

for case, extra in [("appointment_patient", {"patient_id": 1234}), ("visit_doctor", {"doctor_id": 5})]:
    st, r = call("POST", "/api/perf", dict(case=case, repeat=3, **extra))
    d = r.get("data") or {}
    w, wo = d.get("with", {}), d.get("without", {})
    check("11) 索引对比 %s" % case, r.get("ok") and d.get("speedup") is not None,
          "走索引 %s ms / 全表 %s ms / 提速 %sx / type %s vs %s" % (
              w.get("time", {}).get("avg_ms"), wo.get("time", {}).get("avg_ms"), d.get("speedup"),
              w.get("plan", {}).get("type"), wo.get("plan", {}).get("type")))

print()
print("=" * 72)
print("结果：通过 %d 项，失败 %d 项" % (ok_n, fail_n))
print("=" * 72)
sys.exit(0 if fail_n == 0 else 1)
