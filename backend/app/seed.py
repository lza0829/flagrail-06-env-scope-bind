from datetime import datetime, timezone

from app.db import 连接

建表 = """
CREATE TABLE IF NOT EXISTS 站规 (
  键 TEXT PRIMARY KEY,
  值 TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS 开关版本 (
  版本键 TEXT PRIMARY KEY,
  开关键 TEXT NOT NULL,
  版本号 INTEGER NOT NULL,
  载荷 TEXT NOT NULL DEFAULT '{}',
  状态 TEXT NOT NULL DEFAULT '已入轨',
  入轨时 TEXT NOT NULL,
  说明 TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS 分桶规 (
  桶键 TEXT PRIMARY KEY,
  开关键 TEXT NOT NULL,
  桶类型 TEXT NOT NULL,
  规则 TEXT NOT NULL DEFAULT '{}',
  域 TEXT NOT NULL DEFAULT 'prod',
  状态 TEXT NOT NULL DEFAULT '生效',
  更新时 TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS 下发票 (
  票号 TEXT PRIMARY KEY,
  开关键 TEXT NOT NULL,
  版本键 TEXT NOT NULL,
  桶键 TEXT NOT NULL,
  票域 TEXT NOT NULL DEFAULT 'prod',
  桶域 TEXT NOT NULL DEFAULT 'prod',
  跨域标记 INTEGER NOT NULL DEFAULT 0,
  授权号 TEXT NOT NULL DEFAULT '',
  状态 TEXT NOT NULL DEFAULT '待下发',
  下发时 TEXT NOT NULL DEFAULT '',
  世代 INTEGER NOT NULL DEFAULT 1,
  说明 TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS 回读证 (
  证号 TEXT PRIMARY KEY,
  票号 TEXT NOT NULL,
  边缘键 TEXT NOT NULL,
  边缘域 TEXT NOT NULL DEFAULT 'prod',
  票域 TEXT NOT NULL DEFAULT 'prod',
  桶域 TEXT NOT NULL DEFAULT 'prod',
  跨域标记 INTEGER NOT NULL DEFAULT 0,
  所见版本键 TEXT NOT NULL DEFAULT '',
  结果 TEXT NOT NULL,
  回读时 TEXT NOT NULL,
  说明 TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS 跨域授权 (
  授权号 TEXT PRIMARY KEY,
  开关键 TEXT NOT NULL,
  票域 TEXT NOT NULL,
  桶域 TEXT NOT NULL,
  状态 TEXT NOT NULL DEFAULT '有效',
  到期时 TEXT NOT NULL,
  签发时 TEXT NOT NULL,
  说明 TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS 域裁 (
  编号 INTEGER PRIMARY KEY AUTOINCREMENT,
  时刻 TEXT NOT NULL,
  票号 TEXT NOT NULL,
  票域 TEXT NOT NULL,
  桶域 TEXT NOT NULL,
  授权号 TEXT NOT NULL DEFAULT '',
  口径 TEXT NOT NULL,
  裁决 TEXT NOT NULL,
  理由 TEXT NOT NULL DEFAULT '',
  说明 TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS 命运账 (
  编号 INTEGER PRIMARY KEY AUTOINCREMENT,
  对象类型 TEXT NOT NULL,
  对象键 TEXT NOT NULL,
  命运 TEXT NOT NULL,
  时刻 TEXT NOT NULL,
  说明 TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS 装载批次 (
  编号 TEXT PRIMARY KEY,
  时刻 TEXT NOT NULL,
  报告 TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS 轨贯验 (
  编号 INTEGER PRIMARY KEY AUTOINCREMENT,
  时刻 TEXT NOT NULL,
  版本数 INTEGER NOT NULL,
  下发数 INTEGER NOT NULL,
  回读数 INTEGER NOT NULL,
  命运数 INTEGER NOT NULL,
  是否通过 INTEGER NOT NULL,
  说明 TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS 空桩 (
  名 TEXT PRIMARY KEY,
  状态 TEXT NOT NULL DEFAULT '空桩'
);
"""

补列 = [
    ("分桶规", "域", "域 TEXT NOT NULL DEFAULT 'prod'"),
    ("下发票", "票域", "票域 TEXT NOT NULL DEFAULT 'prod'"),
    ("下发票", "桶域", "桶域 TEXT NOT NULL DEFAULT 'prod'"),
    ("下发票", "跨域标记", "跨域标记 INTEGER NOT NULL DEFAULT 0"),
    ("下发票", "授权号", "授权号 TEXT NOT NULL DEFAULT ''"),
    ("回读证", "边缘域", "边缘域 TEXT NOT NULL DEFAULT 'prod'"),
    ("回读证", "票域", "票域 TEXT NOT NULL DEFAULT 'prod'"),
    ("回读证", "桶域", "桶域 TEXT NOT NULL DEFAULT 'prod'"),
    ("回读证", "跨域标记", "跨域标记 INTEGER NOT NULL DEFAULT 0"),
]


def 此刻() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def 补旧列(conn) -> None:
    # 旧骨架里跨域授权用「源域/目标域」，统一为票域/桶域
    授权列 = {r["name"] for r in conn.execute("PRAGMA table_info(跨域授权)")}
    if "源域" in 授权列 and "票域" not in 授权列:
        conn.execute("ALTER TABLE 跨域授权 RENAME COLUMN 源域 TO 票域")
    if "目标域" in 授权列 and "桶域" not in 授权列:
        conn.execute("ALTER TABLE 跨域授权 RENAME COLUMN 目标域 TO 桶域")
    for 表, 列, 定义 in 补列:
        已有 = {r["name"] for r in conn.execute(f"PRAGMA table_info({表})")}
        if 列 not in 已有:
            conn.execute(f"ALTER TABLE {表} ADD COLUMN {定义}")


def 初始化库() -> None:
    conn = 连接()
    try:
        conn.executescript(建表)
        补旧列(conn)
        for k, v in {
            "站名": "灰度轨",
            "领域": "基础设施",
            "子方向": "中间件",
            "默认灰度比": "10",
            "回读齐套策略": "全边缘确认才可晋升",
            "骨架": "开关版本入轨→分桶灰度下发→回读确认→失效/回滚互证",
            "跨域口径": "授权豁免",
            "边缘域校验": "是",
        }.items():
            conn.execute(
                "INSERT OR IGNORE INTO 站规(键, 值) VALUES (?, ?)", (k, v)
            )
        for name in [
            "分桶比例冲突", "名单与百分比互斥", "回读超时旁置", "半截下发回滚",
            "双版本同键并发", "回读偏差拒升全量", "环境桶隔离", "失效后幽灵回读",
            "下发票幂等键", "回滚窗互斥", "变体载荷契约", "边缘时钟漂移",
            "分桶重切", "全量晋升闸", "影子轨对照", "配置世代戳",
            "回读仲裁策略", "失效写闸", "跨环境串扰", "轨贯验三联",
            "环境域绑定",
        ]:
            conn.execute(
                "INSERT OR IGNORE INTO 空桩(名, 状态) VALUES (?, '空桩')", (name,)
            )
        # 环境域绑定本批落地：空桩表保留名录，但状态标为已落地
        conn.execute("UPDATE 空桩 SET 状态='已落地' WHERE 名='环境域绑定'")
        conn.commit()
    finally:
        conn.close()
