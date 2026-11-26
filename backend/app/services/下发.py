"""环境域绑定：下发票落地域字段 + 跨域授权判定 + 失败闭。

下发路径在同一写事务内完成「版本/分桶校验 → 域裁定 → 落票」：
裁失败则不落票，只留域裁证据。并发下由 BEGIN IMMEDIATE 串行化，
过期授权在任何并发次序下都落不了票。
"""

import json
from datetime import datetime, timezone

from fastapi import HTTPException

from app.db import 连接
from app.services import 环境域


def 此刻() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _拒发(裁: dict, 票号: str, 票域: str, 桶域: str) -> HTTPException:
    return HTTPException(
        409,
        {
            "拒发": True,
            "票号": 票号,
            "票域": 票域,
            "桶域": 桶域,
            "口径": 裁["口径"],
            "授权号": 裁["授权号"],
            "理由": 裁["理由"],
        },
    )


def 下一票(体: dict) -> dict:
    版本键 = 体["版本键"]
    桶键 = 体["桶键"]
    票域 = (体.get("票域") or "prod").strip()
    授权号 = (体.get("授权号") or "").strip()
    票号 = 体.get("票号") or f"票-{版本键}-{桶键}"
    conn = 连接()
    conn.isolation_level = None  # 显式 BEGIN IMMEDIATE，保证裁定与落票同事务
    try:
        conn.execute("BEGIN IMMEDIATE")
        ver = conn.execute("SELECT * FROM 开关版本 WHERE 版本键=?", (版本键,)).fetchone()
        if not ver:
            conn.rollback()
            raise HTTPException(400, f"版本未入轨:{版本键}")
        if ver["状态"] not in ("已入轨", "灰度中"):
            conn.rollback()
            raise HTTPException(400, f"版本状态不可下发:{ver['状态']}")
        bucket = conn.execute(
            "SELECT * FROM 分桶规 WHERE 桶键=? AND 状态='生效'", (桶键,)
        ).fetchone()
        if not bucket:
            conn.rollback()
            raise HTTPException(400, f"分桶未生效:{桶键}")
        if bucket["开关键"] != ver["开关键"]:
            conn.rollback()
            raise HTTPException(400, "分桶开关键与版本不一致")

        桶域 = (bucket["域"] or "prod").strip()
        决策时 = 环境域.解析时(此刻())
        裁 = 环境域.裁定(
            conn,
            开关键=ver["开关键"],
            票域=票域,
            桶域=桶域,
            时=决策时,
            授权号=授权号,
        )
        if 裁["裁决"] != "放行":
            环境域.记裁定(conn, 票号=票号, 裁=裁, 说明="下发拒落票")
            conn.commit()
            raise _拒发(裁, 票号, 票域, 桶域)

        已 = conn.execute("SELECT * FROM 下发票 WHERE 票号=?", (票号,)).fetchone()
        if 已 and 已["状态"] == "已下发":
            same = (
                int(已["跨域标记"] or 0) == int(裁["跨域标记"])
                and (已["票域"] or "prod") == 票域
                and (已["桶域"] or "prod") == 桶域
                and (已["授权号"] or "") == 裁["授权号"]
            )
            if not same:
                坏裁 = dict(裁)
                坏裁["理由"] = "同票号域/授权与已落票不一致，拒幂等旁路"
                conn.rollback()
                raise HTTPException(409, {"拒发": True, "票号": 票号, "理由": 坏裁["理由"]})
            conn.commit()
            return {
                "票号": 票号, "状态": "已下发", "幂等": True,
                "票域": 票域, "桶域": 桶域, "跨域标记": int(已["跨域标记"] or 0),
                "授权号": 已["授权号"] or "",
            }

        conn.execute(
            """INSERT INTO 下发票(
                  票号, 开关键, 版本键, 桶键, 票域, 桶域, 跨域标记, 授权号,
                  状态, 下发时, 世代, 说明)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(票号) DO UPDATE SET
                 状态='已下发', 下发时=excluded.下发时, 世代=下发票.世代+1,
                 票域=excluded.票域, 桶域=excluded.桶域,
                 跨域标记=excluded.跨域标记, 授权号=excluded.授权号""",
            (
                票号, ver["开关键"], 版本键, 桶键, 票域, 桶域,
                int(裁["跨域标记"]), 裁["授权号"], "已下发", 此刻(), 1,
                体.get("说明", ""),
            ),
        )
        conn.execute("UPDATE 开关版本 SET 状态='灰度中' WHERE 版本键=?", (版本键,))
        环境域.记裁定(conn, 票号=票号, 裁=裁, 说明=体.get("说明", ""))
        conn.commit()
        return {
            "票号": 票号, "状态": "已下发", "幂等": False,
            "票域": 票域, "桶域": 桶域, "跨域标记": int(裁["跨域标记"]),
            "授权号": 裁["授权号"], "口径": 裁["口径"],
        }
    except HTTPException:
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def 装下发夹具(体: dict) -> dict:
    """兼容旧夹具：仍回 {批次编号, 报告:{类型:'下发', 结果}}，但走域裁定路径。"""
    批次 = 体.get("批次编号") or f"下发-{此刻()}"
    结果 = [环境域.试下发(下一票, item) for item in 体.get("下发") or []]
    conn = 连接()
    try:
        报告 = {"类型": "下发", "结果": 结果}
        conn.execute(
            "INSERT OR REPLACE INTO 装载批次(编号, 时刻, 报告) VALUES (?,?,?)",
            (批次, 此刻(), json.dumps(报告, ensure_ascii=False)),
        )
        conn.commit()
    finally:
        conn.close()
    return {"批次编号": 批次, "报告": 报告}
