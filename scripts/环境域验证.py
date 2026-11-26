#!/usr/bin/env python3
"""环境域绑定 · 端到端自动化验证。

起一个临时库的 uvicorn，按夹具断言：
  1. 无授权跨域 → 拒发，不落票
  2. 授权后跨域 → 落票 + 跨域标记，核证 valid
  3. 同域对照 → 落票，无跨域标记
  4. 授权过期并发 → 全部拒发，零落票
  5. 硬拦口径 → 即使持有效授权也拒发（与授权豁免结局可区分）
  6. 回读边缘域不一致 → 拒记
  7. 跨域票回读不带标记 → 记偏差
  8. 授权表可查过期
只用标准库；需要 backend 依赖已安装（uvicorn/fastapi）。
"""

import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

根 = Path(__file__).resolve().parent.parent
后端 = 根 / "backend"
夹具 = 根 / "fixtures"

失败 = []
通过 = []


def 记(名: str, 条件: bool, 细节: str = "") -> None:
    (通过 if 条件 else 失败).append(名 + (f"（{细节}）" if 细节 else ""))
    print(("  ✓ " if 条件 else "  ✗ ") + 名 + (f" — {细节}" if 细节 else ""))


def 空端口() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    口 = s.getsockname()[1]
    s.close()
    return 口


class 客户端:
    def __init__(self, 基: str):
        self.基 = 基.rstrip("/")

    def _开(self, 路径: str, 方法: str, 体=None):
        # 路径含中文路由段，先编码；查询串若已由 urlencode 编码则不再二次编码
        if "?" in 路径:
            路, 查 = 路径.split("?", 1)
            url = self.基 + urllib.parse.quote(路, safe="/") + "?" + 查
        else:
            url = self.基 + urllib.parse.quote(路径, safe="/")
        data = None if 体 is None else json.dumps(体, ensure_ascii=False).encode()
        req = urllib.request.Request(url, data=data, method=方法)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, json.loads(r.read().decode() or "null")
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read().decode() or "null")
            except Exception:
                return e.code, None

    def get(self, 路径: str, 参数=None):
        if 参数:
            路径 = 路径 + "?" + urllib.parse.urlencode(参数)
        return self._开(路径, "GET")

    def post(self, 路径: str, 体=None):
        return self._开(路径, "POST", 体)

    def 夹具(self, 名: str):
        return self._开("/api/装载/夹具?" + urllib.parse.urlencode({"名": 名}), "POST", None)


def 起服务(数据目录: str, 端口: int) -> subprocess.Popen:
    环境 = dict(os.environ)
    环境["数据目录"] = 数据目录
    环境["夹具目录"] = str(夹具)
    return subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(端口)],
        cwd=str(后端),
        env=环境,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )


def 等就绪(c: 客户端, 超时=25) -> bool:
    截 = time.time() + 超时
    while time.time() < 截:
        try:
            st, _ = c.get("/api/健康")
            if st == 200:
                return True
        except Exception:
            pass
        time.sleep(0.3)
    return False


