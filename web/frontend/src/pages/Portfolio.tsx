import { useEffect, useState } from 'react'
import { api } from '../api/client'

interface Pos {
  ts_code: string
  name?: string
  cost?: number
  shares?: number
  stop_loss?: number
  note?: string
}

interface Alert {
  ts_code: string
  name?: string
  price?: number
  stop_loss?: number
  status: string
  msg: string
}

export default function Portfolio() {
  const [positions, setPositions] = useState<Pos[]>([])
  const [alerts, setAlerts] = useState<Alert[]>([])
  const [prices, setPrices] = useState<Record<string, number>>({})
  const [err, setErr] = useState('')
  const [form, setForm] = useState({ ts_code: '', name: '', cost: '', stop_loss: '', shares: '' })

  const load = () => {
    api.portfolio()
      .then((r) => {
        setPositions((r.portfolio?.positions as Pos[]) || [])
        setAlerts((r.alerts as Alert[]) || [])
        setPrices(r.prices || {})
      })
      .catch((e) => setErr(String(e)))
  }
  useEffect(load, [])

  const onAdd = async () => {
    if (!form.ts_code.trim()) return
    try {
      await api.portfolioUpsert({
        action: 'upsert',
        ts_code: form.ts_code.trim().toUpperCase(),
        name: form.name,
        cost: form.cost ? Number(form.cost) : undefined,
        stop_loss: form.stop_loss ? Number(form.stop_loss) : undefined,
        shares: form.shares ? Number(form.shares) : undefined,
      })
      setForm({ ts_code: '', name: '', cost: '', stop_loss: '', shares: '' })
      load()
    } catch (e) {
      setErr(String(e))
    }
  }

  const onRemove = async (code: string) => {
    try {
      await api.portfolioUpsert({ action: 'remove', ts_code: code })
      load()
    } catch (e) {
      setErr(String(e))
    }
  }

  return (
    <div>
      <h2 style={{ marginTop: 0 }}>📋 持仓与止损</h2>
      <p className="muted">本地 JSON（runtime/portfolio.json）。每日对照最新收盘价检查是否触发止损。</p>

      {err && <div className="err">{err}</div>}

      <div className="card" style={{ marginBottom: 16, padding: 16 }}>
        <div className="row" style={{ flexWrap: 'wrap', gap: 8 }}>
          <input placeholder="代码 000001.SZ" value={form.ts_code}
            onChange={(e) => setForm({ ...form, ts_code: e.target.value })}
            style={inp} />
          <input placeholder="名称" value={form.name}
            onChange={(e) => setForm({ ...form, name: e.target.value })}
            style={inp} />
          <input placeholder="成本" value={form.cost}
            onChange={(e) => setForm({ ...form, cost: e.target.value })}
            style={{ ...inp, width: 90 }} />
          <input placeholder="止损" value={form.stop_loss}
            onChange={(e) => setForm({ ...form, stop_loss: e.target.value })}
            style={{ ...inp, width: 90 }} />
          <input placeholder="股数" value={form.shares}
            onChange={(e) => setForm({ ...form, shares: e.target.value })}
            style={{ ...inp, width: 90 }} />
          <button className="btn primary" onClick={onAdd}>添加/更新</button>
          <button className="btn" onClick={load}>刷新检查</button>
        </div>
      </div>

      <div className="card" style={{ marginBottom: 16, padding: 0, overflow: 'hidden' }}>
        <table className="data-table">
          <thead>
            <tr>
              <th>代码</th><th>名称</th><th>成本</th><th>现价</th><th>止损</th><th>状态</th><th></th>
            </tr>
          </thead>
          <tbody>
            {positions.map((p) => {
              const al = alerts.find((a) => a.ts_code === p.ts_code)
              const px = prices[p.ts_code]
              return (
                <tr key={p.ts_code}>
                  <td>{p.ts_code}</td>
                  <td>{p.name || '—'}</td>
                  <td>{p.cost ?? '—'}</td>
                  <td>{px != null ? px.toFixed(2) : '—'}</td>
                  <td>{p.stop_loss ?? '—'}</td>
                  <td>
                    {al ? (
                      <span className={al.status === 'STOP_HIT' ? 'badge badge-danger' : 'badge badge-ok'}>
                        {al.msg}
                      </span>
                    ) : '—'}
                  </td>
                  <td><button className="btn" onClick={() => onRemove(p.ts_code)}>删除</button></td>
                </tr>
              )
            })}
            {!positions.length && (
              <tr><td colSpan={7} className="muted" style={{ textAlign: 'center', padding: 24 }}>暂无持仓</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}

const inp: Record<string, string | number> = {
  background: 'var(--surface-2)',
  border: '1px solid var(--border)',
  color: 'var(--text)',
  borderRadius: 6,
  padding: '6px 10px',
  width: 130,
}
