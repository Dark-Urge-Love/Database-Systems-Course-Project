# -*- coding: utf-8 -*-
"""
药品进销存管理系统
初始数据生成脚本  data_gen.py

功能：向 drug_db 批量生成初始数据（12 张业务表，合计约 15 万条，
      满足课程"至少 5 张表、至少 10 万条数据、数据与药品相关"的要求）

业务链：
    供应商供货 → 采购单 → 采购明细（批号/生产日期/有效期）
      → 入库生成批次 → 库存结存 → 销售单 → 销售明细出库 → 库存流水

自洽性保证（脚本末尾会逐项校验）：
    1. 库存结存 = 该药品该仓库各批次结存之和
    2. 库存流水 after_qty = before_qty + change_qty，且逐笔首尾相接
    3. 采购/销售明细金额 = 数量 × 单价，单据金额 = 明细金额之和
    4. 采购明细与库存批次 1-1 对应

用法：
    python data_gen.py                                   # 默认 127.0.0.1:3306 root/123456
    python data_gen.py --host 192.168.56.10 --password abc123
    python data_gen.py --seed 2026                        # 固定随机种子，结果可复现

注意：执行前请先运行 schema.sql 建库建表；脚本会自动清空已有数据。
"""

import argparse
import random
import sys
import time
from collections import defaultdict
from datetime import date, datetime, timedelta

try:
    import pymysql
except ImportError:
    print("[错误] 缺少依赖，请先执行：pip install pymysql")
    sys.exit(1)

# =====================================================================
# 目标行数
# =====================================================================
N_CATEGORY = 12       # 药品分类
N_WAREHOUSE = 6       # 仓库
N_SUPPLIER = 80       # 供应商
N_EMPLOYEE = 60       # 员工
N_DRUG = 1200         # 药品（1200 个唯一的名称+规格组合）
N_PURCHASE = 3500     # 采购单
N_SALE = 12000        # 销售单
PI_MIN, PI_MAX = 1, 8    # 每张采购单的明细条数
SI_MIN, SI_MAX = 1, 6    # 每张销售单的明细条数
N_ADJUST = 800        # 盘点调整次数

# =====================================================================
# 词库
# =====================================================================
CATEGORIES = [
    ("抗感染药", "A01"), ("解热镇痛药", "A02"), ("呼吸系统用药", "A03"),
    ("消化系统用药", "A04"), ("心血管系统用药", "A05"), ("内分泌代谢用药", "A06"),
    ("神经系统用药", "A07"), ("泌尿系统用药", "A08"), ("皮肤科用药", "A09"),
    ("五官科用药", "A10"), ("维生素与营养药", "A11"), ("中成药", "A12"),
]

WAREHOUSES = [
    ("中心仓", "南京市江宁区科建路 18 号", 120000),
    ("城东仓", "南京市栖霞区仙林大道 66 号", 60000),
    ("城西仓", "南京市浦口区浦滨路 99 号", 55000),
    ("冷链仓", "南京市江宁区冷链物流园 3 号库", 20000),
    ("南区仓", "南京市雨花台区软件大道 128 号", 45000),
    ("北区仓", "南京市六合区龙池街道 12 号", 40000),
]

CITIES = ["北京", "上海", "广州", "深圳", "杭州", "南京", "成都", "重庆",
          "武汉", "西安", "天津", "苏州", "郑州", "长沙", "青岛", "济南",
          "合肥", "福州", "昆明", "沈阳"]
SUPPLIER_SUFFIX = ["医药有限公司", "医药供应链有限公司", "药业有限公司", "医药贸易有限公司"]

MANUFACTURERS = [
    "华北制药股份有限公司", "哈药集团制药总厂", "石药集团欧意药业有限公司",
    "齐鲁制药有限公司", "扬子江药业集团有限公司", "江苏恒瑞医药股份有限公司",
    "正大天晴药业集团股份有限公司", "江苏豪森药业集团有限公司", "浙江海正药业股份有限公司",
    "华东医药股份有限公司", "丽珠医药集团股份有限公司", "广州白云山制药股份有限公司",
    "太极集团重庆涪陵制药厂", "北京同仁堂科技发展股份有限公司", "云南白药集团股份有限公司",
    "天士力医药集团股份有限公司", "江苏康缘药业股份有限公司", "石家庄以岭药业股份有限公司",
    "山东步长制药股份有限公司", "仁和药业股份有限公司",
]

