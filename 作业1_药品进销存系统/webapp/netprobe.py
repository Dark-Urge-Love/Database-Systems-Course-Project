# -*- coding: utf-8 -*-
"""
自动发现数据库服务器地址
------------------------------------------------------------------
背景：Ubuntu(WSL) 每次重启后，它的虚拟网卡 IP 都会重新分配
（例如 192.168.150.181 → 192.168.144.30），写死在配置里会天天连不上。

本模块按"从快到慢"的顺序找可用的 MySQL，找到就把地址与账号写回 config：

  1) 环境变量 DB_HOST —— 用户显式指定，最高优先级，坚决不猜、不回退
  2) 上次成功过的地址缓存 .db_host_cache
  3) Ubuntu 启动时写出的 wsl_ip.txt
  4) 扫描 WSL/Hyper-V 虚拟网卡所在网段，找开放着 MySQL 端口的主机
  5) 本机 127.0.0.1（开发环境回退；可用 ALLOW_LOCAL_FALLBACK=0 关掉）

前三步与第 4 步都用"服务器账号"（druguser）验证，且要求 @@datadir 是 Linux 路径，
确保找到的是那台 Linux 服务器，而不是本机或别的什么服务。
第 5 步改用"本机账号"（root），且不要求 Linux 路径 —— 它明确就是开发环境。
"""
import ipaddress
import os
import re
import socket
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor

PROJ_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_FILE = os.path.join(PROJ_ROOT, ".db_host_cache")

_SCAN_LIMIT = 8192          # 单次最多扫描的主机数，防止极端网段拖慢启动
_SCAN_WORKERS = 512
_SCAN_TIMEOUT = 0.2
_COOLDOWN = 20.0            # 秒：一次失败后 20 秒内不再重复扫描，避免每个请求都扫一遍

_last_attempt = 0.0


# =====================================================================
# 基础工具
# =====================================================================
def _decode(raw):
    for enc in ("gbk", "utf-8"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "ignore")


def port_open(host, port=3306, timeout=_SCAN_TIMEOUT):
    s = socket.socket()
    s.settimeout(timeout)
    try:
        return s.connect_ex((host, int(port))) == 0
    except OSError:
        return False
    finally:
        try:
            s.close()
        except OSError:
            pass


def verify_login(host, port, user, password, database, timeout=3, require_linux=True):
    """
    用真实账号连一次，确认这台 MySQL 就是我们要找的那台。

    require_linux=True 时会额外检查 @@datadir 是不是 Linux 风格路径（以 / 开头），
    这样即使本机也装了一台账号相同的 MySQL，也不会被误认成 Linux 服务器。
    """
    try:
        import pymysql
        conn = pymysql.connect(host=host, port=int(port), user=user, password=password,
                               database=database or None, connect_timeout=timeout)
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT @@datadir AS d, @@version_comment AS v")
                row = cur.fetchone()
            datadir = (row[0] if isinstance(row, (list, tuple)) else row.get("d")) or ""
            if require_linux and not str(datadir).startswith("/"):
                return False
        finally:
            conn.close()
        return True
    except Exception:                                          # noqa: BLE001
        return False


# =====================================================================
# 网段探测
# =====================================================================
def _adapter_blocks():
    """把 ipconfig 输出切成「一个适配器一块」。

    注意：ipconfig 里适配器标题行后面会跟一个空行，所以不能按空行切块，
    要按"顶格的行"（标题行不缩进，详细行都缩进）来切。
    """
    try:
        raw = subprocess.run(["ipconfig"], capture_output=True, timeout=10).stdout
    except Exception:                                          # noqa: BLE001
        return []
    out = _decode(raw)
    blocks, cur = [], None
    for line in out.splitlines():
        if line.strip() and not line.startswith((" ", "\t")):
            if cur:
                blocks.append(cur)
            cur = line + "\n"
        elif cur is not None:
            cur += line + "\n"
    if cur:
        blocks.append(cur)
    return blocks


def local_ipv4_addresses():
    """本机所有网卡的 IPv4 地址（扫描时要排除，否则会扫到自己）。"""
    ips = {"127.0.0.1", "0.0.0.0"}
    for block in _adapter_blocks():
        m_ip = re.search(r"IPv4[^:：]*[:：]\s*([0-9]+\.[0-9]+\.[0-9]+\.[0-9]+)", block)
        if m_ip:
            ips.add(m_ip.group(1))
    return ips


def wsl_networks():
    """解析 ipconfig，取出 WSL / Hyper-V 虚拟网卡的网段。"""
    nets = []
    for block in _adapter_blocks():
        if not re.search(r"WSL|Hyper-V|vEthernet", block, re.I):
            continue
        m_ip = re.search(r"IPv4[^:：]*[:：]\s*([0-9]+\.[0-9]+\.[0-9]+\.[0-9]+)", block)
        m_mask = re.search(r"(?:子网掩码|Subnet\s*Mask)[^:：]*[:：]\s*([0-9]+\.[0-9]+\.[0-9]+\.[0-9]+)",
                           block, re.I)
        if not (m_ip and m_mask):
            continue
        try:
            net = ipaddress.IPv4Network(m_ip.group(1) + "/" + m_mask.group(1), strict=False)
        except ValueError:
            continue
        if net.num_addresses <= 65536 and net not in nets:
            nets.append(net)
    return nets


def scan_network(nets, port=3306):
    """并发扫描若干网段，返回开放了指定端口的主机列表（已排除本机地址）。"""
    skip = local_ipv4_addresses()
    hosts = []
    for net in nets:
        for h in net.hosts():
            hs = str(h)
            if hs in skip:
                continue
            hosts.append(hs)
            if len(hosts) >= _SCAN_LIMIT:
                break
        if len(hosts) >= _SCAN_LIMIT:
            break
    if not hosts:
        return []
    found = []
    with ThreadPoolExecutor(max_workers=_SCAN_WORKERS) as ex:
        for host, ok in zip(hosts, ex.map(lambda h: port_open(h, port), hosts)):
            if ok:
                found.append(host)
    return found


