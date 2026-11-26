import React, { useEffect, useState } from "react";
import { get, post, postFixture } from "../api.js";

const 夹具们 = [
  ["环域无授权跨域", "无授权跨域"],
  ["环域授权后跨域", "授权后跨域"],
  ["环域同域对照", "同域对照"],
  ["环域授权过期并发", "授权过期并发"],
  ["环域硬拦口径", "硬拦口径"],
];

export default function 环境域() {
  const [授权们, set授权们] = useState([]);
  const [裁们, set裁们] = useState([]);
  const [口径, set口径] = useState("");
  const [票号, set票号] = useState("票-xdom.auth");
  const [核, set核] = useState(null);
  const [讯, set讯] = useState("");

  const 刷 = async () => {
    const [a, b, c] = await Promise.all([get("/跨域授权"), get("/域裁"), get("/跨域口径")]);
    set授权们(a); set裁们(b); set口径(c.跨域口径);
  };

  useEffect(() => { 刷().catch((e) => set讯(String(e))); }, []);

  const 灌 = async (名) => {
    set讯("装载中…");
    try {
      const r = await postFixture(名);
      await 刷();
      set讯(`${名}：落票 ${r.落票数 ?? 0}` + (r.异常 ? ` · 异常 ${JSON.stringify(r.异常)}` : ""));
    } catch (e) { set讯(String(e)); }
  };

  const 切口径 = async (值) => {
    try { await post("/跨域口径", { 跨域口径: 值 }); await 刷(); set讯(`口径 → ${值}`); }
    catch (e) { set讯(String(e)); }
  };

  const 查核 = async () => {
    try { set核(await get(`/核证?票号=${encodeURIComponent(票号)}`)); }
    catch (e) { set讯(String(e)); }
  };

  return (
    <div>
      <h1 className="题">环境域</h1>
      <p className="述">
        桶域 / 票域 / 边缘域 / 授权凭证同票校验。跨域无授权必须失败闭且不落票；
        持有效授权可下发，但票与回读都打跨域标记。过期授权不得静默豁免。
      </p>

      <div className="行">
        <span className="mono">跨域口径：</span>
        <button className={口径 === "授权豁免" ? "信" : "次"} onClick={() => 切口径("授权豁免")}>授权豁免</button>
        <button className={口径 === "硬拦" ? "信" : "次"} onClick={() => 切口径("硬拦")}>硬拦</button>
      </div>

      <div className="行">
        {夹具们.map(([名, 标]) => (
          <button key={名} className="次" onClick={() => 灌(名)}>灌{标}</button>
        ))}
      </div>
      {讯 && <p className="讯">{讯}</p>}

      <div className="行">
        <input className="查" value={票号} onChange={(e) => set票号(e.target.value)} />
        <button onClick={查核}>核证</button>
      </div>
      {核 && (
        <div className="提示条">
          <div><b>{核.票号}</b> · <span className={"徽章 " + (核.valid ? "通" : "败")}>{核.valid ? "valid" : "invalid"}</span></div>
          <div className="mono">
            桶域 {核.桶域 || "—"} · 票域 {核.票域 || "—"} · 授权号 {核.授权号 || "—"} · 跨域标记 {核.跨域标记}
          </div>
          <div className="mono">理由 {核.理由}</div>
        </div>
      )}

      <h2 className="小题">跨域授权表</h2>
      {!授权们.length ? <div className="空态">尚无授权</div> : (
        <table>
          <thead><tr><th>授权号</th><th>开关键</th><th>票域 → 桶域</th><th>到期时</th><th>时效</th></tr></thead>
          <tbody>{授权们.map((r) => (
            <tr key={r.授权号}>
              <td>{r.授权号}</td><td>{r.开关键}</td><td>{r.票域} → {r.桶域}</td><td>{r.到期时}</td>
              <td>
                <span className={"徽章 " + (r.有效 ? "通" : r.过期 ? "败" : "败")}>
                  {r.有效 ? "有效" : r.过期 ? "过期" : r.判定?.理由 || "不可用"}
                </span>
              </td>
            </tr>
          ))}</tbody>
        </table>
      )}

      <h2 className="小题">域裁证据</h2>
      {!裁们.length ? <div className="空态">尚无裁定</div> : (
        <table>
          <thead><tr><th>#</th><th>票号</th><th>票域 → 桶域</th><th>口径</th><th>裁决</th><th>授权号</th><th>理由</th></tr></thead>
          <tbody>{裁们.map((r) => (
            <tr key={r.编号}>
              <td>{r.编号}</td><td>{r.票号}</td><td>{r.票域} → {r.桶域}</td><td>{r.口径}</td>
              <td><span className={"徽章 " + (r.裁决 === "放行" ? "通" : "败")}>{r.裁决}</span></td>
              <td>{r.授权号 || "—"}</td><td>{r.理由}</td>
            </tr>
          ))}</tbody>
        </table>
      )}
    </div>
  );
}
