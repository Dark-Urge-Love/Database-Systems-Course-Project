# -*- coding: utf-8 -*-
"""
智慧医疗 · 社区门诊预约与处方管理系统
数据生成脚本  data_gen.py

功能：向 outpatient_db 批量生成初始数据（10 张表，合计约 22.9 万条，满足"≥5 表、≥10 万条"要求）
依赖：仅需 pymysql  （pip install pymysql）
用法：
    python data_gen.py                                   # 默认 127.0.0.1:3306 root/123456
    python data_gen.py --host 192.168.56.10 --password abc123
    python data_gen.py --seed 2026                        # 固定随机种子，结果可复现

注意：
    1. 执行前请先运行 schema.sql 建库建表（本脚本假设库为空表结构已就绪）
    2. 显式写入主键，保证外键引用 100% 正确
    3. 保证业务自洽：remain_slots = total_slots - 未取消预约数；处方明细金额与药品单价一致
"""

import argparse
import random
import sys
import time
import zlib
from datetime import date, datetime, timedelta

try:
    import pymysql
except ImportError:
    print("[错误] 缺少依赖，请先执行：pip install pymysql")
    sys.exit(1)

# =====================================================================
# 目标行数
# =====================================================================
N_DEPT          = 30       # 科室
N_DOCTOR        = 300      # 医生
N_PATIENT       = 5000     # 患者
N_SCHEDULE      = 20000    # 排班
N_APPOINTMENT   = 40000    # 预约挂号
N_DRUG          = 500      # 药品
PRESC_RATE      = 0.73     # 就诊后开处方的比例 → 处方 ≈ 22000
N_ITEM          = 60000    # 处方明细总条数
ITEM_MIN, ITEM_MAX = 1, 5  # 每张处方的明细条数

# =====================================================================
# 词库
# =====================================================================
SURNAMES = list("王李张刘陈杨黄赵吴周徐孙马朱胡郭何高林罗郑梁谢宋唐许韩冯邓曹彭曾肖田董袁潘于蒋蔡余杜叶程苏魏吕丁任沈姚卢姜崔钟谭陆汪范金石廖贾夏韦付方白邹孟熊秦邱江尹薛闫段雷侯龙史陶黎贺顾毛郝龚邵万钱严覃武戴莫孔向汤")

GIVEN_CHARS = list("伟芳娜秀敏静丽强磊军洋勇艳杰娟涛明超秀霞平刚桂英华玉萍红娥玲芬燕彬鹏浩宇轩然梓涵欣怡子墨嘉懿雨萱思远致远晨曦瑞泽锦程文博雅琪书瑶新雨佳明志建国建华晓东晓明小雨小丽雪梅冬梅春燕")

DEPT_NAMES = [
    "内科", "外科", "儿科", "妇产科", "口腔科", "眼科", "耳鼻咽喉科", "皮肤科",
    "中医科", "康复医学科", "急诊科", "感染科", "神经内科", "心血管内科", "消化内科",
    "呼吸内科", "内分泌科", "肾内科", "血液科", "风湿免疫科", "骨科", "泌尿外科",
    "神经外科", "胸外科", "整形外科", "心理科", "营养科", "疼痛科", "体检中心", "全科门诊",
]
DEPT_TYPES = ["内科", "外科", "专科"]

TITLES = ["主任医师", "副主任医师", "主治医师", "住院医师"]
TITLE_WEIGHTS = [10, 20, 45, 25]

PERIODS = ["上午", "下午"]

