import { useEffect, useMemo, useState } from 'react'
import {
  api,
  PaperCycleResult,
  PaperDashboard,
  PaperOrder,
  PaperOrderReview,
  PaperPosition,
} from '../../api/client'
import { FriendlyError, GuideSteps, SuccessFeedback, ViewModeToggle } from '../guidance/BeginnerUi'

const toInputDate = (value?: string | null) => {
  const raw = String(value || '').replaceAll('-', '')
  return raw.length === 8 ? `${raw.slice(0, 4)}-${raw.slice(4, 6)}-${raw.slice(6, 8)}` : ''
}
const toTradeDate = (value: string) => value.replaceAll('-', '')

const actionCopy: Record<string, { title: string; message: string }> = {
  CREATE_ACCOUNT: { title: '先创建纸面账户', message: '输入当前可用现金后，才能进行模拟交易。' },
  REVIEW_DRAFT: { title: '有一笔订单等你确认', message: '先核对股票、数量、日期和费用。' },
  RUN_SETTLEMENT: { title: '订单已确认，等待开盘模拟', message: '点击下方按钮按指定日期的开盘行情撮合。' },
  RESOLVE_RECONCILIATION: { title: '先处理对账异常', message: '账本存在差异，暂停创建新的模拟买入。' },
  START_SIMULATION: { title: '开始一次历史开盘模拟', message: '选择过去的开市日、股票和数量。' },
  SYNC_DATA: { title: '先同步最新行情', message: '账本下一可用日期晚于本地行情，暂时不能创建新的历史模拟。' },
}

