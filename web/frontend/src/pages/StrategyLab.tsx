import { useCallback, useEffect, useRef, useState } from 'react'
import { api, LabArenaResp, LabBoardResp, LabStatusResp } from '../api/client'

const fmt = (v: unknown, digits = 2, suffix = '') => {
  if (v === null || v === undefined || v === '') return '—'
  const n = Number(v)
  if (Number.isNaN(n)) return String(v)
  return n.toFixed(digits) + suffix
}
const pct = (v: unknown) => fmt(v, 1, '%')
const pfColor = (v: unknown) => {
  const n = Number(v)
  if (Number.isNaN(n)) return 'var(--text)'
  return n >= 1.2 ? 'var(--success)' : n >= 1 ? 'var(--text)' : 'var(--danger)'
}

export default function StrategyLab() {
  const [task, setTask] = useState<LabStatusResp | null>(null)
  const [isBoard, setIsBoard] = useState<LabBoardResp>({ rows: [], source: '' })
  const [oosBoard, setOosBoard] = useState<LabBoardResp>({ rows: [], source: '' })
  const [arena, setArena] = useState<LabArenaResp | null>(null)
  const [compare, setCompare] = useState<Record<string, Record<string, unknown> | null> | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const loadBoards = useCallback(() => {
    api.labLeaderboard('IS').then(setIsBoard).catch((e) => setErr(String(e)))
    api.labLeaderboard('OOS').then(setOosBoard).catch(() => undefined)
    api.labArena().then(setArena).catch(() => undefined)
    api.labCompare().then((r) => setCompare(r.best_by_strategy ?? null)).catch(() => undefined)
  }, [])

  useEffect(() => {
    loadBoards()
    return () => {
      if (pollRef.current) clearInterval(pollRef.current)
    }
  }, [loadBoards])

  const stopPoll = () => {
    if (pollRef.current) {
      clearInterval(pollRef.current)
      pollRef.current = null
    }
  }

  const startPoll = (tid: string) => {
    stopPoll()
    pollRef.current = setInterval(() => {
      api.labStatus(tid)
        .then((t) => {
          setTask(t)
          if (['done', 'error'].includes(t.status)) {
            stopPoll()
            setBusy(false)
            loadBoards()
          }
        })
        .catch(() => undefined)
    }, 4000)
  }

  const runOptimize = async (strategy: string) => {
    setErr('')
    setBusy(true)
    try {
      const r = await api.labOptimize({ strategy, max_codes: 4500, step: 5 })
      setTask({ task_id: r.task_id, status: 'pending', strategy })
      startPoll(r.task_id)
    } catch (e) {
      setErr(String(e))
      setBusy(false)
    }
  }

  const rows = (arr: Record<string, unknown>[] | undefined) =>
    (arr || []).map((r, i) => (
      <tr key={i}>
        <td>{i + 1}</td>
        <td>{String(r.strategy ?? '—')}</td>
        <td>{String(r.vol_ratio_min ?? '—')}</td>
        <td>{String(r.strong_reset ?? '—')}</td>
        <td>{String(r.exit_window ?? '—')}</td>
        <td>{pct(Number(r.stop_pct ?? 0) * 100)}</td>
        <td>{fmt(r.n_trades, 0)}</td>
        <td>{pct(Number(r.win_rate ?? 0) * 100)}</td>
        <td style={{ color: pfColor(r.profit_factor) }}>{fmt(r.profit_factor, 3)}</td>
        <td>{pct(Number(r.max_drawdown ?? 0) * 100)}</td>
      </tr>
    ))

  const activeCount = arena?.rows.filter((r) => r.status === 'active').length ?? 0
  const candCount = arena?.rows.filter((r) => r.status === 'candidate').length ?? 0

  return (
    <div>
      <div className="card section-gap" style={{ marginBottom: 16 }}>
        <div className="row" style={{ flexWrap: 'wrap', gap: 10, alignItems: 'center' }}>
          <span style={{ fontSize: 14, fontWeight: 500 }}>策略实验室 · 闭环优化</span>
          <span className="badge" style={{ marginLeft: 8 }}>IS 优化 → OOS 验证 → 擂台赛回灌</span>
          <div style={{ flex: 1 }} />
          <button className="btn" style={{ borderColor: 'var(--accent)', color: 'var(--accent)' }} disabled={busy} onClick={() => runOptimize('A')}>
            {busy ? '运行中…' : '▶ 优化方案 A（形态+标杆量）'}
          </button>
          <button className="btn" disabled={busy} onClick={() => runOptimize('B')}>
            {busy ? '运行中…' : '▶ 优化方案 B（五步抓主升）'}
          </button>
        </div>
        {task && (
          <div style={{ marginTop: 10, fontSize: 12, color: 'var(--text-secondary)' }}>
            任务 {task.task_id} [{task.strategy}]：{task.status === 'done' ? '✅ 完成' : task.status === 'error' ? `❌ ${task.error}` : `⏳ ${task.message ?? task.status} ${task.progress ?? 0}%`}
          </div>
        )}
        {err && <div style={{ marginTop: 8, color: 'var(--danger)', fontSize: 12 }}>{err}</div>}
      </div>

      <div className="row section-gap" style={{ gap: 16, alignItems: 'stretch' }}>
        <div className="card" style={{ flex: 1, minWidth: 300 }}>
          <div className="row" style={{ marginBottom: 10, alignItems: 'center' }}>
            <b>样本内 Top（2025 优化）</b>
            <span style={{ flex: 1 }} />
            <span className="badge badge-mute">{isBoard.source}</span>
          </div>
          <table style={{ width: '100%', fontSize: 12, borderCollapse: 'collapse' }}>
            <thead>
              <tr style={{ color: 'var(--text-secondary)' }}>
                <th>#</th><th>方案</th><th>量比</th><th>清零</th><th>窗口</th><th>止损</th><th>交易</th><th>胜率</th><th>PF</th><th>回撤</th>
              </tr>
            </thead>
            <tbody>{rows(isBoard.rows)}</tbody>
          </table>
        </div>
        <div className="card" style={{ flex: 1, minWidth: 300 }}>
          <div className="row" style={{ marginBottom: 10, alignItems: 'center' }}>
            <b>样本外验证（2026）</b>
            <span style={{ flex: 1 }} />
            <span className="badge badge-mute">{oosBoard.source}</span>
          </div>
          <table style={{ width: '100%', fontSize: 12, borderCollapse: 'collapse' }}>
            <thead>
              <tr style={{ color: 'var(--text-secondary)' }}>
                <th>#</th><th>方案</th><th>量比</th><th>清零</th><th>窗口</th><th>止损</th><th>交易</th><th>胜率</th><th>PF</th><th>回撤</th>
              </tr>
            </thead>
            <tbody>{rows(oosBoard.rows)}</tbody>
          </table>
        </div>
      </div>

      <div className="row section-gap" style={{ gap: 16, alignItems: 'stretch' }}>
        <div className="card" style={{ flex: 1, minWidth: 300 }}>
          <div className="row" style={{ marginBottom: 10, alignItems: 'center' }}>
            <b>A/B 方案最佳（样本内）</b>
          </div>
          {compare ? (
            <table style={{ width: '100%', fontSize: 12, borderCollapse: 'collapse' }}>
              <thead>
                <tr style={{ color: 'var(--text-secondary)' }}>
                  <th>方案</th><th>量比</th><th>清零</th><th>交易</th><th>胜率</th><th>PF</th>
                </tr>
              </thead>
              <tbody>
                {['A', 'B'].map((s) => {
                  const r = compare[s]
                  return (
                    <tr key={s}>
                      <td><b>{s}</b></td>
                      <td>{r ? String(r.vol_ratio_min ?? '—') : '—'}</td>
                      <td>{r ? String(r.strong_reset ?? '—') : '—'}</td>
                      <td>{r ? fmt(r.n_trades, 0) : '—'}</td>
                      <td>{r ? pct(Number(r.win_rate ?? 0) * 100) : '—'}</td>
                      <td style={{ color: r ? pfColor(r.profit_factor) : undefined }}>{r ? fmt(r.profit_factor, 3) : '—'}</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          ) : (
            <div style={{ fontSize: 12, color: 'var(--text-secondary)' }}>暂无优化结果，先点上方「优化」按钮</div>
          )}
        </div>
        <div className="card" style={{ flex: 1, minWidth: 300 }}>
          <div className="row" style={{ marginBottom: 10, alignItems: 'center' }}>
            <b>擂台赛注册表</b>
            <span style={{ flex: 1 }} />
            <span className="badge badge-ok">active {activeCount}</span>
            <span className="badge" style={{ marginLeft: 6 }}>candidate {candCount}</span>
          </div>
          {arena && arena.rows.length > 0 ? (
            <table style={{ width: '100%', fontSize: 12, borderCollapse: 'collapse' }}>
              <thead>
                <tr style={{ color: 'var(--text-secondary)' }}>
                  <th>状态</th><th>方案</th><th>IS PF</th><th>OOS PF</th><th>WF</th><th>退化连</th>
                </tr>
              </thead>
              <tbody>
                {arena.rows.slice(0, 10).map((r, i) => (
                  <tr key={i}>
                    <td>
                      <span className={r.status === 'active' ? 'badge badge-ok' : r.status === 'retired' ? 'badge badge-warn' : 'badge'}>
                        {String(r.status)}
                      </span>
                    </td>
                    <td>{String(r.strategy ?? '—')}</td>
                    <td>{fmt(r.is_profit_factor, 3)}</td>
                    <td style={{ color: pfColor(r.oos_profit_factor) }}>{fmt(r.oos_profit_factor, 3)}</td>
                    <td>{r.wf_pass === 1 ? '✅' : r.wf_pass === 0 ? '❌' : '—'}</td>
                    <td>{fmt(r.degrade_streak, 0)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <div style={{ fontSize: 12, color: 'var(--text-secondary)' }}>尚未播种参数，完成优化后自动注册</div>
          )}
          {arena && Object.keys(arena.weights).length > 0 && (
            <div style={{ marginTop: 10, fontSize: 12, color: 'var(--text-secondary)' }}>
              排序权重：{Object.entries(arena.weights).map(([k, v]) => `${k}=${v.toFixed(3)}`).join(' · ')}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
