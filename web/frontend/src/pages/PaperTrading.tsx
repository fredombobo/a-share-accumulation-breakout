import { useCallback, useEffect, useState } from 'react'
import { api, PaperDashboard, PaperOrder, PaperPosition, PaperFill, PaperImportPreview } from '../api/client'

const fen2yuan = (fen: number | null | undefined) => (fen ?? 0) / 100
const micro2yuan = (m: number | null | undefined) => (m ?? 0) / 1_000_000
const stateColor = (s: string) => {
  if (s === 'FILLED') return '#16a34a'
  if (s === 'REJECTED' || s === 'EXPIRED') return '#dc2626'
  if (s === 'CONFIRMED' || s === 'QUEUED') return '#2563eb'
  if (s === 'CANCELLED') return '#6b7280'
  return '#d97706'
}
const fmt = (n: number | null | undefined, d = 2) =>
  n == null ? '—' : n.toLocaleString('zh-CN', { minimumFractionDigits: d, maximumFractionDigits: d })

export default function PaperTrading() {
  const [tab, setTab] = useState<'dash' | 'orders' | 'fills' | 'import' | 'recon'>('dash')
  const [dash, setDash] = useState<PaperDashboard | null>(null)
  const [positions, setPositions] = useState<PaperPosition[]>([])
  const [orders, setOrders] = useState<PaperOrder[]>([])
  const [fills, setFills] = useState<PaperFill[]>([])
  const [recon, setRecon] = useState<Record<string, unknown>[]>([])
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')
  // 创建账户
  const [cashInput, setCashInput] = useState('100000')
  // 买入草稿
  const [buyCode, setBuyCode] = useState('')
  const [buyQty, setBuyQty] = useState('100')
  // 卖出草稿
  const [sellCode, setSellCode] = useState('')
  const [sellQty, setSellQty] = useState('100')
  // 导入
  const [importPath, setImportPath] = useState('/portfolio.json')
  const [preview, setPreview] = useState<PaperImportPreview | null>(null)

  const refresh = useCallback(async () => {
    try {
      const [d, p, o, f, r] = await Promise.all([
        api.paperDashboard(),
        api.paperPositions(),
        api.paperOrders(),
        api.paperFills(30),
        api.paperReconciliation(),
      ])
      setDash(d)
      setPositions(p.positions)
      setOrders(o.orders)
      setFills(f.fills)
      setRecon(r.items)
      setErr('')
    } catch (e) {
      setErr(String(e))
    }
  }, [])

  useEffect(() => { refresh() }, [refresh])

  const run = async (fn: () => Promise<unknown>, okMsg: string) => {
    setBusy(true); setErr(''); setMsg('')
    try {
      await fn()
      setMsg(okMsg)
      await refresh()
    } catch (e) {
      setErr(String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="paper">
      <div className="paper-banner" style={{ background: '#fef3c7', border: '1px solid #f59e0b', color: '#92400e', padding: '8px 12px', borderRadius: 6, marginBottom: 12, fontWeight: 600 }}>
        📋 纸面仿真交易 — 仅模拟，不会向券商下单 · LIVE_TRADING_DISABLED
      </div>

      {err && <div className="err" style={{ color: '#dc2626', marginBottom: 8 }}>⚠️ {err}</div>}
      {msg && <div style={{ color: '#16a34a', marginBottom: 8 }}>✅ {msg}</div>}

      <div className="tabs" style={{ display: 'flex', gap: 6, marginBottom: 12, flexWrap: 'wrap' }}>
        {([['dash', '📊 账户'], ['orders', '📝 订单'], ['fills', '💱 成交'], ['import', '📥 导入'], ['recon', '🔍 对账']] as const).map(([k, label]) => (
          <button key={k} onClick={() => setTab(k)}
            style={{ padding: '6px 14px', borderRadius: 6, border: tab === k ? '2px solid #2563eb' : '1px solid #ccc', background: tab === k ? '#eff6ff' : '#fff', cursor: 'pointer', fontWeight: 600 }}>
            {label}
          </button>
        ))}
      </div>

      {tab === 'dash' && (
        <div>
          {!dash?.account ? (
            <div style={{ background: '#f9fafb', padding: 20, borderRadius: 8, border: '1px solid #e5e7eb' }}>
              <h3>初始化纸面账户</h3>
              <p>输入「当前可用现金」（元）：</p>
              <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                <input value={cashInput} onChange={(e) => setCashInput(e.target.value)} inputMode="numeric"
                  style={{ padding: 8, width: 160, borderRadius: 6, border: '1px solid #ccc' }} />
                <button disabled={busy} onClick={() => run(() => api.paperCreateAccount(Math.round(parseFloat(cashInput || '0') * 100)), '账户已创建')}
                  style={{ padding: '8px 18px', borderRadius: 6, background: '#2563eb', color: '#fff', border: 'none', cursor: 'pointer' }}>
                  创建账户
                </button>
              </div>
            </div>
          ) : (
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))', gap: 10, marginBottom: 16 }}>
              {[
                ['现金', `¥${fmt(fen2yuan(dash.account.cash_fen))}`],
                ['持仓市值', `¥${fmt(fen2yuan(dash.equity?.market_value_fen))}`],
                ['总权益', `¥${fmt(fen2yuan(dash.equity?.total_equity_fen))}`],
                ['持仓数', String(dash.equity?.positions ?? 0)],
              ].map(([k, v]) => (
                <div key={k} style={{ background: '#f9fafb', padding: 12, borderRadius: 8, border: '1px solid #e5e7eb' }}>
                  <div style={{ color: '#6b7280', fontSize: 12 }}>{k}</div>
                  <div style={{ fontSize: 20, fontWeight: 700 }}>{v}</div>
                </div>
              ))}
            </div>
          )}

          <h3 style={{ marginBottom: 8 }}>持仓</h3>
          <table style={{ width: '100%', borderCollapse: 'collapse' }} className="paper-table">
            <thead><tr style={{ textAlign: 'left', color: '#6b7280' }}>
              <th>代码</th><th>总份额</th><th>可卖</th><th>平均成本(元)</th><th>操作</th>
            </tr></thead>
            <tbody>
              {positions.length === 0 && <tr><td colSpan={5} style={{ color: '#9ca3af', padding: 12 }}>暂无持仓</td></tr>}
              {positions.map((p) => (
                <tr key={p.ts_code}>
                  <td>{p.ts_code}</td>
                  <td>{p.total_qty}</td>
                  <td>{p.sellable_qty}</td>
                  <td>{fmt(micro2yuan(p.avg_cost_micro), 3)}</td>
                  <td>
                    <button onClick={() => { setSellCode(p.ts_code); setSellQty('100'); setTab('orders') }}
                      style={{ padding: '4px 10px', borderRadius: 4, border: '1px solid #ccc', cursor: 'pointer', background: '#fff' }}>
                      卖出
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {tab === 'orders' && (
        <div>
          <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 14, background: '#f9fafb', padding: 12, borderRadius: 8 }}>
            <div>
              <b>买入草稿</b>
              <div style={{ display: 'flex', gap: 6, marginTop: 4 }}>
                <input placeholder="代码如 000001.SZ" value={buyCode} onChange={(e) => setBuyCode(e.target.value)} style={{ padding: 6, width: 130, borderRadius: 4, border: '1px solid #ccc' }} />
                <input placeholder="数量" value={buyQty} onChange={(e) => setBuyQty(e.target.value)} inputMode="numeric" style={{ padding: 6, width: 80, borderRadius: 4, border: '1px solid #ccc' }} />
                <button disabled={busy} onClick={() => run(() => api.paperCreateDraft({ side: 'BUY', ts_code: buyCode.toUpperCase(), trade_date: new Date().toISOString().slice(0, 10).replace(/-/g, ''), qty: parseInt(buyQty) }), '买入草稿已创建')}
                  style={{ padding: '6px 12px', borderRadius: 4, background: '#dc2626', color: '#fff', border: 'none', cursor: 'pointer' }}>
                  创建买入
                </button>
              </div>
            </div>
            <div>
              <b>卖出草稿</b>
              <div style={{ display: 'flex', gap: 6, marginTop: 4 }}>
                <input placeholder="代码" value={sellCode} onChange={(e) => setSellCode(e.target.value)} style={{ padding: 6, width: 130, borderRadius: 4, border: '1px solid #ccc' }} />
                <input placeholder="数量" value={sellQty} onChange={(e) => setSellQty(e.target.value)} inputMode="numeric" style={{ padding: 6, width: 80, borderRadius: 4, border: '1px solid #ccc' }} />
                <button disabled={busy} onClick={() => run(() => api.paperCreateDraft({ side: 'SELL', ts_code: sellCode.toUpperCase(), qty: parseInt(sellQty) }), '卖出草稿已创建')}
                  style={{ padding: '6px 12px', borderRadius: 4, background: '#16a34a', color: '#fff', border: 'none', cursor: 'pointer' }}>
                  创建卖出
                </button>
              </div>
            </div>
          </div>

          <h3 style={{ marginBottom: 8 }}>订单</h3>
          <table style={{ width: '100%', borderCollapse: 'collapse' }} className="paper-table">
            <thead><tr style={{ textAlign: 'left', color: '#6b7280' }}>
              <th>ID</th><th>代码</th><th>方向</th><th>数量</th><th>状态</th><th>预留(元)</th><th>拒绝原因</th><th>操作</th>
            </tr></thead>
            <tbody>
              {orders.length === 0 && <tr><td colSpan={8} style={{ color: '#9ca3af', padding: 12 }}>暂无订单</td></tr>}
              {orders.map((o) => (
                <tr key={o.order_id}>
                  <td style={{ fontSize: 11 }}>{o.order_id.slice(0, 12)}</td>
                  <td>{o.ts_code}</td>
                  <td style={{ color: o.side === 'BUY' ? '#dc2626' : '#16a34a', fontWeight: 600 }}>{o.side}</td>
                  <td>{o.qty}</td>
                  <td style={{ color: stateColor(o.state), fontWeight: 600 }}>{o.state}</td>
                  <td>{fmt(fen2yuan(o.reserve_fen))}</td>
                  <td style={{ fontSize: 11, color: '#6b7280' }}>{o.reject_reason || ''}</td>
                  <td>
                    {o.state === 'DRAFT' && (
                      <button onClick={() => run(() => api.paperConfirmOrder(o.order_id), '已确认')}
                        style={{ padding: '4px 10px', borderRadius: 4, border: '1px solid #2563eb', color: '#2563eb', background: '#fff', cursor: 'pointer' }}>
                        确认
                      </button>
                    )}
                    {(o.state === 'DRAFT' || o.state === 'CONFIRMED' || o.state === 'QUEUED') && (
                      <button onClick={() => run(() => api.paperCancelOrder(o.order_id), '已取消')}
                        style={{ padding: '4px 10px', borderRadius: 4, border: '1px solid #ccc', color: '#6b7280', background: '#fff', cursor: 'pointer', marginLeft: 4 }}>
                        取消
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          <div style={{ marginTop: 12, display: 'flex', gap: 8 }}>
            <input id="cycleDate" defaultValue={new Date().toISOString().slice(0, 10).replace(/-/g, '')} style={{ padding: 6, width: 120, borderRadius: 4, border: '1px solid #ccc' }} aria-label="交易日" />
            <button disabled={busy} onClick={() => {
              const el = document.getElementById('cycleDate') as HTMLInputElement
              if (window.confirm(`确认对 ${el.value} 执行日结撮合？`)) {
                run(() => api.paperRunCycle(el.value), '日结完成')
              }
            }} style={{ padding: '6px 14px', borderRadius: 4, background: '#7c3aed', color: '#fff', border: 'none', cursor: 'pointer' }}>
              ▶ 手动补跑日结
            </button>
          </div>
        </div>
      )}

      {tab === 'fills' && (
        <div>
          <h3 style={{ marginBottom: 8 }}>成交记录</h3>
          <table style={{ width: '100%', borderCollapse: 'collapse' }} className="paper-table">
            <thead><tr style={{ textAlign: 'left', color: '#6b7280' }}>
              <th>成交ID</th><th>订单</th><th>成交价(元)</th><th>数量</th><th>佣金(元)</th><th>税(元)</th><th>模型</th><th>时间</th>
            </tr></thead>
            <tbody>
              {fills.length === 0 && <tr><td colSpan={8} style={{ color: '#9ca3af', padding: 12 }}>暂无成交</td></tr>}
              {fills.map((f) => (
                <tr key={f.fill_id}>
                  <td style={{ fontSize: 11 }}>{f.fill_id.slice(0, 12)}</td>
                  <td style={{ fontSize: 11 }}>{f.order_id.slice(0, 12)}</td>
                  <td>{fmt(micro2yuan(f.fill_price_micro), 4)}</td>
                  <td>{f.qty}</td>
                  <td>{fmt(fen2yuan(f.commission_fen))}</td>
                  <td>{fmt(fen2yuan(f.tax_fen))}</td>
                  <td>{f.fill_model_version}</td>
                  <td style={{ fontSize: 11 }}>{f.filled_at.slice(0, 16).replace('T', ' ')}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {tab === 'import' && (
        <div>
          <h3 style={{ marginBottom: 8 }}>旧持仓导入（portfolio.json 预览-确认）</h3>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', marginBottom: 10 }}>
            <input value={importPath} onChange={(e) => setImportPath(e.target.value)} style={{ padding: 6, width: 260, borderRadius: 4, border: '1px solid #ccc' }} aria-label="portfolio 路径" />
            <button disabled={busy} onClick={() => run(async () => {
              setPreview(await api.paperImportPreview(importPath))
            }, '预览完成')} style={{ padding: '6px 14px', borderRadius: 4, border: '1px solid #2563eb', color: '#2563eb', background: '#fff', cursor: 'pointer' }}>
              预览
            </button>
          </div>
          {preview && (
            <div>
              <p style={{ color: '#6b7280' }}>
                源文件哈希: <code>{preview.source_hash.slice(0, 16)}</code> · 有效 {preview.valid_count} / 无效 {preview.invalid_count}
              </p>
              <table style={{ width: '100%', borderCollapse: 'collapse' }} className="paper-table">
                <thead><tr style={{ textAlign: 'left', color: '#6b7280' }}>
                  <th>代码</th><th>数量</th><th>成本(元)</th><th>止损</th><th>建仓时间</th><th>现价</th><th>校验</th>
                </tr></thead>
                <tbody>
                  {preview.items.map((it, i) => (
                    <tr key={i} style={it.valid ? {} : { background: '#fef2f2' }}>
                      <td>{it.ts_code}</td>
                      <td>{it.shares}</td>
                      <td>{it.cost}</td>
                      <td>{it.stop_loss ?? '—'}</td>
                      <td style={{ fontSize: 11 }}>{it.opened_at.slice(0, 10)}</td>
                      <td>{it.last_close ?? '—'}</td>
                      <td style={{ color: it.valid ? '#16a34a' : '#dc2626', fontSize: 11 }}>
                        {it.valid ? 'OK' : it.errors.join('; ')}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {preview.invalid_count === 0 && (
                <button disabled={busy} onClick={() => {
                  if (window.confirm('确认导入所有有效持仓？此操作不可撤销（可重复导入同文件会被跳过）。')) {
                    run(() => api.paperImportCommit(importPath), '持仓已导入')
                  }
                }} style={{ marginTop: 10, padding: '8px 18px', borderRadius: 4, background: '#16a34a', color: '#fff', border: 'none', cursor: 'pointer' }}>
                  确认导入
                </button>
              )}
              {preview.invalid_count > 0 && (
                <p style={{ color: '#dc2626' }}>存在无效项，请修正 portfolio.json 后重新预览（不会导入任何持仓）。</p>
              )}
            </div>
          )}
        </div>
      )}

      {tab === 'recon' && (
        <div>
          <h3 style={{ marginBottom: 8 }}>对账记录</h3>
          <table style={{ width: '100%', borderCollapse: 'collapse' }} className="paper-table">
            <thead><tr style={{ textAlign: 'left', color: '#6b7280' }}>
              <th>日期</th><th>结果</th><th>严重级别</th><th>状态</th><th>检查时间</th><th>差异</th>
            </tr></thead>
            <tbody>
              {recon.length === 0 && <tr><td colSpan={6} style={{ color: '#9ca3af', padding: 12 }}>暂无对账记录</td></tr>}
              {recon.map((r, i) => (
                <tr key={i}>
                  <td>{String(r.run_date)}</td>
                  <td style={{ color: r.result === 'OK' ? '#16a34a' : '#dc2626', fontWeight: 600 }}>{String(r.result)}</td>
                  <td>{String(r.severity)}</td>
                  <td>{String(r.status)}</td>
                  <td style={{ fontSize: 11 }}>{String(r.checked_at).slice(0, 16).replace('T', ' ')}</td>
                  <td style={{ fontSize: 11, color: '#6b7280' }}>
                    {String(r.diff_json).slice(0, 80) === '[]' ? '无' : String(r.diff_json).slice(0, 80)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