# 药品目录：(药品名, 规格, 单位) —— 名称与剂型/规格真实对应，避免"XX滴眼液 0.5g*20粒"这种不合理组合
DRUG_CATALOG = [
    # 抗感染
    ("阿莫西林胶囊", "0.25g*24粒", "盒"), ("阿莫西林颗粒", "0.125g*20袋", "盒"),
    ("头孢克肟分散片", "0.1g*12片", "盒"), ("头孢克肟颗粒", "50mg*6袋", "盒"),
    ("阿奇霉素分散片", "0.25g*6片", "盒"), ("阿奇霉素干混悬剂", "0.1g*6袋", "盒"),
    ("左氧氟沙星片", "0.5g*7片", "盒"), ("盐酸左氧氟沙星滴眼液", "5ml:15mg", "支"),
    ("克拉霉素片", "0.25g*6片", "盒"), ("罗红霉素胶囊", "0.15g*12粒", "盒"),
    ("甲硝唑片", "0.2g*24片", "盒"), ("氟康唑胶囊", "0.15g*3粒", "盒"),
    ("阿昔洛韦片", "0.2g*24片", "盒"), ("利巴韦林颗粒", "50mg*18袋", "盒"),
    # 解热镇痛/抗炎
    ("布洛芬缓释胶囊", "0.3g*20粒", "盒"), ("布洛芬混悬液", "100ml:2g", "瓶"),
    ("对乙酰氨基酚片", "0.5g*20片", "盒"), ("阿司匹林肠溶片", "100mg*30片", "盒"),
    ("双氯芬酸钠缓释片", "75mg*10片", "盒"), ("塞来昔布胶囊", "0.2g*6粒", "盒"),
    ("洛索洛芬钠片", "60mg*20片", "盒"),
    # 抗过敏/呼吸
    ("氯雷他定片", "10mg*12片", "盒"), ("盐酸西替利嗪片", "10mg*12片", "盒"),
    ("孟鲁司特钠片", "10mg*5片", "盒"), ("糠酸莫米松鼻喷雾剂", "60喷:50μg", "支"),
    ("盐酸氨溴索口服溶液", "100ml:0.6g", "瓶"), ("氢溴酸右美沙芬糖浆", "100ml", "瓶"),
    ("复方甘草片", "100片", "瓶"), ("沙丁胺醇气雾剂", "200掀:100μg", "支"),
    ("布地奈德福莫特罗粉吸入剂", "60吸", "支"), ("茶碱缓释片", "0.1g*24片", "盒"),
    ("噻托溴铵粉雾剂", "18μg*10粒", "盒"),
    # 消化系统
    ("奥美拉唑肠溶胶囊", "20mg*14粒", "盒"), ("兰索拉唑肠溶片", "15mg*14片", "盒"),
    ("雷贝拉唑钠肠溶片", "10mg*7片", "盒"), ("铝碳酸镁咀嚼片", "0.5g*20片", "盒"),
    ("多潘立酮片", "10mg*30片", "盒"), ("枸橼酸莫沙必利片", "5mg*12片", "盒"),
    ("蒙脱石散", "3g*10袋", "盒"), ("双歧杆菌三联活菌胶囊", "210mg*24粒", "盒"),
    ("乳果糖口服溶液", "100ml:66.7g", "瓶"), ("熊去氧胆酸胶囊", "250mg*10粒", "盒"),
    # 心血管
    ("硝苯地平缓释片", "20mg*30片", "盒"), ("苯磺酸氨氯地平片", "5mg*7片", "盒"),
    ("缬沙坦胶囊", "80mg*14粒", "盒"), ("琥珀酸美托洛尔缓释片", "47.5mg*7片", "盒"),
    ("辛伐他汀片", "20mg*20片", "盒"), ("阿托伐他汀钙片", "20mg*7片", "盒"),
    ("硝酸甘油片", "0.5mg*100片", "瓶"), ("氯沙坦钾片", "50mg*7片", "盒"),
    ("硫酸氢氯吡格雷片", "75mg*7片", "盒"),
    # 内分泌/代谢
    ("盐酸二甲双胍片", "0.5g*30片", "盒"), ("格列美脲片", "2mg*15片", "盒"),
    ("阿卡波糖片", "50mg*30片", "盒"), ("精蛋白人胰岛素注射液", "300U:3ml", "支"),
    ("左甲状腺素钠片", "50μg*100片", "瓶"), ("非布司他片", "40mg*14片", "盒"),
    # 维生素/营养/血液
    ("维生素C片", "100mg*100片", "瓶"), ("复合维生素B片", "100片", "瓶"),
    ("碳酸钙D3片", "600mg*60片", "瓶"), ("叶酸片", "0.4mg*93片", "瓶"),
    ("维生素AD滴剂", "30粒", "盒"), ("琥珀酸亚铁片", "0.1g*20片", "盒"),
    # 中成药
    ("板蓝根颗粒", "10g*20袋", "盒"), ("连花清瘟胶囊", "0.35g*24粒", "盒"),
    ("藿香正气水", "10ml*10支", "盒"), ("小柴胡颗粒", "10g*10袋", "盒"),
    ("六味地黄丸", "360丸", "瓶"), ("逍遥丸", "200丸", "瓶"),
    ("云南白药气雾剂", "85g", "支"), ("复方丹参滴丸", "27mg*180丸", "瓶"),
    ("速效救心丸", "40mg*60丸", "盒"), ("蓝芩口服液", "10ml*6支", "盒"),
    # 外用/输注/神经系统
    ("碘伏消毒液", "500ml:2.5g", "瓶"), ("创可贴", "100片", "盒"),
    ("红霉素软膏", "10g:0.1g", "支"), ("莫匹罗星软膏", "10g:0.2g", "支"),
    ("氯化钠注射液", "250ml:2.25g", "瓶"), ("葡萄糖注射液", "250ml:12.5g", "瓶"),
    ("甲钴胺片", "0.5mg*20片", "盒"), ("甲钴胺注射液", "1ml:0.5mg", "支"),
    ("玻璃酸钠滴眼液", "5ml:5mg", "支"), ("地塞米松磷酸钠注射液", "1ml:5mg", "支"),
    ("氯化钾缓释片", "0.5g*24片", "盒"), ("醋酸泼尼松片", "5mg*100片", "瓶"),
]
MANUFACTURERS = [
    "华北制药", "石药集团", "扬子江药业", "哈药集团", "北京同仁堂", "云南白药", "齐鲁制药",
    "恒瑞医药", "正大天晴", "修正药业", "仁和药业", "白云山制药", "广州医药", "丽珠集团",
]
USAGE_NOTES = [
    "口服，一次1片，一日3次", "口服，一次1粒，一日2次", "口服，一次1袋，一日3次",
    "外用，一日2次", "静脉滴注，遵医嘱", "口服，睡前1片", "口服，一次2片，一日3次",
    "含服，一次1片", "口服，一次10ml，一日3次",
]
CHIEF_COMPLAINTS = [
    "咳嗽、咳痰3天", "发热2天，最高38.5℃", "腹痛、腹泻1天", "头痛、头晕1周",
    "咽痛、咽干2天", "反复咳嗽1月余", "关节疼痛、肿胀", "皮疹伴瘙痒3天",
    "胸闷、心悸2天", "食欲不振、乏力1周", "鼻塞、流涕4天", "腰背部疼痛",
    "视物模糊、眼干", "牙龈肿痛2天", "失眠、多梦1月",
]
DIAGNOSES = [
    "急性上呼吸道感染", "急性支气管炎", "急性胃肠炎", "高血压病2级", "2型糖尿病",
    "过敏性鼻炎", "荨麻疹", "慢性胃炎", "冠心病", "腰椎间盘突出症",
    "急性扁桃体炎", "功能性消化不良", "偏头痛", "结膜炎", "牙周炎",
]
ADVICES = [
    "多饮水，注意休息，清淡饮食", "按时服药，一周后复诊", "避免辛辣刺激食物",
    "监测血压，规律作息", "低盐低脂饮食，适当运动", "不适随诊", "完善相关检查后复诊",
]
PAY_METHODS = ["微信", "支付宝", "现金", "医保"]
ROOM_PREFIX = ["A", "B", "C", "D"]