def 步报告(报告: dict, 键: str):
    for 步 in 报告.get("步骤", []):
        if 键 in 步:
            return 步[键]
    return None


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="flagrail-env-") as 目录:
        端口 = 空端口()
        进程 = 起服务(目录, 端口)
        c = 客户端(f"http://127.0.0.1:{端口}")
        try:
            if not 等就绪(c):
                print("服务未就绪"); return 2

            print("[1] 无授权跨域：必须失败闭且不落票")
            r = c.夹具("环域无授权跨域")[1]
            下发 = 步报告(r, "下发") or [{}]
            记("拒发状态码 409", 下发[0].get("状态码") == 409, str(下发[0].get("说明")))
            记("无凭证不落票", (r.get("落票数") or 0) == 0)
            记("拒发原因含授权缺失", "无授权" in str(下发[0].get("说明")))
            记("核证无此票", c.get("/api/核证", {"票号": "票-xdom.noauth"})[1]["valid"] is False)

            print("[2] 写入授权后跨域：应落票且核证见跨域标记")
            r = c.夹具("环域授权后跨域")[1]
            记("落票 1 张", (r.get("落票数") or 0) == 1)
            核 = c.get("/api/核证", {"票号": "票-xdom.auth"})[1]
            记("核证 valid", 核["valid"] is True, 核["理由"])
            记("核证返回桶域", 核["桶域"] == "pre", 核["桶域"])
            记("核证返回票域", 核["票域"] == "prod", 核["票域"])
            记("核证返回授权号", 核["授权号"] == "XA-xdom.auth-prod-pre-01", 核["授权号"])
            记("核证返回跨域标记 1", 核["跨域标记"] == 1)
            记("回读带跨域标记", bool(核["回读"]) and int(核["回读"][0]["结果"] == "确认") == 1)
            回读 = 步报告(r, "回读") or [{}]
            记("回读确认", all(x.get("结果") == "确认" for x in 回读))

            print("[3] 同域对照：直接放行、无标记")
            r = c.夹具("环域同域对照")[1]
            记("同域落票", (r.get("落票数") or 0) == 1)
            核 = c.get("/api/核证", {"票号": "票-xdom.same"})[1]
            记("同域核证 valid", 核["valid"] is True)
            记("同域跨域标记 0", 核["跨域标记"] == 0)

            print("[4] 授权过期 + 并发下发：全部失败，零落票")
            r = c.夹具("环域授权过期并发")[1]
            并发 = 步报告(r, "并发下发") or []
            记("并发条数 4", len(并发) == 4, str(len(并发)))
            记("并发全部拒发", all(x.get("失败") for x in 并发))
            并发落票 = 步报告(r, "并发落票数")
            记("并发落票 0", 并发落票 == 0, str(并发落票))
            记("每张并发票核证均 invalid", all(
                c.get("/api/核证", {"票号": f"票-xdom.expired-{i}"})[1]["valid"] is False
                for i in range(4)
            ))

            print("[5] 授权表可查过期")
            授权 = c.get("/api/跨域授权")[1]
            过期行 = [a for a in 授权 if a["授权号"] == "XA-xdom.expired-prod-pre-01"]
            记("授权表含过期行", len(过期行) == 1)
            记("过期标记 true", 过期行 and 过期行[0]["过期"] is True)
            记("过期行有效 false", 过期行 and 过期行[0]["有效"] is False)

            print("[6] 两口径结局可区分：同输入、有效授权")
            r = c.夹具("环域硬拦口径")[1]
            记("硬拦口径拒发", (r.get("落票数") or 0) == 0)
            记("硬拦拒发原因", "硬拦" in str(步报告(r, "下发")))
            记("硬拦下核证 invalid", c.get("/api/核证", {"票号": "票-xdom.hard"})[1]["valid"] is False)
            c.post("/api/跨域口径", {"跨域口径": "授权豁免"})
            r = c.夹具("环域授权后跨域")[1]
            记("切回授权豁免仍可放行（幂等）", (r.get("落票数") or 0) == 1)
            # 同一张硬拦票在授权豁免口径下可新开票号放行，证明口径分叉真实
            r = c._开("/api/下发", "POST", {
                "票号": "票-xdom.hard-豁免对照",
                "版本键": "xdom.hard@1",
                "桶键": "xdom.hard-pre10",
                "票域": "prod",
                "授权号": "XA-xdom.hard-prod-pre-01",
            })
            记("授权豁免口径下同一域对可放行", r[0] == 200 and r[1].get("跨域标记") == 1, str(r[1]))

            print("[7] 回读须校验边缘域")
            坏 = c._开("/api/回读", "POST", {
                "证号": "证-坏边缘", "票号": "票-xdom.auth", "边缘键": "edge-prod-x",
                "边缘域": "prod", "所见版本键": "xdom.auth@1", "结果": "确认",
            })
            记("边缘域不一致拒记", 坏[0] == 409, str(坏[1]))
            缺标 = c._开("/api/回读", "POST", {
                "证号": "证-缺标记", "票号": "票-xdom.auth", "边缘键": "edge-pre-c",
                "边缘域": "pre", "所见版本键": "xdom.auth@1", "结果": "确认",
            })
            记("跨域票回读不带标记记偏差", 缺标[0] == 200 and 缺标[1].get("结果") == "偏差", str(缺标[1]))

            print("[8] 幂等旁路防护：同票号换域不得静默复用")
            旁 = c._开("/api/下发", "POST", {
                "票号": "票-xdom.auth", "版本键": "xdom.auth@1", "桶键": "xdom.auth-pre10",
                "票域": "pre", "授权号": "XA-xdom.auth-prod-pre-01",
            })
            记("同票号跨口径改写被拒", 旁[0] == 409, str(旁[1]))

            print("[9] 轨贯验（含域/标记/授权一致性）")
            g = c.post("/api/贯验")[1]
            记("贯验通过", g["是否通过"] is True, g["说明"])
        finally:
            进程.terminate()
            try:
                进程.wait(timeout=10)
            except subprocess.TimeoutExpired:
                进程.kill()

    print(f"\n通过 {len(通过)} · 失败 {len(失败)}")
    if 失败:
        for f in 失败:
            print("  FAIL:", f)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
