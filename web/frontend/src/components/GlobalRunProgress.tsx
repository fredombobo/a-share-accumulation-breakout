import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router'

import {
  api,
  type BacktestTask,
  type ScanStatus,
  type SyncStatus,
} from '../api/client'
import { IcoLayers, IcoRefresh, IcoScan } from './Icons'

export const RUN_TASK_EVENT = 'ab-run-task-started'

const ACTIVE_SCAN = new Set<ScanStatus['status']>(['pending', 'running', 'cancelling'])
const ACTIVE_BACKTEST = new Set<BacktestTask['status']>(['pending', 'running', 'cancelling'])
const STALL_AFTER_MS = 3 * 60 * 1000

type RunKind = 'scan' | 'backtest' | 'sync'

interface RunItem {
  key: string
  kind: RunKind
  title: string
  statusLabel: string
  stage: string
  message: string
  progress: number | null
  startedAt: string | null
  updatedAt: string | null
  path: '/' | '/backtest'
}

interface Observation {
  signature: string
  changedAt: number
}

function timestamp(value: string | null | undefined): number | null {
  if (!value) return null
  const parsed = Date.parse(value)
  return Number.isFinite(parsed) ? parsed : null
}

export function formatRunDuration(milliseconds: number): string {
  const seconds = Math.max(0, Math.floor(milliseconds / 1000))
  if (seconds < 60) return `${seconds}秒`
  const minutes = Math.floor(seconds / 60)
  const remainingSeconds = seconds % 60
  if (minutes < 60) return `${minutes}分${String(remainingSeconds).padStart(2, '0')}秒`
  const hours = Math.floor(minutes / 60)
  const remainingMinutes = minutes % 60
  return `${hours}小时${String(remainingMinutes).padStart(2, '0')}分`
}

function formatAgo(milliseconds: number): string {
  const seconds = Math.max(0, Math.floor(milliseconds / 1000))
  if (seconds < 5) return '刚刚'
  if (seconds < 60) return `${seconds}秒前`
  const minutes = Math.floor(seconds / 60)
  return minutes < 60 ? `${minutes}分钟前` : `${Math.floor(minutes / 60)}小时前`
}

function clampProgress(value: number): number {
  return Math.max(0, Math.min(100, Math.round(value)))
}

function statusLabel(status: string): string {
  if (status === 'pending') return '等待开始'
  if (status === 'cancelling') return '正在取消'
  return '运行中'
}

