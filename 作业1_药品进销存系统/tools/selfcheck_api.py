# -*- coding: utf-8 -*-
"""端到端接口自查：按课程要求逐项调用真实 HTTP 接口"""
import datetime, json, sys, time, urllib.request, urllib.error, urllib.parse

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
for path, kw in [("/", "数据总量"), ("/ops", "采购入库"), ("/perf", "索引")]:
    st, html = call("GET", path)
    raw = html if isinstance(html, str) else json.dumps(html, ensure_ascii=False)
    check("GET %s" % path, st == 200 and kw in raw, "HTTP %s / 关键字「%s」%s"
          % (st, kw, "命中" if kw in raw else "未命中"))

print()
print("=" * 72)
print("B. 六项基本操作（药品目录）")
print("=" * 72)
stamp = str(int(time.time()))[-6:]
drug_name = "自查药品" + stamp

st, r = call("GET", "/api/options")
opts = r.get("data") or {}
cate_id = (opts.get("categories") or [{}])[0].get("id", 1)
check("0) 基础资料下拉", r.get("ok") and opts.get("suppliers") and opts.get("warehouses")
      and opts.get("buyers") and opts.get("sellers") and opts.get("drugs"),
      "供应商 %s / 仓库 %s / 采购员 %s / 销售员 %s / 药品 %s" % (
          len(opts.get("suppliers") or []), len(opts.get("warehouses") or []),
          len(opts.get("buyers") or []), len(opts.get("sellers") or []),
          len(opts.get("drugs") or [])))

st, r = call("POST", "/api/drugs",
             {"name": drug_name, "spec": "0.25g*24粒", "unit": "盒", "cate": cate_id,
              "maker": "自查药厂", "pp": 12.5, "rp": 16.8, "life": 24, "rx": True})
check("1) 插入药品（INSERT）", r.get("ok") and r.get("data", {}).get("sql"),
      str(r.get("data", {}).get("message") or r.get("error", ""))[:60])
drug_id = None
if r.get("ok"):
    st2, r2 = call("GET", "/api/drugs?q=" + urllib.parse.quote(drug_name))
    rows = (r2.get("data") or {}).get("rows") or []
    drug_id = rows[0]["id"] if rows else None
    check("   新药出现在列表", bool(drug_id), "编码=%s" % (rows[0]["code"] if rows else "—"))

st, r = call("POST", "/api/drugs",
             {"name": drug_name, "spec": "0.25g*24粒", "cate": cate_id, "pp": 12, "rp": 15})
check("2) 同名同规格被拦截（唯一约束）", not r.get("ok"), str(r.get("error", ""))[:52])

st, r = call("POST", "/api/drugs/%s" % drug_id,
             {"name": drug_name, "spec": "0.25g*24粒", "unit": "盒", "cate": cate_id,
              "maker": "自查药厂", "pp": 12.5, "rp": 19.9, "life": 24, "rx": True,
              "status": True})
check("3) 修改药品（UPDATE 调价）", r.get("ok"), str(r.get("data", {}).get("sql") or "")[:60])

st, r = call("GET", "/api/drugs?q=" + urllib.parse.quote(drug_name))
rows = (r.get("data") or {}).get("rows") or []
check("   修改已生效", rows and abs(rows[0]["rp"] - 19.9) < 0.01,
      "零售价=%s" % (rows[0]["rp"] if rows else "—"))

st, r = call("POST", "/api/drugs/%s/remove" % drug_id)
check("4) 删除无引用药品（DELETE）", r.get("ok"),
      str(r.get("data", {}).get("message") or r.get("error", ""))[:60])

st, r = call("POST", "/api/drugs/1/remove")
check("   有业务数据的药品禁删", not r.get("ok"), str(r.get("error", ""))[:56])

print()
print("=" * 72)
print("C. 业务表单（CREATE TABLE / INSERT / DROP TABLE）")
print("=" * 72)
form_name = "药品召回登记" + stamp
st, r = call("POST", "/api/forms", {"name": form_name, "fields": [
    {"label": "召回原因", "type": "long", "required": True},
    {"label": "召回日期", "type": "date", "required": True},
    {"label": "涉及批号", "type": "text"},
    {"label": "处理结果", "type": "text"}]})
tbl = (r.get("data") or {}).get("table")
create_sql = str((r.get("data") or {}).get("sql") or "")
check("5) 新建表单（CREATE TABLE）", r.get("ok") and tbl, "表=%s" % tbl)
check("   自动挂 drug_id 外键",
      "drug_id" in create_sql and "REFERENCES drug" in create_sql,
      "FOREIGN KEY → drug(drug_id)")

