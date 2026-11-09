# -*- coding: utf-8 -*-
"""
数据库访问底层
------------------------------------------------------------------
职责：
  · 统一创建 pymysql 连接（DictCursor，返回字典便于模板渲染）
  · 提供 get_cursor / transaction 两个上下文管理器
  · transaction() 是"事务"能力的基础：正常结束 COMMIT，抛异常 ROLLBACK

注意：所有 SQL 都使用参数化（%s 占位），从根上避免 SQL 注入。
"""
from contextlib import contextmanager

import pymysql
from pymysql.cursors import DictCursor

from . import config


def _raw_connect(autocommit=False):
    return pymysql.connect(
        host=config.DB_HOST,
        port=config.DB_PORT,
        user=config.DB_USER,
        password=config.DB_PASSWORD,
        database=config.DB_NAME,
        charset=config.DB_CHARSET,
        connect_timeout=config.DB_CONNECT_TIMEOUT,
        autocommit=autocommit,
        cursorclass=DictCursor,
    )


_rediscovering = False


def connect(autocommit=False):
    """
    新建一条到 Linux 服务器上 MySQL 的连接。

    如果连不上（Ubuntu 重启后 IP 变了），会自动重新探测一次服务器地址再重连，
    这样就不用每次改配置。
    """
    global _rediscovering
    try:
        return _raw_connect(autocommit)
    except pymysql.OperationalError as e:
        code = e.args[0] if e.args else None
        if code in (2002, 2003) and config.AUTO_DISCOVER and not _rediscovering:
            _rediscovering = True
            try:
                from . import netprobe
                old_host = config.DB_HOST
                netprobe.ensure_server(config, force=False, verbose=False)
                if config.DB_HOST != old_host:
                    return _raw_connect(autocommit)
            finally:
                _rediscovering = False
        raise


@contextmanager
def get_cursor(commit=False):
    """只读查询用；commit=True 时用于不需要显式事务的单条写语句。"""
    conn = connect(autocommit=False)
    try:
        cur = conn.cursor()
        try:
            yield cur
            if commit:
                conn.commit()
        finally:
            cur.close()
    except BaseException:
        # 包含业务异常与"提前 return 导致上下文退出"的情况，一律回滚
        conn.rollback()
        raise
    finally:
        conn.close()


@contextmanager
def transaction(isolation=None):
    """
    显式事务上下文。用法：

        with transaction() as cur:
            cur.execute("SELECT quantity FROM stock WHERE drug_id=%s AND warehouse_id=%s FOR UPDATE", (1, 1))
            cur.execute("INSERT INTO sale_item (...) VALUES (...)")

    正常退出 → COMMIT；任何异常（含提前 return / 中断）→ ROLLBACK。
    isolation 可传 'READ COMMITTED' / 'REPEATABLE READ' / 'SERIALIZABLE'
    用于报告里对比不同隔离级别下的并发表现。
    """
    conn = connect(autocommit=False)
    try:
        if isolation:
            with conn.cursor() as c:
                c.execute("SET SESSION TRANSACTION ISOLATION LEVEL " + isolation)
        conn.begin()
        cur = conn.cursor()
        try:
            yield cur
        except BaseException:
            conn.rollback()
            raise
        else:
            conn.commit()
        finally:
            cur.close()
    finally:
        conn.close()


class BizError(Exception):
    """业务规则不满足时抛出，页面会把它当作"正常提示"而不是系统错误。"""