export default function PaperGuided({
  dashboard,
  positions,
  onChanged,
  onAdvanced,
}: {
  dashboard: PaperDashboard | null
  positions: PaperPosition[]
  onChanged: () => Promise<void>
  onAdvanced: () => void
}) {
  const guide = dashboard?.guide
  const [date, setDate] = useState('')
  const [code, setCode] = useState('')
  const [qty, setQty] = useState('100')
  const [cash, setCash] = useState('100000')
  const [openDates, setOpenDates] = useState<string[]>([])
  const [review, setReview] = useState<PaperOrderReview | null>(null)
  const [confirmed, setConfirmed] = useState<PaperOrder | null>(null)
  const [cycle, setCycle] = useState<PaperCycleResult | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [busy, setBusy] = useState(false)
  const [tutorialOpen, setTutorialOpen] = useState(false)
  const [tutorialResult, setTutorialResult] = useState<PaperOrderReview | null>(null)
  const [sellCode, setSellCode] = useState('')
  const [sellQty, setSellQty] = useState('')
  const [sellDraft, setSellDraft] = useState<PaperOrder | null>(null)

  useEffect(() => {
    if (!date && guide?.latest_market_date) setDate(toInputDate(guide.latest_market_date))
  }, [date, guide?.latest_market_date])

  useEffect(() => {
    const latest = guide?.latest_market_date
    if (!latest) return
    const end = latest
    const start = `${Math.max(2000, Number(latest.slice(0, 4)) - 4)}0101`
    api.paperTradingCalendar(start, end)
      .then((value) => setOpenDates(value.open_dates))
      .catch(() => setOpenDates([]))
  }, [guide?.latest_market_date])

  const selectedTradeDate = toTradeDate(date)
  const next = actionCopy[guide?.next_action || 'START_SIMULATION']
  const step = cycle ? 3 : confirmed ? 2 : review ? 1 : 0
  const formattedOpenDates = useMemo(() => new Set(openDates.map(toInputDate)), [openDates])
  const dateHint = date && openDates.length && !formattedOpenDates.has(date)
    ? '这个日期未出现在本地开市日历中，预览时会再次确认。'
    : '历史买入只用于练习，不代表当日属于 A 池。'

  const inspect = async (scope: 'ACCOUNT' | 'TUTORIAL') => {
    setBusy(true); setError(null)
    try {
      const result = await api.paperReviewOrder({
        scope,
        side: 'BUY',
        mode: 'MANUAL_HISTORY',
        ts_code: scope === 'TUTORIAL' ? (code || '000001') : code,
        execution_trade_date: selectedTradeDate || String(guide?.latest_market_date || ''),
        qty: Number.parseInt(qty || '100', 10),
      })
      if (scope === 'TUTORIAL') setTutorialResult(result)
      else { setReview(result); setConfirmed(null); setCycle(null) }
    } catch (caught) {
      setError(caught)
    } finally {
      setBusy(false)
    }
  }

  const confirmReview = async () => {
    if (!review) return
    setBusy(true); setError(null)
    try {
      const draft = await api.paperCreateDraft({
        side: 'BUY', mode: 'MANUAL_HISTORY', ts_code: review.instrument.ts_code,
        execution_trade_date: review.execution_trade_date,
        qty: review.estimate.requested_qty,
      })
      const order = await api.paperConfirmOrder(draft.order_id)
      setConfirmed(order)
      await onChanged()
    } catch (caught) {
      setError(caught)
      await onChanged()
    } finally {
      setBusy(false)
    }
  }

  const runCycle = async (tradeDate: string) => {
    setBusy(true); setError(null)
    try {
      setCycle(await api.paperRunCycle(tradeDate))
      await onChanged()
    } catch (caught) {
      setError(caught)
    } finally {
      setBusy(false)
    }
  }

  const createAccount = async () => {
    const match = cash.trim().match(/^(0|[1-9]\d*)(?:\.(\d{0,2}))?$/)
    if (!match) { setError(new Error('请输入最多两位小数的现金金额')); return }
    const fen = Number(match[1]) * 100 + Number((match[2] || '').padEnd(2, '0'))
    setBusy(true); setError(null)
    try { await api.paperCreateAccount(fen); await onChanged() }
    catch (caught) { setError(caught) }
    finally { setBusy(false) }
  }

  const createSellDraft = async () => {
    const position = positions.find((item) => item.ts_code === sellCode)
    const quantity = Number.parseInt(sellQty, 10)
    if (!position || !Number.isInteger(quantity) || quantity <= 0) {
      setError(new Error('请输入有效的卖出数量'))
      return
    }
    if (quantity > position.sellable_qty) {
      setError(new Error(`最多可卖 ${position.sellable_qty} 股`))
      return
    }
    setBusy(true); setError(null)
    try {
      const draft = await api.paperCreateDraft({ side: 'SELL', ts_code: position.ts_code, qty: quantity })
      setSellDraft(draft)
      await onChanged()
    } catch (caught) {
      setError(caught)
      await onChanged()
    } finally {
      setBusy(false)
    }
  }

  const pending = guide?.pending_order

  return (
    <div className="guided-page paper-guided">
      <div className="paper-banner">纸面仿真，不会向券商下单</div>
      <div className="guide-page-head">
        <div>
          <span className="guide-eyebrow">小白模式</span>
          <h1>纸面交易三步模拟</h1>
          <p>先看清费用和规则，再确认模拟订单，最后按历史开盘行情成交。</p>
        </div>
        <ViewModeToggle mode="guided" onChange={onAdvanced} />
      </div>

      <section className="guide-next-action">
        <span>今天要做什么</span>
        <h2>{next.title}</h2>
        <p>{next.message}</p>
      </section>

      {!dashboard?.account ? (
        <section className="guide-card">
          <h2>创建纸面账户</h2>
          <p>这里填写的是你现在可用于模拟的现金，不是包含持仓的总资产。</p>
          <label className="guide-field">当前可用现金（元）
            <input value={cash} inputMode="decimal" onChange={(event) => setCash(event.target.value)} />
          </label>
          <button type="button" className="btn primary" disabled={busy} onClick={() => { void createAccount() }}>创建纸面账户</button>
        </section>
      ) : (
        <>
          <div className="guide-account-strip">
            <div><span>可用现金</span><strong>¥{((dashboard.account.cash_fen || 0) / 100).toLocaleString('zh-CN')}</strong></div>
            <div><span>持仓市值</span><strong>¥{((dashboard.equity?.market_value_fen || 0) / 100).toLocaleString('zh-CN')}</strong></div>
            <div><span>总权益</span><strong>¥{((dashboard.equity?.total_equity_fen || 0) / 100).toLocaleString('zh-CN')}</strong></div>
          </div>

          {guide?.next_action === 'SYNC_DATA' && (
            <section className="guide-card">
              <h2>先补齐行情，再继续模拟</h2>
              <p>本地行情截至 {guide.latest_market_date || '未知'}，账本下一允许日期是 {guide.earliest_simulation_date || '未知'}。</p>
              <p className="guide-help">双击项目目录里的“一键启动.bat”；同步完成后重新打开本页即可。</p>
            </section>
          )}

          {guide?.next_action !== 'SYNC_DATA' && (
            <GuideSteps labels={['填写模拟计划', '核对费用与规则', '确认并等待开盘', '查看成交与持仓']} current={step} />
          )}

          {pending && !review && (
            <section className="guide-card">
              <h2>{pending.state === 'DRAFT' ? '待确认的模拟订单' : '等待开盘模拟的订单'}</h2>
              <p>{pending.ts_code} · {pending.side === 'BUY' ? '买入' : '卖出'} {pending.qty} 股 · {pending.eligible_trade_date || '下一交易日'}</p>
              {pending.state === 'DRAFT' ? (
                <button type="button" className="btn primary" disabled={busy} onClick={async () => {
                  setBusy(true); setError(null)
                  try { setConfirmed(await api.paperConfirmOrder(pending.order_id)); await onChanged() }
                  catch (caught) { setError(caught); await onChanged() }
                  finally { setBusy(false) }
                }}>确认这笔模拟订单</button>
              ) : pending.eligible_trade_date && (
                <button type="button" className="btn primary" disabled={busy}
                  onClick={() => { void runCycle(pending.eligible_trade_date || '') }}>
                  按 {pending.eligible_trade_date} 开盘模拟成交
                </button>
              )}
            </section>
          )}

          {guide?.next_action !== 'SYNC_DATA' && !pending && !review && !confirmed && !cycle && (
            <section className="guide-card">
              <h2>第一步：填写模拟计划</h2>
              <div className="guide-form-grid">
                <label className="guide-field">模拟成交日期
                  <input type="date" aria-label="模拟成交日期" value={date}
                    min={toInputDate(guide?.earliest_simulation_date)}
                    max={toInputDate(guide?.latest_market_date)}
                    onChange={(event) => setDate(event.target.value)} />
                </label>
                <label className="guide-field">股票代码
                  <input aria-label="股票代码" placeholder="例如 000001" value={code}
                    onChange={(event) => setCode(event.target.value)} />
                </label>
                <label className="guide-field">买入数量
                  <input aria-label="买入数量" inputMode="numeric" value={qty}
                    onChange={(event) => setQty(event.target.value.replace(/\D/g, ''))} />
                </label>
              </div>
              <p className="guide-help">{dateHint}</p>
              <button type="button" className="btn primary" disabled={busy || !date || !code || !qty}
                onClick={() => { void inspect('ACCOUNT') }}>检查并预览</button>
            </section>
          )}

          {review && !confirmed && (
            <section className="guide-card">
              <h2>第二步：核对费用和规则</h2>
              <div className="guide-review-grid">
                <div><span>标准代码</span><strong>{review.instrument.ts_code}</strong></div>
                <div><span>决策日</span><strong>{review.decision_date}</strong></div>
                <div><span>模拟成交日</span><strong>{review.execution_trade_date}</strong></div>
                <div><span>当日开盘价</span><strong>¥{review.quote.open}</strong></div>
                <div><span>预计成交价</span><strong>¥{review.estimate.fill_price}</strong></div>
                <div><span>预计成交数量</span><strong>{review.estimate.estimated_fill_qty} 股</strong></div>
                <div><span>预计佣金</span><strong>¥{review.estimate.commission_yuan}</strong></div>
                <div><span>其他费用</span><strong>¥{review.estimate.other_fee_yuan}</strong></div>
                <div><span>预计预留现金</span><strong>¥{review.estimate.reserve_yuan}</strong></div>
              </div>
              <ul className="guide-checks">
                {review.checks.map((check) => <li key={check.code} className={check.passed ? 'pass' : 'fail'}>
                  {check.passed ? '✓' : '×'} <strong>{check.label}</strong><span>{check.message}</span>
                </li>)}
              </ul>
              <div className="guide-primary-actions">
                <button type="button" className="btn" onClick={() => setReview(null)}>返回修改</button>
                <button type="button" className="btn primary" disabled={busy || !review.can_confirm}
                  onClick={() => { void confirmReview() }}>确认模拟订单</button>
              </div>
            </section>
          )}

          {confirmed && !cycle && (
            <section className="guide-card">
              <SuccessFeedback>订单已确认，资金已按规则预留。</SuccessFeedback>
              <h2>第三步：按历史开盘行情模拟成交</h2>
              <p>{confirmed.ts_code} · {confirmed.qty} 股 · 交易日 {confirmed.eligible_trade_date}</p>
              <button type="button" className="btn primary" disabled={busy}
                onClick={() => { void runCycle(confirmed.eligible_trade_date || selectedTradeDate) }}>
                按 {confirmed.eligible_trade_date || selectedTradeDate} 开盘模拟成交
              </button>
            </section>
          )}

          {cycle && (
            <section className="guide-verdict pass">
              <span>第四步</span><h2>模拟成交完成</h2>
              <p>成交 {cycle.filled_count} 笔，零成交 {cycle.zero_fill_count} 笔；对账 {cycle.reconciliation.result === 'OK' ? '通过' : '需要处理'}。</p>
              <div className="guide-review-grid">
                <div><span>剩余现金</span><strong>¥{(cycle.mark.cash_fen / 100).toLocaleString('zh-CN')}</strong></div>
                <div><span>持仓市值</span><strong>¥{(cycle.mark.market_value_fen / 100).toLocaleString('zh-CN')}</strong></div>
                <div><span>总资产</span><strong>¥{(cycle.mark.total_asset_fen / 100).toLocaleString('zh-CN')}</strong></div>
              </div>
              <button type="button" className="btn" onClick={() => { setReview(null); setConfirmed(null); setCycle(null) }}>再做一次模拟</button>
            </section>
          )}

          {positions.length > 0 && (
            <section className="guide-card">
              <h2>当前持仓</h2>
              {positions.slice(0, 5).map((position) => (
                <div key={position.ts_code}>
                  <div className="guide-position">
                    <strong>{position.ts_code}</strong>
                    <span>持有 {position.total_qty} 股 · 可卖 {position.sellable_qty} 股</span>
                    <button type="button" className="btn"
                      aria-label={`模拟卖出 ${position.ts_code}`}
                      disabled={busy || position.sellable_qty <= 0}
                      title={position.sellable_qty <= 0 ? 'T+1：当前没有可卖份额' : '从这笔持仓发起卖出'}
                      onClick={() => {
                        setSellCode(position.ts_code)
                        setSellQty(String(position.sellable_qty))
                        setSellDraft(null)
                      }}>
                      {position.sellable_qty > 0 ? '模拟卖出' : 'T+1 暂不可卖'}
                    </button>
                  </div>
                  {sellCode === position.ts_code && !sellDraft && (
                    <div className="guide-sell-panel">
                      <strong>卖出 {position.ts_code}</strong>
                      <span>系统已带入可卖上限；不能超过 {position.sellable_qty} 股。</span>
                      <label className="guide-field">卖出数量
                        <input aria-label="卖出数量" inputMode="numeric" value={sellQty}
                          onChange={(event) => setSellQty(event.target.value.replace(/\D/g, ''))} />
                      </label>
                      <div className="guide-primary-actions">
                        <button type="button" className="btn" onClick={() => setSellCode('')}>取消</button>
                        <button type="button" className="btn primary" disabled={busy || !sellQty}
                          onClick={() => { void createSellDraft() }}>创建待确认卖出</button>
                      </div>
                    </div>
                  )}
                  {sellDraft?.ts_code === position.ts_code && (
                    <div className="guide-sell-panel">
                      <SuccessFeedback>卖出草稿已创建，尚未成交。</SuccessFeedback>
                      <button type="button" className="btn primary" disabled={busy}
                        onClick={async () => {
                          setBusy(true); setError(null)
                          try {
                            setConfirmed(await api.paperConfirmOrder(sellDraft.order_id))
                            setSellDraft(null)
                            await onChanged()
                          } catch (caught) { setError(caught); await onChanged() }
                          finally { setBusy(false) }
                        }}>确认这笔卖出</button>
                    </div>
                  )}
                </div>
              ))}
              <button type="button" className="btn" onClick={onAdvanced}>查看全部持仓明细</button>
            </section>
          )}
        </>
      )}

      <FriendlyError error={error} />

      <section className="guide-tutorial-card">
        <button type="button" className="btn" onClick={() => { setTutorialOpen((value) => !value); setTutorialResult(null) }}>
          第一次使用演练
        </button>
        {tutorialOpen && (
          <div>
            <strong>演练数据，不影响你的纸面账户</strong>
            <p>使用本地历史行情和固定 10 万元演示资金，只计算结果，不创建订单或持仓。</p>
            <button type="button" className="btn primary" disabled={busy}
              onClick={() => { void inspect('TUTORIAL') }}>运行隔离演练</button>
          </div>
        )}
        {tutorialResult && (
          <div className="guide-feedback success">
            <strong>隔离演练结果</strong>
            <span>{tutorialResult.instrument.ts_code} 预计以 ¥{tutorialResult.estimate.fill_price} 成交 {tutorialResult.estimate.estimated_fill_qty} 股。</span>
          </div>
        )}
      </section>
    </div>
  )
}