st, r = call("GET", "/api/forms")
names = [x["name"] for x in (r.get("data") or {}).get("items", [])]
check("   表单出现在列表", form_name in names, "共 %d 个表单" % len(names))

st, r = call("POST", "/api/forms/%s/records" % tbl, {"values": {"f1": "批号异常", "f2": "2026-01-01"}})
check("   未选药时被拦截", not r.get("ok"), str(r.get("error", ""))[:44])

st, r = call("POST", "/api/drugs",
             {"name": "自查测试药" + stamp, "spec": "1mg*1片", "unit": "盒", "cate": cate_id,
              "maker": "自查药厂", "pp": 1.0, "rp": 2.0, "life": 24, "rx": True})
st2, r2 = call("GET", "/api/drugs?q=" + urllib.parse.quote("自查测试药" + stamp))
trows = (r2.get("data") or {}).get("rows") or []
test_drug = trows[0]["id"] if trows else None
check("   新药入库备用", bool(test_drug), "id=%s" % test_drug)

st, r = call("POST", "/api/forms/%s/records" % tbl,
             {"drug_id": test_drug,
              "values": {"f1": "自查召回", "f2": "2026-01-01", "f3": "B20260101", "f4": "已下架"}})
check("6) 按药品登记（INSERT）", r.get("ok"),
      str(r.get("data", {}).get("message") or r.get("error", ""))[:52])

st, r = call("POST", "/api/drugs/%s/remove" % test_drug)
check("   表单引用的药品禁删", not r.get("ok"), str(r.get("error", ""))[:52])

st, r = call("POST", "/api/forms/%s/remove" % tbl, {})
check("7) 删表前二次确认", (not r.get("ok")) and r.get("need_confirm"), str(r.get("error"))[:40])
st, r = call("POST", "/api/forms/%s/remove" % tbl, {"force": True})
check("   删除表单（DROP TABLE）", r.get("ok"), str(r.get("data", {}).get("sql") or "")[:40])
st, r = call("POST", "/api/drugs/%s/remove" % test_drug)
check("   清理测试药", r.get("ok"), str(r.get("data", {}).get("message") or r.get("error", ""))[:52])

print()
print("=" * 72)
print("D. 采购入库事务（T1，6 张表）")
print("=" * 72)
sup = (opts.get("suppliers") or [{}])[0].get("id")
wh = (opts.get("warehouses") or [{}])[0].get("id")
buyer = (opts.get("buyers") or [{}])[0].get("id")
seller = [e for e in (opts.get("sellers") or []) if e.get("wh_id") == wh]
seller = (seller or (opts.get("sellers") or [{}]))[0].get("id")

st, r = call("GET", "/api/drugs?q=" + urllib.parse.quote("阿莫西林胶囊"))
drows = (r.get("data") or {}).get("rows") or []
tid = drows[0]["id"] if drows else 1
stock0 = drows[0]["stock"] if drows else 0

st, r = call("POST", "/api/purchases",
             {"supplier_id": sup, "warehouse_id": wh, "employee_id": buyer,
              "drug_id": tid, "qty": 50, "unit_price": 9.9,
              "batch_no": "SELF%s" % stamp, "prod_date": "2026-06-01",
              "expiry_date": "2028-06-01"})
d = r.get("data") or {}
check("8) 采购入库事务提交", r.get("ok"),
      "%s 库存 %s → %s" % (d.get("po_no"), d.get("stock_before"), d.get("stock_after")))
sql = str(d.get("sql") or "")
check("   事务 SQL 覆盖 6 张表",
      all(k in sql for k in ("purchase_order", "purchase_item", "stock_batch",
                             "stock", "stock_movement", "COMMIT")),
      "采购单+明细+批次+库存+流水+COMMIT")
check("   库存原子增加", r.get("ok") and d.get("stock_after") == d.get("stock_before") + 50,
      "+50")

st, r = call("GET", "/api/purchases")
prows = (r.get("data") or {}).get("rows") or []
check("   最近采购单出现新单", prows and prows[0].get("单号") == d.get("po_no"),
      str(prows[0])[:70] if prows else "无")

print()
print("=" * 72)
print("E. 销售出库事务（T2，行锁 + 批次先出）")
print("=" * 72)
st, r = call("POST", "/api/sales",
             {"warehouse_id": wh, "employee_id": seller, "drug_id": tid, "qty": 5,
              "customer_name": "自查大药房", "customer_type": "连锁药店"})
