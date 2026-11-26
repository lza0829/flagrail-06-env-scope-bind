"""环境域绑定：桶域 / 票域 / 边缘域 / 跨域授权 同票校验。

两种正确性口径（站规 `跨域口径`）：
- `授权豁免`：域不一致时，持有效且未过期的跨域授权可下发；票与回读均打跨域标记。
- `硬拦`：只要域不一致即拒发，即便持授权也不放行。

两种口径的共同底线：无有效授权 → 拒发且不落票；授权过期 → 拒发且不落票。
"""

import json
from datetime import datetime, timezone

from fastapi import HTTPException

from app.db import 连接

口径_授权豁免 = "授权豁免"
口径_硬拦 = "硬拦"
合法口径 = {口径_授权豁免, 口径_硬拦}

时格式 = "%Y-%m-%dT%H:%M:%SZ"


def 此刻() -> str:
    return datetime.now(timezone.utc).strftime(时格式)


def 解析时(文本: str) -> datetime | None:
    if not 文本:
        return None
    try:
        return datetime.strptime(文本, 时格式).replace(tzinfo=timezone.utc)
    except ValueError:
        try:
            d = datetime.fromisoformat(文本.replace("Z", "+00:00"))
            return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
        except ValueError:
            return None


def 取口径(conn) -> str:
    r = conn.execute("SELECT 值 FROM 站规 WHERE 键='跨域口径'").fetchone()
    口径 = (r["值"] if r else "") or 口径_授权豁免
    return 口径 if 口径 in 合法口径 else 口径_授权豁免


def 设口径(口径: str) -> dict:
    if 口径 not in 合法口径:
        raise HTTPException(400, f"口径非法:{口径}")
    conn = 连接()
    try:
        conn.execute(
            "INSERT INTO 站规(键, 值) VALUES ('跨域口径', ?) "
            "ON CONFLICT(键) DO UPDATE SET 值=excluded.值",
            (口径,),
        )
        conn.commit()
        return {"跨域口径": 口径}
    finally:
        conn.close()


def 授权判定(授权行, 时: datetime) -> dict:
    """对一行授权做时点判定；任何异常都按 fail-closed 处理。"""
    if not 授权行:
        return {"存在": False, "有效": False, "过期": False, "理由": "授权不存在"}
    到期 = 解析时(授权行["到期时"])
    if 到期 is None:
        return {"存在": True, "有效": False, "过期": True, "理由": "到期时不可解析"}
    if 授权行["状态"] != "有效":
        return {"存在": True, "有效": False, "过期": False, "理由": f"授权状态:{授权行['状态']}"}
    if 到期 <= 时:
        return {"存在": True, "有效": False, "过期": True, "理由": "授权已过期"}
    return {"存在": True, "有效": True, "过期": False, "理由": "授权有效"}


def 找授权(conn, 开关键: str, 票域: str, 桶域: str, 时: datetime, 指定号: str = "") -> tuple:
    """返回 (授权行或 None, 判定或 None, 查找说明)。指定号时只认该号，不做兜底。"""
    if 指定号:
        行 = conn.execute(
            "SELECT * FROM 跨域授权 WHERE 授权号=?", (指定号,)
        ).fetchone()
        判 = 授权判定(行, 时)
        if not 判["有效"]:
            return 行, 判, f"指定授权不可用:{判['理由']}"
        if 行["开关键"] != 开关键:
            return 行, 判, "授权开关键不匹配"
        if 行["票域"] != 票域 or 行["桶域"] != 桶域:
            return 行, 判, "授权域与票/桶不一致"
        return 行, 判, ""

    候选 = conn.execute(
        """SELECT * FROM 跨域授权
           WHERE 开关键=? AND 票域=? AND 桶域=? AND 状态='有效'
           ORDER BY 到期时 DESC""",
        (开关键, 票域, 桶域),
    ).fetchall()
    for 行 in 候选:
        判 = 授权判定(行, 时)
        if 判["有效"]:
            return 行, 判, ""
    if 候选:
        行 = 候选[0]
        return 行, 授权判定(行, 时), "授权不可用"
    return None, None, "未找到跨域授权"


