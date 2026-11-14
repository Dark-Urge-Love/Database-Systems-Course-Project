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

【知识点：为什么要有 Service 层】
页面（views）只负责"收参数、调函数、返回结果"，SQL 在 dao，连接在 db。
业务规则（库存够不够、批次怎么扣、几张表要一起改）集中放在这一层，
叫"业务逻辑层"。这样同一套规则既能给网页用，也能给脚本/压测复用 ——
下面的 concurrency_test.py 就是直接调 sell_out 的。
"""

import random
import statistics
import threading
import time
from datetime import date, datetime
from . import dao
from .db import transaction, BizError, get_cursor


# 生成业务单号
def _no(prefix):
    """生成单号：前缀 + 日期 + 时分秒 + 2 位随机，避免并发下重号。"""
    now = datetime.now()

    # 【知识点：strftime 格式化时间】
    # %Y%m%d → 20261110；%H%M%S → 152033
    # %02d 表示"整数至少占 2 位，不够前面补 0"（randint(0,99) 里的 7 会变成 "07"）
    # 拼出来的效果：PO2026111015203317 —— 带秒 + 随机数，同一秒里也很难重复
    return "%s%s%s%02d" % (prefix, now.strftime("%Y%m%d"), now.strftime("%H%M%S"),
                           random.randint(0, 99))


# T1  采购入库
def receive_purchase(supplier_id, warehouse_id, employee_id, drug_id,
                     qty, unit_price, batch_no=None, prod_date=None, expiry_date=None):
    """
    库房到货登记：一张采购单 → 明细 → 入库批次 → 库存增加 → 库存流水。
    """

    # 生成本次采购单号，前缀 PO（Purchase Order）
    po_no = _no("PO")

    today = date.today()

    # 没传批号就自动编一个：B + 年月日(2位年) + 3 位随机数
    if not batch_no:
        batch_no = "B%s%03d" % (today.strftime("%y%m%d"), random.randint(1, 999))

    # 【知识点：with transaction() as cur】从这里开始进入一个事务，
    # 块里所有 SQL 要么一起成功（COMMIT），要么一起撤销（ROLLBACK）。
    with transaction() as cur:
        # ① 药品必须存在且在售
        # %s 是占位符，(drug_id,) 是参数元组 —— 注意单参数也必须带逗号，否则只是括号不是元组
        cur.execute("SELECT drug_id, drug_name, spec, unit, shelf_life_months "
                    "FROM drug WHERE drug_id = %s AND status = 1", (drug_id,))
        # 查到的数据存在游标里面，还没到python变量里
        # 取回一行（DictCursor，所以 drug 是字典）
        drug = cur.fetchone()

        # 查不到就抛业务异常。抛出后 transaction() 会捕获并 ROLLBACK，整笔不做
        if not drug:
            raise BizError("药品不存在或已停用，请刷新后重选")

        # 金额 = 数量 × 单价，round(...,2) 保留两位小数（金额不能有分以下的数）
        amount = round(qty * unit_price, 2)

        # ② 采购单（状态直接为已入库，本演示不做"先下单后到货"的两阶段）
        # 注意 SQL 里 '已入库' 是写死的字符串常量，不是占位符（它不是用户输入）
        cur.execute(
            "INSERT INTO purchase_order (po_no, supplier_id, warehouse_id, employee_id, "
            "order_date, expect_date, total_amount, status) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, '已入库')",
            (po_no, supplier_id, warehouse_id, employee_id, today, today, amount))

        # 【知识点：lastrowid】INSERT 之后，cur.lastrowid 就是刚插入那行的自增主键值。
        po_id = cur.lastrowid

        # ③ 采购明细（批号 / 生产日期 / 有效期）
        cur.execute(
            "INSERT INTO purchase_item (po_id, drug_id, quantity, unit_price, amount, "
            "batch_no, production_date, expiry_date) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (po_id, drug_id, qty, unit_price, amount, batch_no, prod_date, expiry_date))

        # 拿到明细行的自增主键，下一步建批次要用
        item_id = cur.lastrowid

        # ④ 入库批次（与采购明细 1-1）
        # 一个采购明细对应一个库存批次，这是"1-1 联系"的物理落点：
        # stock_batch.purchase_item_id 上有唯一约束，从库结构上保证不会一分为二
        cur.execute(
            "INSERT INTO stock_batch (purchase_item_id, drug_id, warehouse_id, batch_no, "
            "production_date, expiry_date, quantity, purchase_price, in_date) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (item_id, drug_id, warehouse_id, batch_no, prod_date, expiry_date,
             qty, unit_price, today))

        # ⑤ 库存：有则加，无则建（先锁行，保证并发到货时不会互相覆盖）
        # 【知识点：FOR UPDATE 排他锁（行锁）】
        # 加了它，这一行就被当前事务"锁住"，别的事务想改这一行必须先等它提交或回滚。
        # 为什么"先查再加"要加锁？如果不锁：两个采购同时到货，
        # 都读到库存=10，都写 10+5=15，结果少加了 5。—— 这叫"丢失更新"
        cur.execute("SELECT stock_id, quantity FROM stock "
                    "WHERE drug_id = %s AND warehouse_id = %s FOR UPDATE",
                    (drug_id, warehouse_id))

        # 取这行库存记录
        row = cur.fetchone()

        # 已有库存记录 → 累加
        if row:
            # 变动前的库存，后面写流水要用
            before = row["quantity"]
            # 【知识点：quantity = quantity + %s 这种写法】
            # 让数据库自己去取当前值再加，比"先读到 Python 里算好再写回"更安全，
            # 因为"读-算-写"在数据库内部是一条语句，不会被别的会话插队
            cur.execute("UPDATE stock SET quantity = quantity + %s, update_time = NOW() "
                        "WHERE stock_id = %s", (qty, row["stock_id"]))

        # 没有库存记录（这个药第一次进这个仓库）→ 新建一行
        else:
            before = 0
            # safety_stock 安全库存先给个默认值 50
            cur.execute("INSERT INTO stock (drug_id, warehouse_id, quantity, safety_stock, "
                        "update_time) VALUES (%s, %s, %s, 50, NOW())",
                        (drug_id, warehouse_id, qty))

        # 变动后 = 变动前 + 入库量
        after = before + qty

        # ⑥ 库存流水：正数入库
        # 【知识点：流水表为什么要存 before/after】
        # 流水表是"账本"，每一笔都记下当时的前后余额，事后能逐笔核对。
        # 数据库里还有 CHECK 约束保证 after_qty = before_qty + change_qty，
        # 写错了直接被数据库拒绝 —— 这是数据一致性的最后一道防线
        cur.execute(
            "INSERT INTO stock_movement (drug_id, warehouse_id, move_type, ref_no, "
            "change_qty, before_qty, after_qty, batch_no, employee_id, move_time) "
            "VALUES (%s, %s, '采购入库', %s, %s, %s, %s, %s, %s, NOW())",
            (drug_id, warehouse_id, po_no, qty, before, after, batch_no, employee_id))

        # 【知识点：return 放在 with 块里】Python 执行 return 前会先"退出 with 块"，
        # transaction() 的 else 分支照样执行 → 正常提交，然后把字典交出去
        return {
            "po_no": po_no, "batch_no": batch_no, "amount": amount,
            "drug_name": drug["drug_name"], "spec": drug["spec"], "unit": drug["unit"],
            "qty": qty, "stock_before": before, "stock_after": after,
        }


# T2  销售出库
def sell_out(warehouse_id, employee_id, drug_id, qty, customer_name, customer_type="连锁药店"):
    """
    销售出库：锁定库存 → 校验余额 → 按批次近效期先出扣减 → 出库单 + 明细 + 流水。
    """
    # 生成销售单号，前缀 SO（Sale Order）
    so_no = _no("SO")
    today = date.today()

    # 开事务：下面所有操作必须同生共死
    with transaction() as cur:

        # 先查药品信息（名称、规格、零售价）
        cur.execute("SELECT drug_id, drug_name, spec, unit, retail_price "
                    "FROM drug WHERE drug_id = %s", (drug_id,))

        drug = cur.fetchone()

        # 药品不存在 → 抛异常，整笔回滚
        if not drug:
            raise BizError("药品不存在")

        # ① 锁住该药品在该仓库的库存行
        # 【知识点：这就是"防超卖"的关键】
        # 10 个终端同时卖同一种药，第一个进来的事务锁住这行，其余 9 个阻塞等待；
        # 等第一个提交（库存已减），第二个才拿到锁，然后读到的是"减过之后"的最新库存。
        # 所以每个事务看到的库存都是真实的剩余量，不会出现两个人都以为还剩 1 件的情况
        cur.execute("SELECT stock_id, quantity FROM stock "
                    "WHERE drug_id = %s AND warehouse_id = %s FOR UPDATE",
                    (drug_id, warehouse_id))

        # 取库存行
        row = cur.fetchone()

        # 没有库存记录，说明这个仓库压根没进过这个药
        if not row:
            raise BizError("该仓库没有这种药品的库存，请先做采购入库")

        # 变动前库存
        before = row["quantity"]

        # 【知识点：检查必须放在锁内】
        # 如果先查库存、再判断、再扣减，中间不留锁，两个事务就可能都通过检查。
        # 现在"查-判断-扣"都在同一把行锁的保护下，是一个原子区间，所以判断才可靠
        if before < qty:
            raise BizError("库存不足：该仓库只剩 %d %s" % (before, drug["unit"] or ""))

        # 零售价转成浮点数（数据库返回的 Decimal 类型，和 float 相乘会报错，先转）
        price = float(drug["retail_price"])

        # 销售额 = 数量 × 单价
        amount = round(qty * price, 2)

        # ② 批次近效期先出：锁住仍在有效期内的批次，按到期日升序扣减；
        #    已过期批次不参与出库（等"效期预警"报表提示后做下架处理）
        # 【知识点：FEFO近效期先出】
        # 药品行业不能随便先出，必须"快过期的先卖"。实现方式就是 ORDER BY expiry_date 升序
        # 这里同样用 FOR UPDATE 锁住这批行，防止两个终端扣同一个批次扣成负数
        cur.execute("SELECT batch_id, batch_no, quantity FROM stock_batch "
                    "WHERE drug_id = %s AND warehouse_id = %s AND quantity > 0 "
                    "AND expiry_date > CURDATE() "
                    "ORDER BY expiry_date, batch_id FOR UPDATE",
                    (drug_id, warehouse_id))

        # fetchall 取出所有符合条件的批次（已按到期日从近到远排好）
        batches = cur.fetchall()

        # left = "还差多少件没扣完"，一开始就是要卖的数量
        left = qty
        # 记录实际用到的第一个批号（写流水时要填）
        used_batch = None

        # 挨个批次扣，直到扣够为止
        for b in batches:
            # 【知识点：提前跳出循环】已经扣够了就不用再扣后面的批次
            if left <= 0:
                break
            # 这个批次最多能贡献多少：自身库存 和 还差的量 取小的那个
            take = min(b["quantity"], left)
            # 扣这个批次的库存
            cur.execute("UPDATE stock_batch SET quantity = quantity - %s WHERE batch_id = %s",
                        (take, b["batch_id"]))

            # 只在第一次扣的时候记批号（代表"主要从哪批出的"）
            if used_batch is None:
                used_batch = b["batch_no"]

            # 还差多少要减掉已经扣走的
            left -= take

        # 扣完所有批次还有剩 → 说明批次账上的总数比库存账少（数据被人手动改过才会出现）
        batch_short = left > 0        # 批次账不足（人为改过库存时才会出现）

        # ③ 出库单 + 明细
        cur.execute(
            "INSERT INTO sale_order (so_no, customer_name, customer_type, warehouse_id, "
            "employee_id, sale_date, total_amount, status) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, '已出库')",
            (so_no, customer_name, customer_type, warehouse_id, employee_id, today, amount))

        # 拿到出库单的自增主键，明细表要填
        so_id = cur.lastrowid

        # 插入销售明细（一个药品一行；多药品就是多条明细，构成"单-明细"的 1-N 关系）
        cur.execute(
            "INSERT INTO sale_item (so_id, drug_id, quantity, unit_price, amount) "
            "VALUES (%s, %s, %s, %s, %s)", (so_id, drug_id, qty, price, amount))

        # ④ 扣减库存
        cur.execute("UPDATE stock SET quantity = quantity - %s, update_time = NOW() "
                    "WHERE stock_id = %s", (qty, row["stock_id"]))

        # 变动后库存
        after = before - qty

        # ⑤ 库存流水：负数出库
        # change_qty 传 -qty（负数），配合 CHECK 约束 after = before + change 自动成立
        cur.execute(
            "INSERT INTO stock_movement (drug_id, warehouse_id, move_type, ref_no, "
            "change_qty, before_qty, after_qty, batch_no, employee_id, move_time) "
            "VALUES (%s, %s, '销售出库', %s, %s, %s, %s, %s, %s, NOW())",
            (drug_id, warehouse_id, so_no, -qty, before, after, used_batch, employee_id))

        # 返回本次出库的结果（页面拿它显示"出库成功、库存 8→7、用了哪个批次"）
        return {
            "so_no": so_no, "amount": amount, "drug_name": drug["drug_name"],
            "spec": drug["spec"], "unit": drug["unit"], "qty": qty,
            "stock_before": before, "stock_after": after,
            "batch_no": used_batch, "batch_short": batch_short,
        }


# T2' 并发出库（多终端同时卖同一种药）
def sale_concurrency(drug_id, warehouse_id, clients=10, qty=1, reset_stock=None):
    """
    多客户端并发出库演示。
    · reset_stock 不为空时，演示前把该仓库库存改回这个数（同时对齐批次，保证账实一致）
    · 用 Barrier 让所有终端卡在同一刻发起，尽量制造真实的并发竞争
    · 逐个记录每个终端的响应耗时，演示结束后统一写入 access_log 表
      （日志放在业务事务之外写，避免日志插入反过来拖慢被测的操作）
    """
    # 如果调用方要求"演示前把库存重置成某个数"，先做这一步
    if reset_stock is not None:

        # 重置库存也是改数据，走事务
        with transaction() as cur:

            # 先锁住这行，避免重置的同时有人在卖
            cur.execute("SELECT stock_id FROM stock WHERE drug_id = %s AND warehouse_id = %s "
                        "FOR UPDATE", (drug_id, warehouse_id))

            # 取库存行
            row = cur.fetchone()

            # 有记录 → 直接改数量
            if row:
                cur.execute("UPDATE stock SET quantity = %s, update_time = NOW() "
                            "WHERE stock_id = %s", (reset_stock, row["stock_id"]))

            # 没记录 → 新建一行
            else:
                cur.execute("INSERT INTO stock (drug_id, warehouse_id, quantity, "
                            "safety_stock, update_time) VALUES (%s, %s, %s, 50, NOW())",
                            (drug_id, warehouse_id, reset_stock))

            # 批次与库存对齐：清空后把最近一个批次的数量设为重置值
            # （因为出库要扣批次，如果只改库存不改批次，就会出现"批次账比库存账少"的假象）
            cur.execute("SELECT batch_id FROM stock_batch WHERE drug_id = %s "
                        "AND warehouse_id = %s ORDER BY expiry_date DESC, batch_id DESC LIMIT 1",
                        (drug_id, warehouse_id))

            # 取最后那个批次
            b = cur.fetchone()

            # 有批次才需要对齐
            if b:

                # 先把所有批次清零
                cur.execute("UPDATE stock_batch SET quantity = 0 "
                            "WHERE drug_id = %s AND warehouse_id = %s",
                            (drug_id, warehouse_id))

                # 再把其中一个批次的库存设成重置值（这样"库存 = 所有批次之和"依然成立）
                cur.execute("UPDATE stock_batch SET quantity = %s WHERE batch_id = %s",
                            (reset_stock, b["batch_id"]))

    # 查药品信息 + 当前库存 + 仓库名，用于页面显示和结果统计
    with get_cursor() as cur:
        cur.execute("SELECT d.drug_id, d.drug_name, d.spec, d.unit, "
                    "COALESCE(s.quantity, 0) AS stock, w.warehouse_name "
                    "FROM drug d "
                    # 注意这里 LEFT JOIN 的条件写了 s.warehouse_id = %s，
                    # 意思是"只关联指定仓库的库存"，其他仓库忽略
                    "LEFT JOIN stock s ON s.drug_id = d.drug_id AND s.warehouse_id = %s "
                    "JOIN warehouse w ON w.warehouse_id = %s "
                    "WHERE d.drug_id = %s", (warehouse_id, warehouse_id, drug_id))

        # 药品信息
        drug = cur.fetchone()

        # 随便挑一个该仓库的销售员当经办人（LIMIT 1 只取第一条）
        cur.execute("SELECT employee_id FROM employee WHERE position = '销售员' "
                    "AND warehouse_id = %s LIMIT 1", (warehouse_id,))

        # 销售员
        emp = cur.fetchone()

    # 药品不存在，直接报错
    if not drug:
        raise BizError("药品不存在")

    # 没销售员也没法出库
    if not emp:
        raise BizError("该仓库还没有销售员，无法出库")

    # 演示开始前的库存（用于对比"卖了多少"）
    stock_before = drug["stock"]

    # 【知识点：Barrier 栅栏】
    # 创建一道"栅栏"，要求正好 clients 个线程都到齐了，才允许所有线程同时往下走。
    # 这样 10 个线程几乎在同一微秒发起请求，竞争最激烈 —— 比"依次启动"更能暴露并发问题
    barrier = threading.Barrier(clients)

    # 【知识点：Lock 互斥锁】多个线程要往同一个 list 里 append，必须加锁，
    # 否则可能同时写导致数据错乱（list 不是线程安全的）
    lock = threading.Lock()

    # 收集每个终端的结果
    detail = []

    # 【知识点：闭包】在函数内部再定义函数，内部的 worker 可以直接用外面的变量
    # （warehouse_id、drug_id、barrier、detail 等）。target=worker 交给 Thread 去跑
    def worker(idx):

        # 先到栅栏前排队等待，等所有人都到齐
        try:
            barrier.wait(timeout=10)

        # 万一有线程迟迟不来（超时），栅栏会"破裂"，这里把异常吞掉，继续执行
        except threading.BrokenBarrierError:
            pass

        # 开始掐表（perf_counter 单位是秒，精度很高）
        t0 = time.perf_counter()

        # 先默认成功
        ok, reason = True, ""

        # 真正做一次销售出库（和页面上点"销售出库"走的是同一个函数）
        try:
            sell_out(warehouse_id, emp["employee_id"], drug_id, qty,
                     "并发演示客户-%02d" % (idx + 1), "个人")

        # 业务失败（比如库存不足）—— 这是预期内的结果，不是程序错误
        except BizError as e:
            ok, reason = False, str(e)

        # 其他异常（程序 bug、数据库报错）
        except Exception as e:                     # noqa: BLE001
            ok, reason = False, "系统异常：" + str(e)

        # 结束计时，换算成毫秒
        elapsed = (time.perf_counter() - t0) * 1000

        # 加锁后再写共享列表，防止多线程同时写
        with lock:
            detail.append({"client": idx + 1, "ok": ok,
                           "elapsed_ms": round(elapsed, 2), "reason": reason})

    # 整个演示的总计时开始
    t_start = time.perf_counter()

    # 【知识点：列表推导式】一句话造出 10 个线程对象，每个线程 target=worker、参数是它的序号
    threads = [threading.Thread(target=worker, args=(i,)) for i in range(clients)]

    # 逐个启动线程（start 之后线程真正跑起来，主线程继续往下走，不等待）
    for t in threads:
        t.start()

    # 【知识点：join 等待线程结束】主线程在这里等每个子线程跑完，最多等 60 秒
    for t in threads:
        t.join(timeout=60)

    # 总耗时（毫秒）
    elapsed_ms = (time.perf_counter() - t_start) * 1000

    # 查一下卖完之后的库存
    with get_cursor() as cur:
        cur.execute("SELECT quantity FROM stock WHERE drug_id = %s AND warehouse_id = %s",
                    (drug_id, warehouse_id))

        # 取结果
        r = cur.fetchone()

        # 有记录就用它的数量，查不到就当 0
        stock_after = r["quantity"] if r else 0

    # 按终端序号排序，页面显示更整齐
    # 【知识点：lambda】匿名小函数，这里表示"按字典里的 client 字段排序"
    detail.sort(key=lambda x: x["client"])

    # 把所有终端的耗时抽成一个列表
    times = [d["elapsed_ms"] for d in detail]

    # 【知识点：sum(1 for ... if ...)】统计满足条件的个数 —— 数有多少个终端成功了
    success = sum(1 for d in detail if d["ok"])

    # 失败数 = 总数 - 成功数
    failed = len(detail) - success

    # 把每个终端的访问记录写进 access_log 表（这就是"记录访问性能"的落地）
    # 1 if d["ok"] else 0 把布尔值转成数据库里的 0/1
    dao.log_access_batch([("sale_out", d["client"], drug_id, qty,
                           1 if d["ok"] else 0, d["elapsed_ms"]) for d in detail])

    # 汇总结果返回给页面
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
        # 实际卖掉的件数
        "consumed": stock_before - stock_after,
        # 【知识点：怎么判断"超卖"】两种情况说明出错：
        #   ① 库存被扣成负数
        #   ② 成功次数 × 每单数量 ≠ 实际减少的库存（说明有笔成功了但没扣到库存）
        # 只要有一项成立，oversold 就是 True，页面会红字告警
        "oversold": (stock_after < 0) or (success * qty != stock_before - stock_after),
        # 总耗时
        "elapsed_ms": round(elapsed_ms, 2),
        # statistics.mean 求平均值
        "avg_ms": round(statistics.mean(times), 2) if times else 0,
        # min / max 取最小最大耗时
        "min_ms": round(min(times), 2) if times else 0,
        "max_ms": round(max(times), 2) if times else 0,
        # 【知识点：P95 分位数】把耗时从大到小排好，取第 95% 位置的那个值，
        # 意思是"95% 的请求都比这个快"。比平均值更能反映"最慢的那批用户体验"
        "p95_ms": round(sorted(times)[int(len(times) * 0.95) - 1], 2) if times else 0,
        # QPS：每秒完成的请求数 = 请求数 ÷ 秒数
        "qps": round(len(detail) / (elapsed_ms / 1000), 1) if elapsed_ms > 0 else 0,
        # 每个终端的明细（页面表格要显示）
        "detail": detail,
        # 一共写了几条日志
        "log_count": len(detail),
    }