d = r.get("data") or {}
check("9) 销售出库事务提交", r.get("ok"),
      "%s 库存 %s → %s，扣批次 %s" % (
          d.get("so_no"), d.get("stock_before"), d.get("stock_after"), d.get("batch_no")))
sql = str(d.get("sql") or "")
check("   事务含 FOR UPDATE 行锁", "FOR UPDATE" in sql and "COMMIT" in sql,
      "锁库存行 → 校验 → 扣减 → 流水")

st, r = call("POST", "/api/sales",
             {"warehouse_id": wh, "employee_id": seller, "drug_id": tid, "qty": 999999,
              "customer_name": "自查大药房", "customer_type": "个人"})
check("   库存不足被拦截", not r.get("ok"), str(r.get("error", ""))[:48])

st, r = call("POST", "/api/report", {"name": "drug_trace", "drug_id": tid})
rows = (r.get("data") or {}).get("rows") or []
check("   出库写入库存流水（7 表 JOIN）",
      r.get("ok") and rows and any(r_.get("关联单号") == d.get("so_no") for r_ in rows),
      "最近 20 条流水含本次单号")

print()
print("=" * 72)
print("F. 跨数据表操作（JOIN 报表）")
print("=" * 72)
for rep, need in [("supplier_rank", 5), ("drug_sales", 5), ("batch_chain", 6)]:
    st, r = call("POST", "/api/report", {"name": rep})
    rows = (r.get("data") or {}).get("rows") or []
    check("10) %s（%d 表 JOIN）" % (rep, need), r.get("ok") and len(rows) > 0,
          "%d 行，列=%s" % (len(rows), list(rows[0].keys())[:4] if rows else []))

st, r = call("POST", "/api/report", {"name": "drug_trace", "drug_id": tid})
rows = (r.get("data") or {}).get("rows") or []
check("    drug_trace（7 表 JOIN）", r.get("ok") and len(rows) > 0, "%d 行" % len(rows))

print()
print("=" * 72)
print("G. 多用户并发 + 访问性能记录")
print("=" * 72)
st, r = call("POST", "/api/sale_concurrency",
             {"drug_id": tid, "warehouse_id": wh, "clients": 10, "qty": 1, "reset_stock": 8})
d = r.get("data") or {}
if r.get("ok"):
    check("11) 10 终端并发出库", True,
          "成功 %s / 失败 %s / 耗时 %sms / 库存 %s->%s" % (
              d.get("success_count"), d.get("failed_count"), d.get("elapsed_ms"),
              d.get("stock_before"), d.get("stock_after")))
    check("    无超卖（成功次数=库存减少）",
          d.get("oversold") is False and d.get("consumed") == d.get("success_count"),
          "成功 %s, 库存减少 %s, oversold=%s" % (
              d.get("success_count"), d.get("consumed"), d.get("oversold")))
    check("    逐次耗时已写入 access_log", d.get("log_count") == d.get("clients"),
          "本次写入 %s 条日志" % d.get("log_count"))
else:
    check("11) 10 终端并发出库", False, str(r.get("error"))[:60])

st, r = call("GET", "/api/access_logs")
logs = (r.get("data") or {}).get("rows") or []
check("    日志表可查（JOIN 药品名）", r.get("ok") and len(logs) > 0,
      "共 %s 条，最近 1 条药品=%s" % (
          (r.get("data") or {}).get("total"),
          logs[0].get("drug_name") if logs else "—"))

print()
print("=" * 72)
print("H. 索引对比")
print("=" * 72)
for case, extra in [("movement_by_drug", {"drug_id": tid}),
                    ("movement_by_date", {"since": (datetime.date.today()
                                                    - datetime.timedelta(days=7)).isoformat()})]:
    st, r = call("POST", "/api/perf", dict(case=case, repeat=3, **extra))
    d = r.get("data") or {}
    w, wo = d.get("with", {}), d.get("without", {})
    check("12) 索引对比 %s" % case, r.get("ok") and d.get("speedup") is not None,
          "走索引 %s ms / 全表 %s ms / 提速 %sx / type %s vs %s" % (
              w.get("time", {}).get("avg_ms"), wo.get("time", {}).get("avg_ms"),
              d.get("speedup"), w.get("plan", {}).get("type"), wo.get("plan", {}).get("type")))

print()
print("=" * 72)
print("结果：通过 %d 项，失败 %d 项" % (ok_n, fail_n))
print("=" * 72)
sys.exit(0 if fail_n == 0 else 1)