# =====================================================================
# 工具函数
# =====================================================================
def rand_name(rng):
    n = rng.choice(SURNAMES)
    k = rng.choice([1, 2, 2])          # 姓名总长 2~3 字，符合中文习惯
    return n + "".join(rng.choice(GIVEN_CHARS) for _ in range(k))


def rand_phone(rng):
    prefix = rng.choice(["138", "139", "137", "136", "135", "150", "151", "158", "159", "180", "186", "188", "199"])
    return prefix + "".join(rng.choice("0123456789") for _ in range(8))


def rand_id_card(rng, birth: date, seq: int):
    area = rng.choice(["440106", "440103", "440304", "440305", "440111", "441302", "442000"])
    body = f"{area}{birth.strftime('%Y%m%d')}{seq:03d}"
    weight = [7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2]
    check_chars = "10X98765432"
    total = sum(int(body[i]) * weight[i] for i in range(17))
    return body + check_chars[total % 11]


def batch_insert(cur, sql: str, rows: list, chunk: int = 2000):
    for i in range(0, len(rows), chunk):
        cur.executemany(sql, rows[i:i + chunk])


def take(pool, n, rng):
    """从词库中取 n 个不重复值（不足则循环填充）"""
    out, guard = [], 0
    while len(out) < n:
        out.append(pool[(len(out) + guard) % len(pool)])
        guard += 1
        if guard > n * 3:
            out = (pool * ((n // len(pool)) + 1))[:n]
            break
    rng.shuffle(out)
    return out


# =====================================================================
# 主流程
# =====================================================================
def main():
    ap = argparse.ArgumentParser(description="门诊预约系统数据生成脚本")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=3306)
    ap.add_argument("--user", default="root")
    ap.add_argument("--password", default="123456")
    ap.add_argument("--db", default="outpatient_db")
    ap.add_argument("--seed", type=int, default=None, help="随机种子（可复现）")
    args = ap.parse_args()

    rng = random.Random(args.seed)
    t0 = time.time()

    conn = pymysql.connect(host=args.host, port=args.port, user=args.user,
                           password=args.password, database=args.db,
                           charset="utf8mb4", autocommit=False)
    cur = conn.cursor()
    print(f"[连接成功] {args.host}:{args.port}/{args.db}")

    today = date.today()
    stat = {}

    # ---------------- 1. 科室 ----------------
    depts = []
    for i, name in enumerate(DEPT_NAMES[:N_DEPT], start=1):
        depts.append((i, name, rng.choice(DEPT_TYPES),
                      f"{rng.randint(1, 5)}楼{rng.randint(1, 20)}诊区", rand_phone(rng)[:11].replace("138", "020"), 1))
    batch_insert(cur, "INSERT INTO department (dept_id,dept_name,dept_type,location,phone,status) VALUES (%s,%s,%s,%s,%s,%s)", depts)
    stat["department"] = len(depts)
    print(f"  department      {len(depts):>7,}")

    # ---------------- 2. 医生 ----------------
    doctors = []
    for i in range(1, N_DOCTOR + 1):
        dept_id = (i - 1) % N_DEPT + 1
        hire = today - timedelta(days=rng.randint(365, 365 * 25))
        doctors.append((i, rand_name(rng), rng.choice("男女"),
                        rng.choices(TITLES, weights=TITLE_WEIGHTS)[0], dept_id,
                        rand_phone(rng), hire, f"擅长{DEPT_NAMES[dept_id-1]}常见病诊治", 1))
    batch_insert(cur, "INSERT INTO doctor (doctor_id,doctor_name,gender,title,dept_id,phone,hire_date,intro,status) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)", doctors)
    stat["doctor"] = len(doctors)
    print(f"  doctor          {len(doctors):>7,}")

    # ---------------- 3. 患者 ----------------
    patients, phones, idcards = [], set(), set()
    for i in range(1, N_PATIENT + 1):
        birth = date(rng.randint(1940, 2022), rng.randint(1, 12), rng.randint(1, 28))
        ph = rand_phone(rng)
        while ph in phones:
            ph = rand_phone(rng)
        phones.add(ph)
        ic = rand_id_card(rng, birth, i % 1000)
        while ic in idcards:
            ic = rand_id_card(rng, birth, (i * 7) % 1000)
        idcards.add(ic)
        create_time = datetime.combine(birth, datetime.min.time()) + timedelta(days=rng.randint(7000, 20000))
        patients.append((i, rand_name(rng), rng.choice("男女"), birth, ph, ic,
                         f"MC{100000 + i}", create_time, 1))
    batch_insert(cur, "INSERT INTO patient (patient_id,patient_name,gender,birth_date,phone,id_card,medical_card_no,create_time,status) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)", patients)
    stat["patient"] = len(patients)
    print(f"  patient         {len(patients):>7,}")

    # ---------------- 4. 药品（同一药品可由不同厂家生产，形成真实的价格/规格差异） ----------------
    combos = [(n, s, u, m) for (n, s, u) in DRUG_CATALOG for m in MANUFACTURERS]
    rng.shuffle(combos)
    if len(combos) < N_DRUG:
        sys.exit(f"[错误] 药品目录组合数不足：{len(combos)} < {N_DRUG}")
    drugs = []
    for i, (name, spec, unit, manu) in enumerate(combos[:N_DRUG], start=1):
        # 同一药品规格价格稳定，不同厂家有 ±25% 浮动
        base_price = 3 + (zlib.crc32(f"{name}|{spec}".encode("utf-8")) % 145)
        price = round(base_price * rng.uniform(0.85, 1.25), 2)
        drugs.append((i, name, spec, unit, manu, price, rng.randint(500, 20000),
                      rng.choice([0, 1, 1]), 1))
    batch_insert(cur, "INSERT INTO drug (drug_id,drug_name,spec,unit,manufacturer,price,stock,is_rx,status) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)", drugs)
    stat["drug"] = len(drugs)
    print(f"  drug            {len(drugs):>7,}")

    # ---------------- 5. 排班（近 365 天） ----------------
    schedules, slots_total = [], {}
    sid = 0
    for doctor_id in range(1, N_DOCTOR + 1):
        for d in range(365):
            if rng.random() > 0.22:                      # 约 78% 的日子有排班
                continue
            work_date = today - timedelta(days=d)
            if work_date.weekday() >= 6 and rng.random() > 0.3:
                continue                                  # 周末多数不排班
            for period in PERIODS:
                if rng.random() > 0.75:
                    continue
                sid += 1
                total = rng.randint(15, 40)
                fee = round(rng.choice([5.0, 10.0, 15.0, 20.0, 30.0, 50.0]), 2)
                schedules.append([sid, doctor_id, work_date, period, total, total,
                                  fee, f"{rng.choice(ROOM_PREFIX)}{rng.randint(1, 9)}诊室"])
                slots_total[sid] = (total, fee, doctor_id, work_date, period)
                if sid >= N_SCHEDULE:
                    break
            if sid >= N_SCHEDULE:
                break
        if sid >= N_SCHEDULE:
            break
    print(f"  已生成排班 {len(schedules):,} 条，开始生成预约...")

    # ---------------- 6. 预约挂号（推导 remain_slots） ----------------
    # 逐个排班分配预约，保证 单排班预约数 <= total_slots，且 remain_slots 准确
    appts, visit_seed = [], []
    aid = 0
    order = schedules[:]
    rng.shuffle(order)                                    # 打散，使预约分散在全部排班上
    for s in order:
        if aid >= N_APPOINTMENT:
            break
        sid_, doctor_id, work_date, period = s[0], s[1], s[2], s[3]
        total = s[4]
        k = min(rng.randint(0, min(total, 12)), N_APPOINTMENT - aid)
        booked = 0
        for q in range(1, k + 1):
            aid += 1
            patient_id = rng.randint(1, N_PATIENT)
            hour = rng.randint(7, 11) if period == "上午" else rng.randint(13, 16)
            appt_time = datetime.combine(work_date, datetime.min.time()) + timedelta(
                hours=hour, minutes=rng.randint(0, 59), days=-1)
            r = rng.random()
            status = "已就诊" if r < 0.75 else ("已取消" if r < 0.85 else "已预约")
            appts.append((aid, patient_id, sid_, appt_time, q, status))
            if status != "已取消":
                booked += 1
            if status == "已就诊":
                visit_seed.append((aid, patient_id, doctor_id, work_date, period, appt_time))
        s[5] = total - booked                             # 剩余号源 = 总数 - 未取消预约

    batch_insert(cur, "INSERT INTO schedule (schedule_id,doctor_id,work_date,period,total_slots,remain_slots,reg_fee,room) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)", schedules)
    stat["schedule"] = len(schedules)
    print(f"  schedule        {len(schedules):>7,}")

    batch_insert(cur, "INSERT INTO appointment (appt_id,patient_id,schedule_id,appt_time,queue_no,status) VALUES (%s,%s,%s,%s,%s,%s)", appts)
    stat["appointment"] = len(appts)
    print(f"  appointment     {len(appts):>7,}")

    # ---------------- 7. 就诊记录（与"已就诊"预约 1-1） ----------------
    visits = []
    for idx, (appt_id, patient_id, doctor_id, work_date, period, appt_time) in enumerate(visit_seed, start=1):
        visit_time = appt_time + timedelta(days=1, hours=rng.randint(0, 3), minutes=rng.randint(0, 59))
        visits.append((idx, appt_id, patient_id, doctor_id, visit_time,
                       rng.choice(CHIEF_COMPLAINTS), rng.choice(DIAGNOSES), rng.choice(ADVICES)))
    batch_insert(cur, "INSERT INTO visit (visit_id,appt_id,patient_id,doctor_id,visit_time,chief_complaint,diagnosis,advice) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)", visits)
    stat["visit"] = len(visits)
    print(f"  visit           {len(visits):>7,}")

    # ---------------- 8. 处方 ----------------
    prescs, presc_visit = [], []
    pid = 0
    for v in visits:
        if rng.random() < PRESC_RATE:
            pid += 1
            prescs.append((pid, v[0], v[4] + timedelta(minutes=rng.randint(10, 60)),
                           rng.choices(["西药", "中药"], weights=[85, 15])[0], "已开方"))
            presc_visit.append(pid)
    batch_insert(cur, "INSERT INTO prescription (presc_id,visit_id,presc_time,presc_type,status) VALUES (%s,%s,%s,%s,%s)", prescs)
    stat["prescription"] = len(prescs)
    print(f"  prescription    {len(prescs):>7,}")

    # ---------------- 9. 处方明细（拆分 处方 N-M 药品） ----------------
    items, iid = [], 0
    while iid < N_ITEM:
        presc_id = rng.choice(presc_visit)
        cnt = rng.randint(ITEM_MIN, ITEM_MAX)
        picked = set()
        for _ in range(cnt):
            if iid >= N_ITEM:
                break
            drug_id = rng.randint(1, N_DRUG)
            if drug_id in picked:
                continue
            picked.add(drug_id)
            iid += 1
            qty = rng.randint(1, 4)
            items.append((iid, presc_id, drug_id, qty, rng.choice(USAGE_NOTES),
                          drugs[drug_id - 1][5]))
    batch_insert(cur, "INSERT INTO prescription_item (item_id,presc_id,drug_id,quantity,usage_note,unit_price) VALUES (%s,%s,%s,%s,%s,%s)", items)
    stat["prescription_item"] = len(items)
    print(f"  prescription_item {len(items):>5,}")

    # ---------------- 10. 缴费记录（挂号费 + 药费） ----------------
    presc_of_visit = {p[1]: p[0] for p in prescs}
    appt_schedule = {a[0]: a[2] for a in appts}          # 预约 → 排班（O(1) 查询）
    item_total = {}
    for it in items:
        item_total[it[1]] = item_total.get(it[1], 0) + it[3] * float(it[5])

    pays, pay_id = [], 0
    for v in visits:
        visit_id = v[0]
        appt_id = v[1]
        # 挂号费
        sch_id = appt_schedule.get(appt_id)
        fee = slots_total.get(sch_id, (0, 10.0, 0, 0, ""))[1] if sch_id else 10.0
        pay_id += 1
        pays.append((pay_id, visit_id, "挂号费", fee, rng.choice(PAY_METHODS),
                     v[4] + timedelta(minutes=5), "已支付"))
        # 药费
        if visit_id in presc_of_visit:
            amount = round(item_total.get(presc_of_visit[visit_id], 0.0), 2)
            if amount > 0:
                pay_id += 1
                pays.append((pay_id, visit_id, "药费", amount, rng.choice(PAY_METHODS),
                             v[4] + timedelta(minutes=rng.randint(30, 120)), "已支付"))
    batch_insert(cur, "INSERT INTO payment (pay_id,visit_id,fee_type,amount,pay_method,pay_time,status) VALUES (%s,%s,%s,%s,%s,%s,%s)", pays)
    stat["payment"] = len(pays)
    print(f"  payment         {len(pays):>7,}")

    # ---------------- 重置自增起点，避免后续插入主键冲突 ----------------
    for tbl, col, n in [("department", "dept_id", N_DEPT), ("doctor", "doctor_id", N_DOCTOR),
                        ("patient", "patient_id", N_PATIENT), ("drug", "drug_id", N_DRUG),
                        ("schedule", "schedule_id", len(schedules)),
                        ("appointment", "appt_id", len(appts)), ("visit", "visit_id", len(visits)),
                        ("prescription", "presc_id", len(prescs)),
                        ("prescription_item", "item_id", len(items)), ("payment", "pay_id", len(pays))]:
        cur.execute(f"ALTER TABLE {tbl} AUTO_INCREMENT = {n + 1}")

    conn.commit()

    # ---------------- 汇总 ----------------
    total = sum(stat.values())
    print("\n" + "=" * 46)
    print(f"{'表名':<20}{'行数':>12}")
    print("-" * 46)
    for k, v in stat.items():
        print(f"{k:<20}{v:>12,}")
    print("-" * 46)
    print(f"{'合计':<20}{total:>12,}")
    print("=" * 46)
    print(f"耗时 {time.time() - t0:.1f} 秒  |  {'✓ 已满足 ≥100,000 条要求' if total >= 100000 else '✗ 数据量不足'}")

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