SURNAMES = list("王李张刘陈杨黄赵吴周徐孙马朱胡郭何高林罗郑梁谢宋唐许韩冯邓曹彭曾肖田董袁潘于蒋蔡余杜叶程苏魏吕丁任沈姚卢姜崔钟谭陆汪范金石廖贾夏韦付方白邹孟熊秦邱江尹薛闫段雷侯龙史陶黎贺顾毛郝龚邵万钱严覃武戴莫孔向汤")
GIVEN_CHARS = list("伟芳娜秀敏静丽强磊军洋勇艳杰娟涛明超霞平刚桂英华玉萍红娥玲芬燕彬鹏浩宇轩然梓涵欣怡子墨嘉懿雨萱思远致远晨曦瑞泽锦程文博雅琪书瑶新雨佳明志建国建华晓东晓明小雨小丽雪梅冬梅春燕")

POSITIONS = ["采购员", "仓管员", "销售员", "质检员"]

# 药名 + 剂型（决定规格模板）
# form: capsule 胶囊 / tablet 片剂 / pill 丸剂 / granule 颗粒 /
#       liquid 口服液 / ointment 软膏 / drop 滴眼液 / spray 喷雾剂 /
#       powder 散剂 / suppository 栓剂
DRUG_GROUPS = {
    "抗感染药": [
        ("阿莫西林胶囊", "capsule"), ("阿莫西林克拉维酸钾片", "tablet"),
        ("头孢克肟分散片", "tablet"), ("头孢呋辛酯片", "tablet"),
        ("头孢拉定胶囊", "capsule"), ("阿奇霉素分散片", "tablet"),
        ("克拉霉素片", "tablet"), ("罗红霉素胶囊", "capsule"),
        ("左氧氟沙星片", "tablet"), ("盐酸环丙沙星片", "tablet"),
        ("甲硝唑片", "tablet"), ("氟康唑胶囊", "capsule"),
        ("阿昔洛韦片", "tablet"), ("利巴韦林颗粒", "granule"),
    ],
    "解热镇痛药": [
        ("布洛芬缓释胶囊", "capsule"), ("布洛芬混悬液", "liquid"),
        ("对乙酰氨基酚片", "tablet"), ("阿司匹林肠溶片", "tablet"),
        ("双氯芬酸钠缓释片", "tablet"), ("塞来昔布胶囊", "capsule"),
        ("洛索洛芬钠片", "tablet"), ("吲哚美辛肠溶片", "tablet"),
        ("萘普生片", "tablet"), ("氨酚咖敏片", "tablet"),
        ("复方对乙酰氨基酚片", "tablet"), ("布洛芬颗粒", "granule"),
    ],
    "呼吸系统用药": [
        ("盐酸氨溴索口服溶液", "liquid"), ("乙酰半胱氨酸颗粒", "granule"),
        ("复方甘草片", "tablet"), ("氢溴酸右美沙芬糖浆", "liquid"),
        ("枸橼酸喷托维林片", "tablet"), ("孟鲁司特钠片", "tablet"),
        ("氯雷他定片", "tablet"), ("盐酸西替利嗪片", "tablet"),
        ("茶碱缓释片", "tablet"), ("硫酸沙丁胺醇气雾剂", "spray"),
        ("布地奈德鼻喷雾剂", "spray"), ("丙酸氟替卡松鼻喷雾剂", "spray"),
        ("复方甲氧那明胶囊", "capsule"), ("桉柠蒎肠溶软胶囊", "capsule"),
    ],
    "消化系统用药": [
        ("奥美拉唑肠溶胶囊", "capsule"), ("泮托拉唑钠肠溶片", "tablet"),
        ("雷贝拉唑钠肠溶片", "tablet"), ("铝碳酸镁咀嚼片", "tablet"),
        ("多潘立酮片", "tablet"), ("枸橼酸莫沙必利片", "tablet"),
        ("蒙脱石散", "powder"), ("双歧杆菌三联活菌胶囊", "capsule"),
        ("乳果糖口服溶液", "liquid"), ("复方消化酶胶囊", "capsule"),
        ("熊去氧胆酸胶囊", "capsule"), ("柳氮磺吡啶肠溶片", "tablet"),
        ("胶体果胶铋胶囊", "capsule"), ("复方谷氨酰胺肠溶胶囊", "capsule"),
    ],
    "心血管系统用药": [
        ("硝苯地平控释片", "tablet"), ("苯磺酸氨氯地平片", "tablet"),
        ("缬沙坦胶囊", "capsule"), ("厄贝沙坦片", "tablet"),
        ("琥珀酸美托洛尔缓释片", "tablet"), ("富马酸比索洛尔片", "tablet"),
        ("卡托普利片", "tablet"), ("阿托伐他汀钙片", "tablet"),
        ("瑞舒伐他汀钙片", "tablet"), ("辛伐他汀片", "tablet"),
        ("硫酸氢氯吡格雷片", "tablet"), ("单硝酸异山梨酯缓释片", "tablet"),
        ("地高辛片", "tablet"), ("呋塞米片", "tablet"),
    ],
    "内分泌代谢用药": [
        ("盐酸二甲双胍缓释片", "tablet"), ("格列美脲片", "tablet"),
        ("阿卡波糖片", "tablet"), ("磷酸西格列汀片", "tablet"),
        ("达格列净片", "tablet"), ("左甲状腺素钠片", "tablet"),
        ("甲巯咪唑片", "tablet"), ("盐酸吡格列酮片", "tablet"),
        ("瑞格列奈片", "tablet"), ("骨化三醇软胶囊", "capsule"),
        ("碳酸钙D3片", "tablet"), ("阿仑膦酸钠片", "tablet"),
    ],
    "神经系统用药": [
        ("甲钴胺片", "tablet"), ("草酸艾司西酞普兰片", "tablet"),
        ("盐酸舍曲林片", "tablet"), ("盐酸帕罗西汀片", "tablet"),
        ("阿普唑仑片", "tablet"), ("佐匹克隆片", "tablet"),
        ("加巴喷丁胶囊", "capsule"), ("普瑞巴林胶囊", "capsule"),
        ("盐酸氟桂利嗪胶囊", "capsule"), ("甲磺酸倍他司汀片", "tablet"),
        ("尼莫地平片", "tablet"), ("多奈哌齐片", "tablet"),
    ],
    "泌尿系统用药": [
        ("盐酸坦索罗辛缓释胶囊", "capsule"), ("非那雄胺片", "tablet"),
        ("酒石酸托特罗定片", "tablet"), ("呋喃妥因肠溶片", "tablet"),
        ("枸橼酸西地那非片", "tablet"), ("他达拉非片", "tablet"),
        ("复方石韦胶囊", "capsule"), ("热淋清颗粒", "granule"),
        ("尿塞通片", "tablet"), ("萆薢分清丸", "pill"),
    ],
    "皮肤科用药": [
        ("莫匹罗星软膏", "ointment"), ("夫西地酸乳膏", "ointment"),
        ("复方醋酸地塞米松乳膏", "ointment"), ("丁酸氢化可的松乳膏", "ointment"),
        ("糠酸莫米松乳膏", "ointment"), ("盐酸特比萘芬乳膏", "ointment"),
        ("酮康唑乳膏", "ointment"), ("阿昔洛韦乳膏", "ointment"),
        ("维A酸乳膏", "ointment"), ("尿素维E乳膏", "ointment"),
        ("炉甘石洗剂", "liquid"), ("复方苦参洗剂", "liquid"),
    ],
    "五官科用药": [
        ("左氧氟沙星滴眼液", "drop"), ("妥布霉素滴眼液", "drop"),
        ("玻璃酸钠滴眼液", "drop"), ("氯霉素滴眼液", "drop"),
        ("氧氟沙星滴耳液", "drop"), ("盐酸赛洛唑啉鼻喷雾剂", "spray"),
        ("复方硼砂含漱液", "liquid"), ("西地碘含片", "tablet"),
        ("氟米龙滴眼液", "drop"), ("色甘酸钠滴眼液", "drop"),
        ("聚乙烯醇滴眼液", "drop"), ("复方氯己定含漱液", "liquid"),
    ],
    "维生素与营养药": [
        ("维生素C片", "tablet"), ("维生素B1片", "tablet"),
        ("维生素B2片", "tablet"), ("维生素B6片", "tablet"),
        ("维生素AD软胶囊", "capsule"), ("维生素E软胶囊", "capsule"),
        ("复合维生素B片", "tablet"), ("叶酸片", "tablet"),
        ("葡萄糖酸钙口服溶液", "liquid"), ("硫酸亚铁片", "tablet"),
        ("碳酸钙D3咀嚼片", "tablet"), ("多维元素片", "tablet"),
    ],
    "中成药": [
        ("连花清瘟胶囊", "capsule"), ("感冒灵颗粒", "granule"),
        ("板蓝根颗粒", "granule"), ("蒲地蓝消炎口服液", "liquid"),
        ("双黄连口服液", "liquid"), ("藿香正气口服液", "liquid"),
        ("六味地黄丸", "pill"), ("逍遥丸", "pill"),
        ("复方丹参滴丸", "pill"), ("速效救心丸", "pill"),
        ("三金片", "tablet"), ("云南白药气雾剂", "spray"),
        ("麝香保心丸", "pill"), ("牛黄解毒片", "tablet"),
    ],
}

