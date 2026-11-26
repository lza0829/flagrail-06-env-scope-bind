"""回读：校验边缘域、跨域标记与授权时效，过期授权在回读侧同样失败闭。"""

import json
from datetime import datetime, timezone

from fastapi import HTTPException

from app.db import 连接
from app.services import 环境域


def 此刻() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def 边缘名册一致(conn, 边缘键: str, 边缘域: str) -> bool:
    """若已建边缘名册则校验；未建视为无约束（名册门槛另题）。"""
    表 = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='边缘名册'"
    ).fetchone()
    if not 表:
        return True
    行 = conn.execute("SELECT 域 FROM 边缘名册 WHERE 边缘键=?", (边缘键,)).fetchone()
    return not 行 or (行["域"] or "prod") == 边缘域


def 记回读(体: dict) -> dict:
    票号 = 体["票号"]
    边缘键 = 体["边缘键"]
    边缘域 = (体.get("边缘域") or "prod").strip()
    conn = 连接()
    try:
        ticket = conn.execute("SELECT * FROM 下发票 WHERE 票号=?", (票号,)).fetchone()
        if not ticket:
            raise HTTPException(400, f"下发票不存在:{票号}")
        if ticket["状态"] not in ("已下发", "回滚中"):
            raise HTTPException(400, f"票状态不可回读:{ticket['状态']}")

        票域 = ticket["票域"] or "prod"
        桶域 = ticket["桶域"] or "prod"
        标记 = int(ticket["跨域标记"] or 0)
        授权号 = ticket["授权号"] or ""

        # 边缘域必须落在桶域；跨域票还需在票面授权仍有效时才能回读
        if not 边缘名册一致(conn, 边缘键, 边缘域):
            raise HTTPException(
                409, {"拒回读": True, "理由": f"边缘域与名册不一致:{边缘域}"}
            )
        if 边缘域 != 桶域:
            raise HTTPException(
                409, {"拒回读": True, "理由": f"边缘域与桶域不一致:{边缘域}≠{桶域}"}
            )
        if 标记:
            行 = conn.execute("SELECT * FROM 跨域授权 WHERE 授权号=?", (授权号,)).fetchone()
            判 = 环境域.授权判定(行, 环境域.解析时(此刻()))
            if not 判["有效"]:
                raise HTTPException(
                    409, {"拒回读": True, "理由": f"跨域授权不可用:{判['理由']}"}
                )
            if 行["开关键"] != ticket["开关键"] or 行["票域"] != 票域 or 行["桶域"] != 桶域:
                raise HTTPException(409, {"拒回读": True, "理由": "授权与票面域不一致"})

        证号 = 体.get("证号") or f"证-{票号}-{边缘键}"
        所见 = 体.get("所见版本键") or ticket["版本键"]
        结果 = 体.get("结果", "确认")
        if 结果 == "确认" and 所见 != ticket["版本键"]:
            结果 = "偏差"
        # 跨域票回读须显式携带跨域标记，否则记偏差（标记不静默）
        if 标记 and 结果 == "确认" and not 体.get("跨域标记", False):
            结果 = "偏差"

        conn.execute(
            """INSERT INTO 回读证(
                  证号, 票号, 边缘键, 边缘域, 票域, 桶域, 跨域标记,
                  所见版本键, 结果, 回读时, 说明)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(证号) DO UPDATE SET
                 所见版本键=excluded.所见版本键, 结果=excluded.结果,
                 回读时=excluded.回读时, 说明=excluded.说明,
                 边缘域=excluded.边缘域, 票域=excluded.票域, 桶域=excluded.桶域,
                 跨域标记=excluded.跨域标记""",
            (
                证号, 票号, 边缘键, 边缘域, 票域, 桶域, 标记,
                所见, 结果, 此刻(), 体.get("说明", ""),
            ),
        )
        conn.commit()
        return {
            "证号": 证号, "结果": 结果,
            "边缘域": 边缘域, "票域": 票域, "桶域": 桶域,
            "跨域标记": 标记,
        }
    finally:
        conn.close()


def 装回读夹具(体: dict) -> dict:
    """兼容旧夹具：仍回 {批次编号, 报告:{类型:'回读', 结果}}，但走边缘域校验路径。"""
    批次 = 体.get("批次编号") or f"回读-{此刻()}"
    结果 = [环境域.试回读(记回读, item) for item in 体.get("回读") or []]
    conn = 连接()
    try:
        报告 = {"类型": "回读", "结果": 结果}
        conn.execute(
            "INSERT OR REPLACE INTO 装载批次(编号, 时刻, 报告) VALUES (?,?,?)",
            (批次, 此刻(), json.dumps(报告, ensure_ascii=False)),
        )
        conn.commit()
    finally:
        conn.close()
    return {"批次编号": 批次, "报告": 报告}