# =====================================================================
# 候选地址收集
# =====================================================================
def _read_ip_file(path):
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            txt = f.read().strip()
        for token in re.findall(r"[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+", txt):
            return token
    except OSError:
        return None
    return None


def candidate_hints(config):
    """
    不需要扫描就能得到的"服务器"候选地址，按可信度从高到低：
      1) Ubuntu 启动脚本写出的 wsl_ip.txt（最新、最可靠）
      2) 上次成功连接并缓存的地址
      3) 配置文件里的服务器默认地址
    """
    hints = []

    def add(x):
        if x and x not in hints:
            hints.append(x)

    add(_read_ip_file(os.path.join(PROJ_ROOT, "wsl_ip.txt")))
    add(_read_ip_file(CACHE_FILE))
    add(getattr(config, "SERVER_HOST", config.DB_HOST))
    return hints


def _write_cache(host):
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            f.write(host + "\n")
    except OSError:
        pass


def _apply(config, host, user, password, profile):
    """探测成功：把地址、账号、来源标记写回 config。"""
    if (host, user) != (config.DB_HOST, config.DB_USER):
        print(" [发现] 数据库目标已切换为 %s（账号 %s，%s）"
              % (host, user, "Linux 服务器" if profile == "server" else "本机 MySQL"))
    config.DB_HOST = host
    config.DB_USER = user
    config.DB_PASSWORD = password
    config.ACTIVE_PROFILE = profile
    if profile == "server":
        _write_cache(host)


# =====================================================================
# 对外主函数
# =====================================================================
def ensure_server(config, force=False, verbose=True):
    """
    确保 config 指向一台可用的 MySQL，返回最终使用的 host；都不可用时返回 None。

    为避免"每个失败的请求都重新扫一遍网段"（一次扫描约 2 秒，会让页面明显变慢），
    这里做了冷却：20 秒内只真正探测一次，其余调用直接返回。
    """
    global _last_attempt

    def log(msg):
        if verbose:
            print(msg)

    now = time.monotonic()
    within_cooldown = (now - _last_attempt) < _COOLDOWN
    if not force and within_cooldown:
        return None
    _last_attempt = now

    # ① 用户显式指定 → 尊重用户，只做连通性提示，不自动改、不回退
    if getattr(config, "DB_HOST_FROM_ENV", False) and not force:
        if verify_login(config.DB_HOST, config.DB_PORT, config.DB_USER,
                        config.DB_PASSWORD, config.DB_NAME, require_linux=False):
            config.ACTIVE_PROFILE = "custom"
            log(" [发现] 使用环境变量指定的服务器 %s：连接成功" % config.DB_HOST)
            return config.DB_HOST
        log(" [发现] 环境变量指定的服务器 %s 连接失败（DB_HOST 已显式指定，不做回退）"
            % config.DB_HOST)
        return None

    # ② 服务器：候选提示地址（wsl_ip.txt / 缓存 / 配置默认）
    #    先花 0.8 秒探端口，不通就直接跳过，避免为死地址白等 3 秒登录超时
    for host in candidate_hints(config):
        if not port_open(host, config.DB_PORT, 0.8):
            continue
        if verify_login(host, config.DB_PORT, config.SERVER_USER,
                        config.SERVER_PASSWORD, config.DB_NAME, require_linux=True):
            _apply(config, host, config.SERVER_USER, config.SERVER_PASSWORD, "server")
            log(" [发现] Linux 服务器 %s：连接成功" % host)
            return host

    # ③ 服务器：扫描 WSL 虚拟网卡所在网段
    if getattr(config, "AUTO_DISCOVER", True):
        nets = wsl_networks()
        if nets:
            log(" [发现] 未找到已知地址，开始扫描网段：%s（约几秒）"
                % ", ".join(str(n) for n in nets))
            for host in scan_network(nets, config.DB_PORT):
                if verify_login(host, config.DB_PORT, config.SERVER_USER,
                                config.SERVER_PASSWORD, config.DB_NAME, require_linux=True):
                    _apply(config, host, config.SERVER_USER, config.SERVER_PASSWORD, "server")
                    log(" [发现] 扫描到 Linux 服务器：%s" % host)
                    return host
            log(" [发现] 扫描完成，没有找到 Linux 服务器")
        else:
            log(" [发现] 未检测到 WSL/Hyper-V 虚拟网卡")

    # ④ 回退本机 MySQL（开发环境）
    if getattr(config, "ALLOW_LOCAL_FALLBACK", False):
        local_host = getattr(config, "LOCAL_HOST", "127.0.0.1")
        local_user = getattr(config, "LOCAL_USER", "root")
        local_pwd = getattr(config, "LOCAL_PASSWORD", "")
        if verify_login(local_host, config.DB_PORT, local_user, local_pwd,
                        config.DB_NAME, require_linux=False):
            _apply(config, local_host, local_user, local_pwd, "local")
            log(" [提示] 未连上 Linux 服务器，已回退到本机 MySQL %s（开发模式）" % local_host)
            log("        课程要求的正式环境是 Linux 服务器；答辩前请打开 Ubuntu 并在其中")
            log("        执行 deploy/init_server_db.sh 建好 druguser 账号与 drug_db 数据。")
            return local_host
        log(" [发现] 本机 MySQL 也不可用")

    log(" [发现] 没有可用数据库（Linux 服务器与本机 MySQL 均连接失败）")
    return None