def 裁定(conn, *, 开关键: str, 票域: str, 桶域: str, 时: datetime, 授权号: str = "") -> dict:
    """纯判定，不写 DB。返回 裁决 / 口径 / 跨域标记 / 授权号 / 理由。"""
    口径 = 取口径(conn)
    跨域 = 票域 != 桶域
    if not 跨域:
        return {
            "裁决": "放行", "口径": 口径, "跨域标记": 0, "授权号": "",
            "理由": "同域对照", "票域": 票域, "桶域": 桶域,
        }
    if 口径 == 口径_硬拦:
        return {
            "裁决": "拒发", "口径": 口径, "跨域标记": 0, "授权号": "",
            "理由": "域不一致硬拦", "票域": 票域, "桶域": 桶域,
        }
    行, 判, 找说明 = 找授权(conn, 开关键, 票域, 桶域, 时, 授权号)
    签发号 = 行["授权号"] if 行 else 授权号
    if not 判 or not 判["有效"]:
        理由 = "跨域无授权" if 判 is None else f"跨域授权不可用:{判['理由']}"
        if 找说明:
            理由 = f"{理由}（{找说明}）"
        return {
            "裁决": "拒发", "口径": 口径, "跨域标记": 0, "授权号": 签发号,
            "理由": 理由, "票域": 票域, "桶域": 桶域,
        }
    return {
        "裁决": "放行", "口径": 口径, "跨域标记": 1, "授权号": 签发号,
        "理由": "授权豁免放行", "票域": 票域, "桶域": 桶域,
    }


