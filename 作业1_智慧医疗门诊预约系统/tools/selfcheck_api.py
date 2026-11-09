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
print("C. 业务档案（建表 / 删表）")
print("=" * 72)
arch_name = "供应商档案" + stamp
st, r = call("POST", "/api/archives", {"name": arch_name, "fields": [
    {"label": "供应商名称", "type": "text", "required": True},
    {"label": "联系人", "type": "text"},
    {"label": "结算金额", "type": "money"},
    {"label": "签约日期", "type": "date"}]})
tbl = (r.get("data") or {}).get("table")
check("5) 新建档案（CREATE TABLE）", r.get("ok") and tbl, "表=%s" % tbl)

st, r = call("GET", "/api/archives")
names = [x["name"] for x in (r.get("data") or {}).get("items", [])]
check("   档案出现在列表", arch_name in names, "共 %d 个档案" % len(names))

st, r = call("POST", "/api/archives/%s/records" % tbl,
             {"values": {"f1": "华东医药", "f2": "张经理", "f3": "128000.00", "f4": "2026-03-01"}})
check("   录入档案记录（INSERT）", r.get("ok"), r.get("data", {}).get("message", ""))

st, r = call("POST", "/api/archives/%s/remove" % tbl, {})
check("   删表前二次确认", (not r.get("ok")) and r.get("need_confirm"), str(r.get("error"))[:40])

st, r = call("POST", "/api/archives/%s/remove" % tbl, {"force": True})
check("6) 删除档案（DROP TABLE）", r.get("ok"), str(r.get("data", {}).get("sql"))[:40])

print()
print("=" * 72)
print("D. 跨表操作（JOIN 报表）")
print("=" * 72)
st, r = call("POST", "/api/report", {"name": "doctor_revenue"})
rows = (r.get("data") or {}).get("rows") or []
check("7) 5 表 JOIN 报表", r.get("ok") and len(rows) > 0, "%d 行，列=%s" % (len(rows), list(rows[0].keys())[:4] if rows else []))
st, r = call("POST", "/api/report", {"name": "patient_flow", "patient_id": 7})
rows2 = (r.get("data") or {}).get("rows") or []
check("8) 7 表 JOIN 报表", r.get("ok") and len(rows2) > 0, "%d 行" % len(rows2))

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

st, r = call("POST", "/api/concurrency_demo", {"schedule_id": sch[0]["schedule_id"] if sch else 1, "thread_count": 10})
d = r.get("data") or {}
if r.get("ok"):
    check("10) 10 线程并发抢号", True,
          "成功 %s / 失败 %s / 耗时 %sms / 号源 %s->%s" % (
              d.get("success_count"), d.get("failed_count"), d.get("elapsed_ms"),
              d.get("remain_before"), d.get("remain_after")))
    check("    无超卖（成功数=号源减少）",
          d.get("oversold") is False and d.get("consumed") == d.get("success_count"),
          "成功 %s, 号源减少 %s, oversold=%s" % (d.get("success_count"), d.get("consumed"), d.get("oversold")))
else:
    check("10) 10 线程并发抢号", False, str(r.get("error"))[:60])

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
