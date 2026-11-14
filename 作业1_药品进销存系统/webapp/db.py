# -*- coding: utf-8 -*-

"""
数据库访问底层
------------------------------------------------------------------
职责：
  · 统一创建 pymysql 连接（DictCursor，返回字典便于模板渲染）
  · 提供 get_cursor / transaction 两个上下文管理器
  · transaction() 是"事务"能力的基础：正常结束 COMMIT，抛异常 ROLLBACK

注意：所有 SQL 都使用参数化（%s 占位），从根上避免 SQL 注入。

【知识点：连接(connection) 与 游标(cursor) 的关系】
  连接 = 你和数据库之间的一条"电话线"，建立一次比较贵，要复用
  游标 = 这条线上的一次"通话"：你把 SQL 交给它，它把结果带回来
一个连接可以开多个游标；关闭游标不等于断开连接。

【知识点：事务 ACID】
  A 原子性：一组操作要么全成功，要么全失败（转账不会只扣不加）
  C 一致性：事务前后数据都满足业务规则
  I 隔离性：多个事务同时跑，互相看不到对方的中间状态
  D 持久性：提交后即使断电也不丢
MySQL 里用 BEGIN 开启，COMMIT 提交，ROLLBACK 撤销。
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
        # True  = 每条 SQL 执行完立刻生效（适合只读查询）
        # False = 需要你手动 commit() 才生效，中途出错可以 rollback() 撤销（事务必需）
        autocommit=autocommit,
        # 指定游标类型：让查询结果返回字典而不是元组
        cursorclass=DictCursor,
    )


# 对外函数：拿一条连接
def connect(autocommit=False):
    """
    新建一条到 Linux 服务器上 MySQL 的连接。
    连不上（Ubuntu 没启动 / MySQL 没起 / IP 变了）会直接抛出异常，
    由调用方（get_cursor、transaction 以及 run.py 的启动自检）捕获并给出友好提示。
    """
    # 直接把 _raw_connect 的结果返回 —— 本函数现在只是一层"起个好名字"的包装
    return _raw_connect(autocommit)


# @contextmanager 把下面的函数变成"可以用 with 的上下文管理器"
@contextmanager
# get_cursor() 是个“自动借还工具”：你只管用游标写 SQL，它帮你建连接、开游标，用完自动关掉、出错自动回滚。
def get_cursor(commit=False):
    # 先拿到一条连接（autocommit=False，也就是要手动提交）
    conn = connect(autocommit=False)

    # 外层 try：保证最后一定关闭连接
    try:

        # 从连接上开一个游标
        cur = conn.cursor()

        # 内层 try：保证最后一定关闭游标
        try:
            # 【知识点：yield】这是个"生成器"写法。yield 会把游标交给 with 语句块使用，
            # 等块里的代码全部执行完，才会回到这里继续往下走。
            # 所以 with get_cursor() as cur: ... 里的 cur 就是这里 yield 出去的对象
            yield cur

            # 如果调用方要求提交（只有单条写语句才会这么用），就提交
            if commit:
                # commit = 让本次事务的修改真正落库
                conn.commit()

        finally:

            # 无论正常还是异常，都关闭游标，释放数据库端资源
            cur.close()
    # BaseException 是最顶层的异常类（连 KeyboardInterrupt 都能接住）
    # 这里故意接这么宽：只要 with 块里出了任何问题，都必须回滚
    except BaseException:

        # 包含业务异常与"提前 return 导致上下文退出"的情况，一律回滚
        # 【知识点：rollback 回滚】把本事务里已经执行但未提交的修改全部丢弃，
        # 数据库回到事务开始前的状态 —— 这就是"原子性"的保证
        conn.rollback()

        # raise 把异常原样再抛出，交给上层（views.py）去决定显示什么错误
        raise

    finally:

        # 无论如何都要关闭连接，不然连接数会越积越多，最后把 MySQL 的连接池占满
        conn.close()


# 事务上下文管理器：本项目的核心能力之一（采购入库、销售出库都靠它）
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

    # 开一条连接，autocommit=False 才能做事务
    conn = connect(autocommit=False)

    # 最外层 try：只管"最终关连接"
    try:

        # 如果调用方指定了隔离级别，就先把它设到当前会话上
        if isolation:

            # with conn.cursor() as c: 用完自动关游标，适合"执行一条就完事"的场景
            with conn.cursor() as c:

                # 【知识点：SQL 拼接的风险】这里用字符串相加拼 SQL，看起来不规范，
                # 但 isolation 的取值只可能是代码里写死的那几个常量（READ COMMITTED 等），
                # 不是用户输入，所以不存在注入风险。真实项目更稳妥的做法是白名单校验
                c.execute("SET SESSION TRANSACTION ISOLATION LEVEL " + isolation)

        # begin() 显式开启一个事务（等价于执行 SQL 的 BEGIN / START TRANSACTION）
        conn.begin()

        # 开游标
        cur = conn.cursor()

        # 内层 try：管"提交还是回滚"
        try:

            # 把游标交给 with 块，业务代码用它执行多条 SQL
            yield cur

        # 出异常 → 回滚
        except BaseException:

            # 撤销本事务的全部修改
            conn.rollback()

            # 异常继续往上抛
            raise

        # 【知识点：try/except/else/finally 四件套】
        # else 只在"try 里没发生异常"时执行 —— 即业务代码全部跑通了
        else:

            # 提交：多条 SQL 在这一刻一起生效（原子性）
            conn.commit()

        finally:

            # 关闭游标
            cur.close()

    finally:

        # 关闭连接
        conn.close()


# 自定义异常类：继承 Exception，专门表示"业务规则不满足"
class BizError(Exception):
    """业务规则不满足时抛出，页面会把它当作"正常提示"而不是系统错误。"""

    # 【知识点：为什么要自定义异常】
    # 库存不足、药品已停用这类情况不是"程序坏了"，而是"业务不允许"。
    # 用专门的异常类型，上层就能区分：BizError → 页面弹个友好提示；
    # 其他异常 → 记录日志、提示系统错误。