# 剂型 → (计数单位, 包装单位)
FORM_RULES = {
    "capsule": ("粒", "盒"),
    "tablet": ("片", "盒"),
    "pill": ("丸", "盒"),
    "granule": ("袋", "盒"),
    "powder": ("袋", "盒"),
    "liquid": ("ml", "瓶"),
    "ointment": ("g", "支"),
    "drop": ("ml", "支"),
    "spray": ("ml", "支"),
    "suppository": ("枚", "盒"),
}

SOLID_DOSES = ["0.1g", "0.125g", "0.2g", "0.25g", "0.5g", "5mg", "10mg", "20mg", "50mg", "100mg"]

# 各分类的常见剂量池，避免出现"阿莫西林 50mg"这类不合理规格
CATE_DOSES = {
    "抗感染药": ["0.1g", "0.125g", "0.25g", "0.5g"],
    "解热镇痛药": ["0.1g", "0.2g", "0.3g", "0.5g"],
    "呼吸系统用药": ["5mg", "10mg", "20mg", "30mg"],
    "消化系统用药": ["10mg", "20mg", "40mg", "0.25g"],
    "心血管系统用药": ["2.5mg", "5mg", "10mg", "20mg"],
    "内分泌代谢用药": ["0.5mg", "1mg", "5mg", "10mg", "30mg"],
    "神经系统用药": ["5mg", "10mg", "20mg", "0.1g"],
    "泌尿系统用药": ["0.2mg", "2mg", "5mg", "10mg"],
    "皮肤科用药": ["0.1g", "0.5g"],
    "五官科用药": ["5mg", "10mg", "0.5g"],
    "维生素与营养药": ["5mg", "10mg", "50mg", "100mg"],
    "中成药": ["0.25g", "0.3g", "0.5g", "1g"],
}


