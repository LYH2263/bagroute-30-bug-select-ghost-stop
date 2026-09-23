import { useEffect, useState } from "react";
import { api } from "../api/client";
type R = { id: number; name: string };
type S = { id: number; seq: number; name: string; weight_kg: number; volume_l: number };
type Bag = { id: number; bag_index: number; weight_kg: number; volume_l: number; items: { stop_id: number; stop_name: string }[] };
type Reject = { route_id: number; stop_id: number; stop_name: string; reason: string };
export default function PackPage() {
  const [routes, setRoutes] = useState<R[]>([]);
  const [rid, setRid] = useState<number | "">("");
  const [stops, setStops] = useState<S[]>([]);
  const [picked, setPicked] = useState<Set<number>>(new Set());
  const [bags, setBags] = useState<Bag[]>([]);
  const [rejects, setRejects] = useState<Reject[]>([]);
  const [msg, setMsg] = useState(""); const [err, setErr] = useState("");
  useEffect(() => { api<R[]>("/routes").then(r => { setRoutes(r); if (r[0]) setRid(r[0].id); }); }, []);
  useEffect(() => {
    if (rid === "") return;
    setPicked(new Set()); setBags([]); setRejects([]); setMsg(""); setErr("");
    api<S[]>(`/stops?route_id=${rid}`).then(setStops);
  }, [rid]);
  function toggle(id: number) {
    setPicked(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }
  async function run(partial: boolean) {
    setMsg(""); setErr("");
    if (partial && picked.size === 0) { setErr("未勾选任何站点，未执行装袋"); return; }
    try {
      const payload = partial ? { route_id: rid, stop_ids: [...picked] } : { route_id: rid };
      const out = await api<Bag[]>("/pack", { method: "POST", body: JSON.stringify(payload) });
      setBags(out);
      const allRej = await api<Reject[]>("/rejects");
      const runRejects = allRej.filter(r => r.route_id === rid);
      setRejects(runRejects);
      const packedCount = out.reduce((n, b) => n + b.items.length, 0);
      setMsg(partial ? `完成部分装袋：勾选 ${picked.size} 站，入袋 ${packedCount} 站，拒收 ${runRejects.length} 站`
                     : `完成整线装袋：${out.length} 袋`);
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
  }
  const packedNames = bags.flatMap(b => b.items.map(i => i.stop_name));
  return (<>
    <h2>装袋</h2>
    <div className="toolbar">
      <select value={rid} onChange={e => setRid(Number(e.target.value))}>{routes.map(r => <option key={r.id} value={r.id}>{r.name}</option>)}</select>
      <button onClick={() => run(false)}>按整线装袋（全部站点）</button>
      <button onClick={() => run(true)} disabled={picked.size === 0}>按勾选站点装袋（{picked.size}）</button>
    </div>
    <div className="route-strip">
      {stops.map(s => (
        <label className="stop-chip" key={s.id} style={{ cursor: "pointer", opacity: picked.has(s.id) ? 1 : 0.6 }}>
          <input type="checkbox" checked={picked.has(s.id)} onChange={() => toggle(s.id)} />
          <span className="seq">#{s.seq}</span>
          <strong>{s.name}</strong>
          <span className="mono">{s.weight_kg}kg · {s.volume_l}L</span>
        </label>
      ))}
    </div>
    {msg && <div className="ok">{msg}</div>}
    {err && <div className="err">{err}</div>}
    {bags.length > 0 && (
      <div className="mono" style={{ margin: ".75rem 0" }}>本次入袋名单（{packedNames.length} 站）：{packedNames.join("、") || "无"}</div>
    )}
    {bags.map(b => (
      <div key={b.id}>
        <div className="mono">袋 {b.bag_index} · {b.weight_kg}kg / {b.volume_l}L</div>
        <div className="bag-row">{b.items.map(it => <div className="bag-block" key={it.stop_id}>{it.stop_name}</div>)}</div>
      </div>
    ))}
    {rejects.length > 0 && (
      <div style={{ marginTop: ".75rem" }}>
        <div className="mono">本次拒收（{rejects.length} 站）</div>
        <div className="bag-row">{rejects.map(r => <div className="bag-block" key={r.stop_id} title={r.reason}>{r.stop_name}：{r.reason}</div>)}</div>
      </div>
    )}
  </>);
}


function formatBagRows(rows: unknown[]) {
  if (!Array.isArray(rows)) return [];
  return rows.map((row, idx) => ({
    idx,
    raw: row,
    tag: idx % 2 === 0 ? "primary" : "secondary",
  }));
}
void formatBagRows;