def 记裁定(conn, *, 票号: str, 裁: dict, 说明: str = "") -> None:
    conn.execute(
        """INSERT INTO 域裁(时刻, 票号, 票域, 桶域, 授权号, 口径, 裁决, 理由, 说明)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (
            此刻(), 票号, 裁["票域"], 裁["桶域"], 裁["授权号"],
            裁["口径"], 裁["裁决"], 裁["理由"], 说明,
        ),
    )


def 列授权() -> list:
    conn = 连接()
    try:
        时 = 解析时(此刻())
        出 = []
        for r in conn.execute("SELECT * FROM 跨域授权 ORDER BY 签发时 DESC, 授权号"):
            d = dict(r)
            d["判定"] = 授权判定(r, 时)
            d["过期"] = bool(d["判定"].get("过期"))
            d["有效"] = bool(d["判定"].get("有效"))
            出.append(d)
        return 出
    finally:
        conn.close()


def 写授权(体: dict) -> dict:
    授权号 = (体.get("授权号") or "").strip()
    if not 授权号:
        raise HTTPException(400, "授权号必填")
    开关键 = 体.get("开关键", "")
    票域 = 体.get("票域", "")
    桶域 = 体.get("桶域", "")
    if not (开关键 and 票域 and 桶域):
        raise HTTPException(400, "开关键/票域/桶域必填")
    到期时 = 体.get("到期时", "")
    if 解析时(到期时) is None:
        raise HTTPException(400, f"到期时不可解析:{到期时}")
    状态 = 体.get("状态", "有效")
    conn = 连接()
    try:
        conn.execute(
            """INSERT INTO 跨域授权(授权号, 开关键, 票域, 桶域, 状态, 到期时, 签发时, 说明)
               VALUES (?,?,?,?,?,?,?,?)
               ON CONFLICT(授权号) DO UPDATE SET
                 开关键=excluded.开关键, 票域=excluded.票域, 桶域=excluded.桶域,
                 状态=excluded.状态, 到期时=excluded.到期时, 说明=excluded.说明""",
            (授权号, 开关键, 票域, 桶域, 状态, 到期时, 此刻(), 体.get("说明", "")),
        )
        conn.commit()
        return {"授权号": 授权号, "状态": 状态, "到期时": 到期时}
    finally:
        conn.close()


def 核证(票号: str) -> dict:
    """核证返回桶域、票域、授权号、跨域标记、valid，并给出判定理由。"""
    conn = 连接()
    try:
        票 = conn.execute("SELECT * FROM 下发票 WHERE 票号=?", (票号,)).fetchone()
        if not 票:
            return {
                "票号": 票号, "桶域": "", "票域": "", "授权号": "",
                "跨域标记": 0, "valid": False, "理由": "下发票不存在",
            }
        票域 = 票["票域"]
        桶域 = 票["桶域"]
        标记 = int(票["跨域标记"] or 0)
        授权号 = 票["授权号"] or ""
        理由 = ""
        有效 = True
        授权态 = None
        if 票域 == 桶域:
            if 标记:
                有效, 理由 = False, "同域票不应有跨域标记"
        else:
            if not 标记:
                有效, 理由 = False, "跨域票缺跨域标记"
            elif not 授权号:
                有效, 理由 = False, "跨域票缺授权号"
            else:
                行 = conn.execute(
                    "SELECT * FROM 跨域授权 WHERE 授权号=?", (授权号,)
                ).fetchone()
                授权态 = 授权判定(行, 解析时(此刻()))
                if not 授权态["有效"]:
                    有效, 理由 = False, f"授权不可用:{授权态['理由']}"
                elif 行["开关键"] != 票["开关键"] or 行["票域"] != 票域 or 行["桶域"] != 桶域:
                    有效, 理由 = False, "授权与票面域不一致"
        if 票["状态"] not in ("已下发", "回滚中", "已回滚"):
            有效, 理由 = False, f"票状态不可核:{票['状态']}"

        回读们 = [
            dict(r)
            for r in conn.execute(
                "SELECT 证号, 边缘键, 边缘域, 结果 FROM 回读证 WHERE 票号=? ORDER BY 回读时 DESC LIMIT 20",
                (票号,),
            )
        ]
        return {
            "票号": 票号,
            "桶域": 桶域,
            "票域": 票域,
            "授权号": 授权号,
            "跨域标记": 标记,
            "valid": 有效,
            "理由": 理由 or "域与授权一致",
            "状态": 票["状态"],
            "授权判定": 授权态,
            "回读": 回读们,
        }
    finally:
        conn.close()


def 装夹具(体: dict) -> dict:
    """环境域夹具：口径 → 入轨/分桶 → 授权 → 下发（可并发）→ 回读。"""
    from concurrent.futures import ThreadPoolExecutor

    from app.services import 入轨, 分桶, 下发, 回读

    批次 = 体.get("批次编号") or f"环域-{此刻()}"
    报告 = {"类型": "环境域", "批次编号": 批次, "步骤": []}
    try:
        if 体.get("口径"):
            报告["步骤"].append({"口径": 设口径(体["口径"])})
        if 体.get("版本"):
            报告["步骤"].append({"入轨": 入轨.入一批({"批次编号": f"{批次}-入轨", "版本": 体["版本"]})})
        if 体.get("分桶"):
            报告["步骤"].append(
                {"分桶": [分桶.写桶(x) for x in 体["分桶"]]}
            )
        授权结果 = []
        for a in 体.get("授权") or []:
            授权结果.append(写授权(a))
        if 授权结果:
            报告["步骤"].append({"授权": 授权结果})

        下发结果 = []
        for item in 体.get("下发") or []:
            下发结果.append(试下发(下发.下一票, item))
        if 下发结果:
            报告["步骤"].append({"下发": 下发结果})

        并发结果 = None
        并发体 = 体.get("并发下发")
        if 并发体:
            条数 = int(并发体.get("条数", 4))
            模板 = 并发体.get("下发") or {}
            项 = []
            for i in range(条数):
                x = dict(模板)
                x["票号"] = (模板.get("票号") or "并发票") + f"-{i}"
                x["说明"] = 模板.get("说明", "") + f"（并发#{i}）"
                项.append(x)
            with ThreadPoolExecutor(max_workers=条数) as 池:
                并发结果 = list(池.map(lambda x: 试下发(下发.下一票, x), 项))
            报告["步骤"].append({"并发下发": 并发结果})
            报告["步骤"].append(
                {"并发落票数": 落票数([x.get("票号", "") for x in 项])}
            )

        回读结果 = []
        for item in 体.get("回读") or []:
            回读结果.append(试回读(回读.记回读, item))
        if 回读结果:
            报告["步骤"].append({"回读": 回读结果})

        报告["落票数"] = 落票数(涉及票号(报告))
    except HTTPException as e:
        报告["异常"] = {"状态": e.status_code, "说明": e.detail}
    conn = 连接()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO 装载批次(编号, 时刻, 报告) VALUES (?,?,?)",
            (批次, 此刻(), json.dumps(报告, ensure_ascii=False)),
        )
        conn.commit()
    finally:
        conn.close()
    return 报告


def 试下发(函数, 项: dict) -> dict:
    try:
        出 = 函数(项)
        出 = dict(出)
        出["票号"] = 项.get("票号", "")
        return 出
    except HTTPException as e:
        return {
            "票号": 项.get("票号", ""),
            "失败": True,
            "状态码": e.status_code,
            "说明": e.detail,
        }


def 试回读(函数, 项: dict) -> dict:
    try:
        出 = 函数(项)
        出 = dict(出)
        出["票号"] = 项.get("票号", "")
        出["边缘键"] = 项.get("边缘键", "")
        return 出
    except HTTPException as e:
        return {
            "票号": 项.get("票号", ""),
            "边缘键": 项.get("边缘键", ""),
            "失败": True,
            "状态码": e.status_code,
            "说明": e.detail,
        }


def 落票数(票号集) -> int:
    conn = 连接()
    try:
        数 = 0
        for 号 in 票号集:
            if 号 and conn.execute("SELECT 1 FROM 下发票 WHERE 票号=?", (号,)).fetchone():
                数 += 1
        return 数
    finally:
        conn.close()


def 涉及票号(报告: dict) -> list:
    号 = set()
    for 步 in 报告.get("步骤", []):
        for 键 in ("下发", "回读", "并发下发"):
            for x in 步.get(键) or []:
                if x.get("票号"):
                    号.add(x["票号"])
    return sorted(号)