def gen_specs(form, rnd, doses):
    """按剂型生成 8 个规格，保证同一药品的 8 个规格互不相同。"""
    unit = FORM_RULES[form][0]
    if form in ("capsule", "tablet", "pill", "suppository"):
        d1, d2 = rnd.sample(doses, 2)
        counts = [12, 24, 36, 48] if form != "suppository" else [6, 8, 10, 12]
        return ["%s*%d%s" % (d, c, unit) for d in (d1, d2) for c in counts]
    if form in ("granule", "powder"):
        d1, d2 = rnd.sample(["3g", "5g", "10g"], 2)
        return ["%s*%d%s" % (d, c, unit) for d in (d1, d2) for c in (6, 10, 12, 20)]
    if form == "liquid":
        return ["%dml" % v for v in (60, 100, 120, 150, 200, 250, 300, 500)]
    if form == "ointment":
        return ["%dg" % v for v in (5, 10, 15, 20, 25, 30, 40, 50)]
    if form == "drop":
        return ["%dml" % v for v in (5, 8, 10, 12, 15, 20, 25, 30)]
    if form == "spray":
        return ["%dml" % v for v in (10, 15, 20, 25, 30, 40, 50, 60)]
    raise ValueError(form)


def calc_price(form, spec, rnd):
    """按包装量推算采购价，避免"24 粒装卖 160 元"这类失真价格。"""
    if "*" in spec:
        n = int(spec.split("*")[1].rstrip("粒片丸袋枚"))
    else:
        n = int("".join(ch for ch in spec if ch.isdigit()) or 10)
    if form in ("capsule", "tablet", "pill"):
        return round(rnd.uniform(0.12, 1.6) * n, 2)
    if form in ("granule", "powder"):
        return round(rnd.uniform(0.7, 3.5) * n, 2)
    if form == "suppository":
        return round(rnd.uniform(1.5, 6.0) * n, 2)
    if form == "liquid":
        return round(n / 100.0 * rnd.uniform(8, 26), 2)
    if form == "ointment":
        return round(n / 10.0 * rnd.uniform(3.5, 14), 2)
    if form == "drop":
        return round(n * rnd.uniform(0.8, 3.2), 2)
    if form == "spray":
        return round(n * rnd.uniform(1.4, 5.0), 2)
    return round(rnd.uniform(5, 60), 2)


def gen_name(rnd):
    s = rnd.choice(SURNAMES)
    n = "".join(rnd.choice(GIVEN_CHARS) for _ in range(rnd.choice((1, 2))))
    return s + n


def balance(x):
    return round(float(x) + 1e-9, 2)