export default function GlobalRunProgress() {
  const navigate = useNavigate()
  const [scan, setScan] = useState<ScanStatus | null>(null)
  const [backtest, setBacktest] = useState<BacktestTask | null>(null)
  const [sync, setSync] = useState<SyncStatus | null>(null)
  const [now, setNow] = useState(() => Date.now())
  const [lastResponseAt, setLastResponseAt] = useState(() => Date.now())
  const mountedRef = useRef(true)
  const activeRef = useRef(false)
  const observationsRef = useRef(new Map<string, Observation>())

  const refresh = useCallback(async () => {
    const results = await Promise.allSettled([
      api.scanStatus(),
      api.backtestLatest(),
      api.syncStatus(),
    ])
    if (!mountedRef.current) return
    let responded = false
    if (results[0].status === 'fulfilled') {
      setScan(results[0].value)
      responded = true
    }
    if (results[1].status === 'fulfilled') {
      setBacktest(results[1].value.task)
      responded = true
    }
    if (results[2].status === 'fulfilled') {
      setSync(results[2].value)
      responded = true
    }
    if (responded) setLastResponseAt(Date.now())
  }, [])

  useEffect(() => {
    mountedRef.current = true
    let stopped = false
    let timer: ReturnType<typeof setTimeout> | null = null
    const tick = async () => {
      await refresh()
      if (!stopped) timer = setTimeout(tick, activeRef.current ? 1500 : 5000)
    }
    const wake = () => void refresh()
    const onVisibility = () => { if (document.visibilityState === 'visible') void refresh() }
    void tick()
    window.addEventListener(RUN_TASK_EVENT, wake)
    window.addEventListener('focus', wake)
    document.addEventListener('visibilitychange', onVisibility)
    return () => {
      stopped = true
      mountedRef.current = false
      if (timer) clearTimeout(timer)
      window.removeEventListener(RUN_TASK_EVENT, wake)
      window.removeEventListener('focus', wake)
      document.removeEventListener('visibilitychange', onVisibility)
    }
  }, [refresh])

  const runs = useMemo<RunItem[]>(() => {
    const items: RunItem[] = []
    if (scan && ACTIVE_SCAN.has(scan.status)) {
      items.push({
        key: `scan:${scan.id || 'pending'}`,
        kind: 'scan',
        title: '全市场扫描',
        statusLabel: statusLabel(scan.status),
        stage: scan.stage || '准备扫描',
        message: scan.status === 'cancelling' ? '取消请求已发送' : '正在筛选 A 池与 B 池候选',
        progress: clampProgress(scan.progress || 0),
        startedAt: scan.started_at || scan.created_at || null,
        updatedAt: scan.updated_at || scan.heartbeat_at || scan.started_at || null,
        path: '/',
      })
    }
    if (backtest && ACTIVE_BACKTEST.has(backtest.status)) {
      items.push({
        key: `backtest:${backtest.task_id}`,
        kind: 'backtest',
        title: '专业回测',
        statusLabel: statusLabel(backtest.status),
        stage: backtest.phase || '准备研究输入',
        message: backtest.message || '正在计算样本内与样本外证据',
        progress: clampProgress(backtest.progress || 0),
        startedAt: backtest.started_at || backtest.created_at || null,
        updatedAt: backtest.updated_at || backtest.heartbeat_at || backtest.started_at || null,
        path: '/backtest',
      })
    }
    if (sync?.status === 'running') {
      items.push({
        key: `sync:${sync.started_at || 'running'}`,
        kind: 'sync',
        title: '行情同步',
        statusLabel: '同步中',
        stage: '供应商数据同步',
        message: sync.message || '正在更新交易日历、日线与资金流',
        progress: null,
        startedAt: sync.started_at,
        updatedAt: null,
        path: '/',
      })
    }
    return items
  }, [backtest, scan, sync])

  useEffect(() => {
    activeRef.current = runs.length > 0
    if (!runs.length) return
    const timer = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(timer)
  }, [runs.length])

  if (!runs.length) return null

  const responseAge = now - lastResponseAt

  return (
    <section className={`global-run-progress ${responseAge > 15_000 ? 'connection-late' : ''}`} aria-label="全局运行进度">
      <div className="global-run-summary" aria-live="polite">
        <strong>系统正在运行</strong>
        <span>{runs.length} 项后台任务。切换页面或窗口不会中断。</span>
        <small>状态接口 {formatAgo(responseAge)}回应</small>
      </div>
      <div className="global-run-list">
        {runs.map((run) => {
          const signature = `${run.statusLabel}|${run.stage}|${run.progress ?? 'indeterminate'}|${run.message}`
          const previous = observationsRef.current.get(run.key)
          if (!previous || previous.signature !== signature) {
            const serverUpdatedAt = timestamp(run.updatedAt)
            observationsRef.current.set(run.key, {
              signature,
              changedAt: serverUpdatedAt && serverUpdatedAt <= now + 60_000 ? serverUpdatedAt : now,
            })
          }
          const changedAt = observationsRef.current.get(run.key)?.changedAt ?? now
          const noChangeFor = now - changedAt
          const stalled = run.progress !== null && run.statusLabel === '运行中' && noChangeFor >= STALL_AFTER_MS
          const startedAt = timestamp(run.startedAt) ?? changedAt
          const Icon = run.kind === 'scan' ? IcoScan : run.kind === 'backtest' ? IcoLayers : IcoRefresh
          const progressProps = run.progress === null
            ? { 'aria-valuetext': '进行中，数据源未提供百分比' }
            : { 'aria-valuemin': 0, 'aria-valuemax': 100, 'aria-valuenow': run.progress }

          return (
            <article key={run.key} className={`global-run-item ${stalled ? 'stalled' : ''} ${run.progress === null ? 'indeterminate' : ''}`}>
              <div className="global-run-identity">
                <span className="global-run-icon"><Icon size={17} /></span>
                <div>
                  <strong>{run.title}</strong>
                  <span>{run.statusLabel}</span>
                </div>
              </div>
              <div className="global-run-stage">
                <b>{run.stage}</b>
                <span>{run.message}</span>
              </div>
              <div className="global-run-clock">
                <b className="num">{run.progress === null ? '处理中' : `${run.progress}%`}</b>
                <span>已运行 {formatRunDuration(now - startedAt)}</span>
              </div>
              <button type="button" className="btn btn-sm global-run-open" onClick={() => navigate(run.path)}>
                查看任务
              </button>
              <div
                className="global-run-track"
                role="progressbar"
                aria-label={`${run.title}进度`}
                {...progressProps}
              >
                <i style={run.progress === null ? undefined : { width: `${Math.max(run.progress, 2)}%` }} />
              </div>
              <div className={`global-run-freshness ${stalled ? 'warn' : ''}`}>
                {run.progress === null
                  ? '供应商未提供分项百分比，使用活动状态显示'
                  : stalled
                    ? `${formatRunDuration(noChangeFor)}无进度变化，可能仍在重计算，可进入任务页检查或取消`
                    : `最近推进 ${formatAgo(noChangeFor)}`}
              </div>
            </article>
          )
        })}
      </div>
    </section>
  )
}