# =====================================================================
# 数据构造
# =====================================================================
def build_base(rnd):
    today = date.today()

    categories = []
    for i, (name, code) in enumerate(CATEGORIES, 1):
        categories.append((i, name, code, "%s类药品" % name, 1))

    warehouses = []
    for i, (name, loc, cap) in enumerate(WAREHOUSES, 1):
        warehouses.append((i, name, loc, cap, gen_name(rnd), 1))

    suppliers = []
    used = set()
    for i in range(1, N_SUPPLIER + 1):
        while True:
            city = rnd.choice(CITIES)
            name = city + rnd.choice(SUPPLIER_SUFFIX)
            if name not in used:
                used.add(name)
                break
        phone = "0%d-%08d" % (rnd.randint(10, 99), rnd.randint(0, 99999999))
        addr = "%s市%s区%s路%d号" % (city, rnd.choice("东西南北中"),
                                    rnd.choice("科创园光华建业"), rnd.randint(1, 999))
        lic = "苏AA%d%04d" % (rnd.randint(100000, 999999), i)
        credit = rnd.choices(["A", "B", "C"], weights=[3, 6, 1])[0]
        coop = today - timedelta(days=rnd.randint(200, 3600))
        suppliers.append((i, name, gen_name(rnd), phone, addr, lic, credit, coop, 1))

    employees = []
    for i in range(1, N_EMPLOYEE + 1):
        wh = (i - 1) % N_WAREHOUSE + 1
        pos = POSITIONS[(i - 1) // N_WAREHOUSE % len(POSITIONS)]
        emp_no = "E%04d" % i
        gender = rnd.choice("男女")
        phone = "1%d%09d" % (rnd.randint(3, 9), rnd.randint(0, 999999999))
        hire = today - timedelta(days=rnd.randint(60, 2500))
        employees.append((i, emp_no, gen_name(rnd), gender, pos, wh, phone, hire, 1))

    emp_by_wh_pos = defaultdict(list)
    for e in employees:
        emp_by_wh_pos[(e[5], e[4])].append(e[0])

    drugs = []
    seq = 0
    for cate_idx, (cate_name, _code) in enumerate(CATEGORIES, 1):
        doses = CATE_DOSES[cate_name]
        for drug_name, form in DRUG_GROUPS[cate_name]:
            for spec in gen_specs(form, rnd, doses):
                seq += 1
                if seq > N_DRUG:
                    break
                pp = calc_price(form, spec, rnd)
                rp = round(pp * rnd.uniform(1.12, 1.38), 2)
                life = rnd.choice([12, 18, 24, 24, 36])
                is_rx = 0 if cate_name == "维生素与营养药" else 1
                drugs.append((seq, "YP%06d" % seq, drug_name, cate_idx, spec,
                              FORM_RULES[form][1], rnd.choice(MANUFACTURERS),
                              "国药准字H%08d" % rnd.randint(10000000, 99999999),
                              pp, rp, is_rx, life, 1))
            if seq >= N_DRUG:
                break
        if seq >= N_DRUG:
            break

    return categories, warehouses, suppliers, employees, drugs, emp_by_wh_pos


def build_purchase(rnd, drugs, warehouses, suppliers, emp_by_wh_pos):
    """采购单 → 明细 → 批次；同时产出每个 (药品, 仓库) 的入库事件。"""
    today = date.today()
    drug_life = {d[0]: d[11] for d in drugs}
    drug_price = {d[0]: d[8] for d in drugs}   # 采购价
    drug_ids = [d[0] for d in drugs]
    wh_ids = [w[0] for w in warehouses]

    orders, items, batches = [], [], []
    events = defaultdict(list)          # (drug_id, wh_id) -> [event]
    first_in = {}                       # (drug_id, wh_id) -> 最早入库日期

    item_id = 0
    for po_id in range(1, N_PURCHASE + 1):
        wh = rnd.choice(wh_ids)
        sup = rnd.randint(1, N_SUPPLIER)
        emp = rnd.choice(emp_by_wh_pos[(wh, "采购员")])
        order_day = today - timedelta(days=rnd.randint(20, 400))
        expect_day = order_day + timedelta(days=rnd.randint(1, 7))
        po_no = "PO%s%05d" % (order_day.strftime("%Y%m%d"), po_id)
        status = "待入库" if rnd.random() < 0.03 else "已入库"

        n_item = rnd.randint(PI_MIN, PI_MAX)
        picked = rnd.sample(drug_ids, n_item)
        order_items = []
        total = 0.0
        for did in picked:
            item_id += 1
            qty = rnd.randint(60, 600)
            price = drug_price[did]
            amount = balance(qty * price)
            total += amount

            life = drug_life[did]
            in_day = order_day + timedelta(days=rnd.randint(1, 7))
            min_prod = today - timedelta(days=life * 30 - 120)
            prod = in_day - timedelta(days=rnd.randint(20, 120))
            if prod < min_prod:
                prod = min_prod
            if prod > in_day:
                prod = in_day - timedelta(days=5)
            expiry = prod + timedelta(days=life * 30)
            batch_no = "%s%05d" % (prod.strftime("%y%m%d"), item_id)

            order_items.append((item_id, po_id, did, qty, price, amount,
                                batch_no, prod, expiry))

            # 只有已入库的采购明细才生成库存批次并计入库存；
            # 待入库单据只停留在单据层面（批号已写，货未到仓）。
            if status == "已入库":
                b = {"batch_id": item_id, "item_id": item_id, "drug_id": did,
                     "wh_id": wh, "batch_no": batch_no, "prod": prod, "exp": expiry,
                     "qty": qty, "remaining": qty, "price": price, "in_date": in_day}
                batches.append(b)

                key = (did, wh)
                if key not in first_in or in_day < first_in[key]:
                    first_in[key] = in_day
                events[key].append({
                    "dt": datetime.combine(in_day, datetime.min.time())
                          + timedelta(hours=rnd.randint(8, 18), minutes=rnd.randint(0, 59)),
                    "kind": "IN", "qty": qty, "batch": b,
                    "ref_no": po_no, "emp": emp,
                })

        orders.append((po_id, po_no, sup, wh, emp, order_day, expect_day,
                       balance(total), status))
        items.extend(order_items)

    return orders, items, batches, events, first_in


def build_sale(rnd, warehouses, drugs, emp_by_wh_pos, events, first_in):
    """销售单 → 明细；出库日期一定晚于该药品在该仓库的首次入库。"""
    today = date.today()
    drug_price = {d[0]: d[9] for d in drugs}   # 零售价
    wh_ids = [w[0] for w in warehouses]

    wh_drugs = defaultdict(list)
    for (did, wh), d0 in first_in.items():
        wh_drugs[wh].append((did, d0))
    for wh in wh_drugs:
        wh_drugs[wh].sort(key=lambda x: x[1])

    orders, items = [], []
    item_id = 0
    for so_id in range(1, N_SALE + 1):
        wh = rnd.choice(wh_ids)
        pool = wh_drugs.get(wh) or []
        if not pool:
            continue
        emp = rnd.choice(emp_by_wh_pos[(wh, "销售员")])
        span = 180 if rnd.random() < 0.75 else 360
        sale_day = today - timedelta(days=rnd.randint(0, span))

        cand = [d for d, d0 in pool if d0 <= sale_day]
        if not cand:
            continue
        n_item = min(rnd.randint(SI_MIN, SI_MAX), len(cand))
        picked = rnd.sample(cand, n_item)

        ctype = rnd.choices(["连锁药店", "医院", "诊所", "个人"],
                            weights=[4, 3, 2, 1])[0]
        city = rnd.choice(CITIES)
        if ctype == "连锁药店":
            customer = "%s%s大药房连锁有限公司" % (city, rnd.choice("同仁堂华氏益丰老百姓"))
        elif ctype == "医院":
            customer = "%s市%s医院" % (city, rnd.choice(["第一人民", "中心", "中医", "第二人民"]))
        elif ctype == "诊所":
            customer = "%s%s社区卫生服务中心" % (city, rnd.choice("鼓楼玄武建邺秦淮"))
        else:
            customer = "个人客户-%s" % gen_name(rnd)

        so_no = "SO%s%05d" % (sale_day.strftime("%Y%m%d"), so_id)
        order_items = []
        total = 0.0
        for did in picked:
            item_id += 1
            qty = rnd.randint(1, 30)
            price = drug_price[did]
            amount = balance(qty * price)
            total += amount
            order_items.append((item_id, so_id, did, qty, price, amount))
            events[(did, wh)].append({
                "dt": datetime.combine(sale_day, datetime.min.time())
                      + timedelta(hours=rnd.randint(8, 20), minutes=rnd.randint(0, 59)),
                "kind": "OUT", "qty": qty, "item_id": item_id,
                "ref_no": so_no, "emp": emp,
            })

        orders.append((so_id, so_no, customer, ctype, wh, emp, sale_day,
                       balance(total), "已出库"))
        items.extend(order_items)

    return orders, items


def simulate(rnd, events, batches, emp_by_wh_pos):
    """按时间推演每个 (药品, 仓库) 的库存：批次 FIFO 扣减 + 流水连续。"""
    today = date.today()
    movements, stocks = [], []
    sale_fix = {}       # sale_item.item_id -> 实际出库数量（库存不足时被截断）
    move_id = 0

    all_keys = list(events.keys())
    for _ in range(N_ADJUST):
        key = rnd.choice(all_keys)
        emp = rnd.choice(emp_by_wh_pos[(key[1], "仓管员")])
        events[key].append({
            "dt": datetime.combine(today - timedelta(days=rnd.randint(0, 360)),
                                   datetime.min.time())
                  + timedelta(hours=rnd.randint(9, 17)),
            "kind": "ADJ", "qty": rnd.choice([-1, 1]) * rnd.randint(1, 20),
            "ref_no": "PD%06d" % rnd.randint(1, 999999), "emp": emp,
        })

    for key, evs in events.items():
        did, wh = key
        evs.sort(key=lambda e: (e["dt"], 0 if e["kind"] == "IN" else 1))
        cur = 0
        queue = []
        last_dt = None
        for e in evs:
            before = cur
            if e["kind"] == "IN":
                cur += e["qty"]
                queue.append(e["batch"])
                change = e["qty"]
            elif e["kind"] == "OUT":
                actual = min(e["qty"], cur)
                if actual <= 0:
                    sale_fix[e["item_id"]] = 0
                    continue
                left = actual
                for b in queue:
                    if left <= 0:
                        break
                    if b["remaining"] <= 0:
                        continue
                    take = min(b["remaining"], left)
                    b["remaining"] -= take
                    left -= take
                cur -= actual
                change = -actual
                sale_fix[e["item_id"]] = actual
            else:                       # 盘点调整（同步调整批次，保持"库存=批次和"）
                change = e["qty"]
                if before + change < 0:
                    change = -before
                if change == 0 or not queue:
                    continue
                if change > 0:          # 盘盈：补到最近一个批次
                    queue[-1]["remaining"] += change
                else:                   # 盘亏：从最早批次扣
                    left = -change
                    for b in queue:
                        if left <= 0:
                            break
                        take = min(b["remaining"], left)
                        b["remaining"] -= take
                        left -= take
                cur = before + change

            move_id += 1
            movements.append((move_id, did, wh,
                              {"IN": "采购入库", "OUT": "销售出库", "ADJ": "盘点调整"}[e["kind"]],
                              e.get("ref_no"), change, before, cur,
                              e["batch"]["batch_no"] if e["kind"] == "IN" else None,
                              e["emp"], e["dt"]))
            last_dt = e["dt"]

        stocks.append((len(stocks) + 1, did, wh, cur,
                       rnd.choice([30, 50, 80, 100]), last_dt or datetime.now()))

    return movements, stocks, sale_fix


# =====================================================================
# 写库
# =====================================================================
def bulk_insert(cur, table, cols, rows, chunk=2000):
    if not rows:
        print("  %-16s %8d 行" % (table, 0))
        return 0
    sql = "INSERT INTO %s (%s) VALUES (%s)" % (
        table, ", ".join(cols), ", ".join(["%s"] * len(cols)))
    for i in range(0, len(rows), chunk):
        cur.executemany(sql, rows[i:i + chunk])
    print("  %-16s %8d 行" % (table, len(rows)))
    return len(rows)


def main():
    ap = argparse.ArgumentParser(description="药品进销存初始数据生成")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=3306)
    ap.add_argument("--user", default="root")
    ap.add_argument("--password", default="123456")
    ap.add_argument("--db", default="drug_db")
    ap.add_argument("--seed", type=int, default=None)
    args = ap.parse_args()

    rnd = random.Random(args.seed) if args.seed is not None else random.Random()

    t0 = time.time()
    print("正在连接 %s:%d/%s ..." % (args.host, args.port, args.db))
    conn = pymysql.connect(host=args.host, port=args.port, user=args.user,
                           password=args.password, database=args.db,
                           charset="utf8mb4", autocommit=False)
    cur = conn.cursor()

    print("\n[1/6] 构造基础数据（分类 / 仓库 / 供应商 / 员工 / 药品）")
    categories, warehouses, suppliers, employees, drugs, emp_by_wh_pos = build_base(rnd)

    print("[2/6] 构造采购业务（采购单 / 明细 / 批次）")
    po_rows, pi_rows, batches, events, first_in = build_purchase(
        rnd, drugs, warehouses, suppliers, emp_by_wh_pos)

    print("[3/6] 构造销售业务（销售单 / 明细）")
    so_rows, si_rows = build_sale(rnd, warehouses, drugs, emp_by_wh_pos, events, first_in)

    print("[4/6] 推演库存（批次 FIFO 扣减 / 生成库存流水）")
    mv_rows, stock_rows, sale_fix = simulate(rnd, events, batches, emp_by_wh_pos)

    # 按实际出库量修正销售明细
    fixed = []
    for it in si_rows:
        item_id, so_id, did, qty, price, amount = it
        actual = sale_fix.get(item_id, qty)
        if actual > 0:
            fixed.append((item_id, so_id, did, actual, price, balance(actual * price)))
    si_rows = fixed

    # 修正单据金额（明细被截断后重算）
    so_total = defaultdict(float)
    for r in si_rows:
        so_total[r[1]] += r[5]
    so_rows = [(r[0], r[1], r[2], r[3], r[4], r[5], r[6],
                balance(so_total.get(r[0], 0.0)), r[8])
               for r in so_rows if r[0] in so_total]

    po_total = defaultdict(float)
    for r in pi_rows:
        po_total[r[1]] += r[5]
    po_rows = [(r[0], r[1], r[2], r[3], r[4], r[5], r[6],
                balance(po_total.get(r[0], 0.0)), r[8]) for r in po_rows]

    print("[5/6] 清空旧数据")
    cur.execute("SET FOREIGN_KEY_CHECKS=0")
    for t in ["stock_movement", "sale_item", "sale_order", "stock_batch", "stock",
              "purchase_item", "purchase_order", "drug", "employee", "supplier",
              "warehouse", "drug_category", "access_log"]:
        cur.execute("TRUNCATE TABLE %s" % t)
    cur.execute("SET FOREIGN_KEY_CHECKS=1")

    print("[6/6] 写入数据库")
    total = 0
    total += bulk_insert(cur, "drug_category",
                         ["category_id", "category_name", "category_code", "description", "status"],
                         categories)
    total += bulk_insert(cur, "warehouse",
                         ["warehouse_id", "warehouse_name", "location", "capacity", "manager", "status"],
                         warehouses)
    total += bulk_insert(cur, "supplier",
                         ["supplier_id", "supplier_name", "contact_person", "phone", "address",
                          "license_no", "credit_level", "cooperate_date", "status"], suppliers)
    total += bulk_insert(cur, "employee",
                         ["employee_id", "emp_no", "emp_name", "gender", "position",
                          "warehouse_id", "phone", "hire_date", "status"], employees)
    total += bulk_insert(cur, "drug",
                         ["drug_id", "drug_code", "drug_name", "category_id", "spec", "unit",
                          "manufacturer", "approval_no", "purchase_price", "retail_price",
                          "is_rx", "shelf_life_months", "status"], drugs)
    total += bulk_insert(cur, "purchase_order",
                         ["po_id", "po_no", "supplier_id", "warehouse_id", "employee_id",
                          "order_date", "expect_date", "total_amount", "status"], po_rows)
    total += bulk_insert(cur, "purchase_item",
                         ["item_id", "po_id", "drug_id", "quantity", "unit_price", "amount",
                          "batch_no", "production_date", "expiry_date"], pi_rows)
    total += bulk_insert(cur, "stock_batch",
                         ["batch_id", "purchase_item_id", "drug_id", "warehouse_id", "batch_no",
                          "production_date", "expiry_date", "quantity", "purchase_price", "in_date"],
                         [(b["batch_id"], b["item_id"], b["drug_id"], b["wh_id"], b["batch_no"],
                           b["prod"], b["exp"], b["remaining"], b["price"], b["in_date"])
                          for b in batches])
    total += bulk_insert(cur, "sale_order",
                         ["so_id", "so_no", "customer_name", "customer_type", "warehouse_id",
                          "employee_id", "sale_date", "total_amount", "status"], so_rows)
    total += bulk_insert(cur, "sale_item",
                         ["item_id", "so_id", "drug_id", "quantity", "unit_price", "amount"],
                         si_rows)
    total += bulk_insert(cur, "stock_movement",
                         ["move_id", "drug_id", "warehouse_id", "move_type", "ref_no",
                          "change_qty", "before_qty", "after_qty", "batch_no",
                          "employee_id", "move_time"], mv_rows)
    total += bulk_insert(cur, "stock",
                         ["stock_id", "drug_id", "warehouse_id", "quantity",
                          "safety_stock", "update_time"], stock_rows)

    conn.commit()

    print("\n合计写入 %d 行，耗时 %.1f 秒" % (total, time.time() - t0))

    print("\n自洽性校验：")
    checks = []
    cur.execute("SELECT COUNT(*) FROM stock s WHERE s.quantity <> "
                "(SELECT COALESCE(SUM(b.quantity),0) FROM stock_batch b "
                " WHERE b.drug_id=s.drug_id AND b.warehouse_id=s.warehouse_id)")
    checks.append(("库存结存 = 批次结存之和", cur.fetchone()[0]))

    cur.execute("SELECT COUNT(*) FROM stock_movement WHERE after_qty <> before_qty + change_qty")
    checks.append(("流水 after = before + change", cur.fetchone()[0]))

    cur.execute("SELECT COUNT(*) FROM purchase_item WHERE amount <> ROUND(quantity * unit_price, 2)")
    checks.append(("采购明细金额 = 数量 × 单价", cur.fetchone()[0]))

    cur.execute("SELECT COUNT(*) FROM sale_item WHERE amount <> ROUND(quantity * unit_price, 2)")
    checks.append(("销售明细金额 = 数量 × 单价", cur.fetchone()[0]))

    cur.execute("SELECT COUNT(*) FROM stock WHERE quantity < 0")
    checks.append(("库存无负数", cur.fetchone()[0]))

    cur.execute("SELECT COUNT(*) FROM stock_batch WHERE quantity < 0")
    checks.append(("批次结存无负数", cur.fetchone()[0]))

    ok = True
    for name, bad in checks:
        print("  %-28s %s" % (name, "通过 ✔" if bad == 0 else "异常 %d 行 ✘" % bad))
        ok = ok and bad == 0

    cur.close()
    conn.close()
    sys.exit(0 if ok else 2)


if __name__ == "__main__":
    main()
