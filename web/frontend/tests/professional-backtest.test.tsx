import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  ApiError,
  api,
  type BacktestCatalog,
  type BacktestPreview,
  type BacktestTask,
  type ProfileActivation,
  type StrategyProfileState,
} from '../src/api/client'
import ProfessionalBacktest from '../src/pages/ProfessionalBacktest'
import BacktestResultCharts from '../src/components/BacktestResultCharts'

vi.mock('../src/components/EChart', () => ({
  default: ({ height }: { height?: number }) => <div data-testid="result-echart" data-height={height} />,
}))

const baseEntryMechanism = {
  id: 'BASE_STRICT_BREAKOUT_V1',
  version: 'base-strict-breakout-v1.0.0',
  semantic_hash: 'base-semantic-hash',
  research_only: false,
  benchmark_code: null,
  parameter_search: 'none',
}

const catalog: BacktestCatalog = {
  version: 'test-v1',
  max_combinations: 5120,
  long_running_warning_combinations: 512,
  parameters: [
    {
      key: 'box_max_days', title: '横盘最长天数', group: 'signal', value_type: 'integer',
      minimum: 40, maximum: 240, default: { mode: 'range', start: 60, stop: 200, step: 20 },
      description: '步进搜索横盘长度',
    },
    {
      key: 'breakout_vol_ratio', title: '突破量比', group: 'signal', value_type: 'number',
      minimum: 1, maximum: 5, default: { mode: 'values', values: [1.4, 1.6, 1.8] },
      description: '突破量相对箱体均量',
    },
    {
      key: 'stop_pct', title: '止损比例', group: 'exit', value_type: 'number',
      minimum: 0.01, maximum: 0.25, default: { mode: 'values', values: [0.05, 0.07] },
      description: '保守止损',
    },
    {
      key: 'target_pct', title: '止盈比例', group: 'exit', value_type: 'number',
      minimum: 0.02, maximum: 1, default: { mode: 'values', values: [0.1, 0.12] },
      description: 'T+1 止盈',
    },
    {
      key: 'max_hold_days', title: '最长持有天数', group: 'exit', value_type: 'integer',
      minimum: 2, maximum: 120, default: { mode: 'fixed', value: 30 },
      description: '到期退出',
    },
  ],
  conditions: [],
  research_boundary: 'EXPLORATORY_ONLY',
  paper_trading_enabled: false,
  live_trading_enabled: false,
}

const preview: BacktestPreview = {
  can_run: true,
  estimated_work: { combinations: 24, stocks: 30, sample_step: 10, long_running: false, warning_threshold: 512, note: '后台运行' },
  prepared: {
    contract_version: 'test-v1', strategy: 'A', sample_step: 10, max_codes: 600,
    entry_mechanism: baseEntryMechanism,
    parameters: Object.fromEntries(catalog.parameters.map((item) => [item.key, item.default])),
    universe: {
      classification: 'industry', classification_title: '细分行业', group_label: '行业', groups: [],
      industries: [], codes: Array.from({ length: 30 }, (_, index) => `${String(index).padStart(6, '0')}.SZ`),
      source: 'CURRENT_ALL', count: 30, sha256: 'universe',
      classification_mode: 'CURRENT_CLASSIFICATION_FROZEN_UNIVERSE', classification_note: '当前分类只用于选择',
    },
    conditions: [],
    windows: { mode: 'auto', is: ['20230101', '20241231'], oos: ['20250101', '20260828'], wf: [], n_dates: 800 },
    parameter_space: { count: 24, sha256: 'grid', horizon: 280, signal_group_count: 24, exit_group_count: 1, invalid_signal_combinations: 0, long_running: false, long_running_warning_combinations: 512 },
    input_hash: 'input-hash',
  },
}

const boundary = {
  scope: 'DAILY_A_POOL_TECHNICAL_ENTRY', manual_activation_required: true,
  automatic_promotion: false, b_pool_uses_profile: false,
  daily_extra_gates: ['资金流', '基本面'], notice: '只统一 A 池技术入场参数；每日额外门禁继续执行。',
}

const eligible: ProfileActivation = {
  task_id: 'probt-good', can_activate: true, already_active: false,
  checks: [], reasons: [], boundary,
}

function profileState(isDefault = true): StrategyProfileState {
  return {
    active: {
      profile_id: isDefault ? 'default' : 'professional-backtest-daily-scan',
      name: isDefault ? 'default' : '回测档案', version: isDefault ? '1.0.0' : 'probt-good',
      schema_version: 2, is_default: isDefault, status: 'active', storage_status: isDefault ? 'built_in' : 'active',
      config_hash: isDefault ? 'default-hash' : 'custom-hash', activated_at: null,
      entry: { box_min_days: 60, box_max_days: 200, breakout_vol_ratio: 1.8 },
      exit_reference: { stop_pct: 0.05, target_pct: 0.15 }, required_scan_days: 210,
      source: { kind: isDefault ? 'BUILT_IN' : 'PROFESSIONAL_BACKTEST', task_id: isDefault ? null : 'probt-good' },
      notes: [],
    },
    history: [], boundary, live_trading_enabled: false,
  }
}

const completedTask: BacktestTask = {
  task_id: 'probt-good', research_run_id: 'probt-good', research_mode: 'professional_grid',
  status: 'done', phase: 'DONE', progress: 100, message: '完成', request: preview.prepared,
  result: {
    verdict: 'EXPLORATORY_PROMISING', verdict_label: '探索结果值得另行预登记复验', verdict_reasons: [],
    candidate_eligible: false, can_claim_edge: false, request: preview.prepared,
    leaderboard: [{
      param_id: 'p1', signal: { box_max_days: 200, breakout_vol_ratio: 1.8 },
      exit: { stop_pct: 0.05, target_pct: 0.15, max_hold_days: 30, exit_window: 10 },
      is: { net_n_trades: 50, net_profit_factor: 1.42, portfolio_total_return: 0.1, portfolio_max_drawdown: 0.06 },
      oos: { net_n_trades: 40, net_profit_factor: 1.22, net_avg_return: 0.08, portfolio_max_drawdown: 0.09 },
    }],
    selected: {
      param_id: 'p1', signal: { box_max_days: 200, breakout_vol_ratio: 1.8 },
      exit: { stop_pct: 0.05, target_pct: 0.15, max_hold_days: 30, exit_window: 10 },
      is: { net_n_trades: 50, net_profit_factor: 1.42, portfolio_total_return: 0.1, portfolio_max_drawdown: 0.06 },
      oos: { net_n_trades: 40, net_profit_factor: 1.22, net_avg_return: 0.08, portfolio_max_drawdown: 0.09 },
    },
    wf: {
      evidence_complete: true, wf_pass: true, train_mean_pf: 1.35, oos_mean_pf: 1.18,
      wf_detail: [
        { window: 'WF1', train_pf: 1.4, test_pf: 1.2, test_dd: 0.08, test_n: 35 },
        { window: 'WF2', train_pf: 1.3, test_pf: 1.16, test_dd: 0.1, test_n: 32 },
        { window: 'WF3', train_pf: 1.1, test_pf: null, test_dd: null, test_n: 0 },
      ],
    },
    baselines: {
      random: { net_n_trades: 40, portfolio_total_return: 0.01, portfolio_max_drawdown: 0.12 },
      ma20_60: { net_n_trades: 36, portfolio_total_return: -0.02, portfolio_max_drawdown: 0.16 },
    },
    cost_stress: { multiplier: '2x', metrics: { portfolio_total_return: 0.04 } }, warnings: [],
  },
  created_at: '2026-08-30T10:00:00+08:00', updated_at: '2026-08-30T10:01:00+08:00',
  code_version: 'code', dataset_version: 'dataset', input_hash: 'input', profile_activation: eligible,
}

completedTask.result!.path_analysis = {
  method: 'combined_portfolio_equity_sha256', evidence_complete: true,
  coverage_complete: false,
  nominal_combinations: 2, independent_is_paths: 1, independent_oos_paths: 1,
  path_eligible_combinations: 1, excluded_without_complete_path: 1,
  independent_joint_paths: 1, duplicate_group_count: 1,
}
completedTask.result!.independent_leaderboard = [{
  ...completedTask.result!.leaderboard[0], equivalent_parameter_count: 2,
}]

afterEach(() => vi.restoreAllMocks())

describe('专业回测工作台', () => {
  it('使用版本化默认参数先预览，不静默启动任务', async () => {
    vi.spyOn(api, 'backtestCatalog').mockResolvedValue(catalog)
    vi.spyOn(api, 'backtestUniverse').mockResolvedValue({
      classification: 'industry', classification_title: '细分行业', group_label: '行业',
      classification_mode: 'CURRENT_CLASSIFICATION_FROZEN_UNIVERSE', classification_note: '当前分类只用于选择',
      classifications: [
        { key: 'industry', title: '细分行业', group_label: '行业', description: '产业细分', pit_status: 'CURRENT_SNAPSHOT_ONLY', group_count: 1 },
        { key: 'market', title: '上市板块', group_label: '板块', description: '上市制度分组', pit_status: 'CURRENT_SNAPSHOT_ONLY', group_count: 2 },
        { key: 'area', title: '地域', group_label: '地区', description: '公司注册地', pit_status: 'CURRENT_SNAPSHOT_ONLY', group_count: 2 },
      ],
      groups: [{ name: '半导体', count: 30 }],
      industries: [{ name: '半导体', count: 30 }], stocks: [], stock_count: 30,
    })
    vi.spyOn(api, 'backtestLatest').mockResolvedValue({ task: null })
    const previewSpy = vi.spyOn(api, 'backtestPreview').mockResolvedValue(preview)
    const runSpy = vi.spyOn(api, 'backtestRun')

    render(<ProfessionalBacktest />)
    await screen.findByRole('heading', { name: '多参数研究回测' })
    expect(screen.getByRole('heading', { name: '止损与止盈（百分比）' })).toBeVisible()
    expect(screen.getByLabelText('止盈比例离散值')).toHaveValue('10, 12')
    expect(screen.getByLabelText('交易日采样间隔')).toHaveValue(10)
    expect(screen.getByText(/每隔 N 个交易日生成一个研究决策截面/)).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: '检查参数空间' }))

    await waitFor(() => expect(previewSpy).toHaveBeenCalledOnce())
    const dialog = await screen.findByRole('dialog', { name: '参数检查通过' })
    expect(dialog).toHaveTextContent('检查通过不代表策略有效，也没有启动回测')
    fireEvent.click(screen.getByRole('button', { name: '查看冻结预览' }))
    expect(previewSpy.mock.calls[0][0].parameters.box_max_days).toEqual({ mode: 'range', start: 60, stop: 200, step: 20 })
    expect(await screen.findByText('24')).toBeVisible()
    expect(runSpy).not.toHaveBeenCalled()
  })

  it('参数检查失败时弹出人话结果并允许返回修改', async () => {
    vi.spyOn(api, 'backtestCatalog').mockResolvedValue(catalog)
    vi.spyOn(api, 'backtestUniverse').mockResolvedValue({
      classification: 'industry', classification_title: '细分行业', group_label: '行业',
      classification_mode: 'CURRENT_CLASSIFICATION_FROZEN_UNIVERSE', classification_note: '当前分类只用于选择',
      classifications: [], groups: [], industries: [], stocks: [], stock_count: 30,
    })
    vi.spyOn(api, 'backtestLatest').mockResolvedValue({ task: null, profile_activation: eligible })
    vi.spyOn(api, 'backtestProfile').mockResolvedValue(profileState(true))
    vi.spyOn(api, 'backtestPreview').mockRejectedValue(new ApiError({
      code: 'TOO_MANY_COMBINATIONS', message: '参数组合 5184 组，超过硬上限 5120 组', status: 422,
    }))

    render(<ProfessionalBacktest />)
    await screen.findByRole('heading', { name: '多参数研究回测' })
    fireEvent.click(screen.getByRole('button', { name: '检查参数空间' }))

    const dialog = await screen.findByRole('dialog', { name: '参数检查未通过' })
    expect(dialog).toHaveTextContent('超过硬上限 5120 组')
    fireEvent.click(screen.getByRole('button', { name: '返回修改参数' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  })

  it('已完成结果用真实字段生成五类图谱和可读参数摘要', async () => {
    vi.spyOn(api, 'backtestCatalog').mockResolvedValue(catalog)
    vi.spyOn(api, 'backtestUniverse').mockResolvedValue({
      classification: 'industry', classification_title: '细分行业', group_label: '行业',
      classification_mode: 'CURRENT_CLASSIFICATION_FROZEN_UNIVERSE', classification_note: '当前分类只用于选择',
      classifications: [], groups: [], industries: [], stocks: [], stock_count: 30,
    })
    vi.spyOn(api, 'backtestLatest').mockResolvedValue({ task: completedTask, profile_activation: eligible })
    vi.spyOn(api, 'backtestProfile').mockResolvedValue(profileState(true))

    render(<ProfessionalBacktest />)

    expect(await screen.findByRole('heading', { name: '结果图谱' })).toBeVisible()
    expect(screen.getByRole('region', { name: '净收益对照' })).toBeVisible()
    expect(screen.getByRole('region', { name: 'Profit Factor 对照' })).toBeVisible()
    expect(screen.getByRole('region', { name: '参数风险收益分布' })).toBeVisible()
    expect(screen.getByRole('region', { name: '独立路径前十的 OOS 收益' })).toBeVisible()
    expect(screen.getByRole('region', { name: 'WF 窗口稳定性' })).toBeVisible()
    expect(screen.getAllByTestId('result-echart')).toHaveLength(5)
    expect(screen.getByLabelText('入选参数摘要')).toHaveTextContent('止盈')
    expect(screen.getByLabelText('入选参数摘要')).toHaveTextContent('最长持有')
    expect(screen.getByLabelText('入选参数摘要')).toHaveTextContent('二次出货观察窗')
    expect(screen.getByRole('heading', { name: '入选参数与独立路径排行榜' })).toBeVisible()
    expect(screen.getByRole('columnheader', { name: '等效参数' })).toBeVisible()
    expect(screen.getByText('2 组', { exact: true })).toBeVisible()
    expect(screen.getByText('1 组未进入路径比较')).toBeVisible()
    expect(screen.getAllByText('8.00%').length).toBeGreaterThan(0)
    expect(screen.getByText('旧记录 0（过滤前未记录）')).toBeVisible()
    expect(screen.getByText(/这是抽样研究，不是逐日完整回测/)).toBeVisible()
    expect(screen.getAllByText('15.00%')).toHaveLength(2)
    expect(screen.getByText(/不绘制或推测净值曲线/)).toBeVisible()
  })

  it('逐日预登记机制明确展示时点与不可启用边界', async () => {
    const researchTask: BacktestTask = {
      ...completedTask,
      request: {
        ...completedTask.request,
        sample_step: 1,
        entry_mechanism: {
          id: 'POST_BREAKOUT_SUPPLY_DRY_UP_V1',
          version: 'post-breakout-supply-dry-up-v1.0.0',
          semantic_hash: 'frozen-research-semantic-hash',
          research_only: true,
          benchmark_code: null,
          parameter_search: 'none',
        },
      },
      result: {
        ...completedTask.result!,
        verdict: 'HISTORICAL_SUPPORT_ONLY',
        verdict_label: '预登记机制获得历史支持，但不能直接晋级',
        request: {
          ...completedTask.result!.request,
          sample_step: 1,
          entry_mechanism: {
            id: 'POST_BREAKOUT_SUPPLY_DRY_UP_V1',
            version: 'post-breakout-supply-dry-up-v1.0.0',
            semantic_hash: 'frozen-research-semantic-hash',
            research_only: true,
            benchmark_code: null,
            parameter_search: 'none',
          },
        },
      },
      profile_activation: {
        ...eligible,
        can_activate: false,
        checks: [{
          code: 'ENTRY_MECHANISM_PRODUCTION_BASE', label: '入场机制边界', passed: false,
          message: '预登记研究机制不能启用为今日选股档案',
        }],
        reasons: [{
          code: 'ENTRY_MECHANISM_PRODUCTION_BASE', label: '入场机制边界', passed: false,
          message: '预登记研究机制不能启用为今日选股档案',
        }],
      },
    }
    vi.spyOn(api, 'backtestCatalog').mockResolvedValue(catalog)
    vi.spyOn(api, 'backtestUniverse').mockResolvedValue({
      classification: 'industry', classification_title: '细分行业', group_label: '行业',
      classification_mode: 'CURRENT_CLASSIFICATION_FROZEN_UNIVERSE', classification_note: '当前分类只用于选择',
      classifications: [], groups: [], industries: [], stocks: [], stock_count: 30,
    })
    vi.spyOn(api, 'backtestLatest').mockResolvedValue({
      task: researchTask,
      profile_activation: researchTask.profile_activation,
    })
    vi.spyOn(api, 'backtestProfile').mockResolvedValue(profileState(true))

    render(<ProfessionalBacktest />)

    expect(await screen.findByText(/预登记研究机制：POST_BREAKOUT_SUPPLY_DRY_UP_V1/)).toBeVisible()
    expect(screen.getByText(/t0 严格突破 → t1 下一交易日收盘确认 → 最早 t2/)).toBeVisible()
    expect(screen.getByText('逐交易日完整复验')).toBeVisible()
    expect(screen.getByText(/不能启用为今日选股档案/)).toBeVisible()
    expect(screen.queryByRole('button', { name: '人工启用为今日选股参数' })).not.toBeInTheDocument()
  })

  it('旧任务缺少权益哈希时明确保留名义排行榜且不推测去重', async () => {
    vi.spyOn(api, 'backtestCatalog').mockResolvedValue(catalog)
    vi.spyOn(api, 'backtestUniverse').mockResolvedValue({
      classification: 'industry', classification_title: '细分行业', group_label: '行业',
      classification_mode: 'CURRENT_CLASSIFICATION_FROZEN_UNIVERSE', classification_note: '当前分类只用于选择',
      classifications: [], groups: [], industries: [], stocks: [], stock_count: 30,
    })
    vi.spyOn(api, 'backtestLatest').mockResolvedValue({
      task: {
        ...completedTask,
        result: {
          ...completedTask.result!,
          path_analysis: undefined,
          independent_leaderboard: undefined,
        },
      },
      profile_activation: eligible,
    })
    vi.spyOn(api, 'backtestProfile').mockResolvedValue(profileState(true))

    render(<ProfessionalBacktest />)

    expect(await screen.findByRole('heading', { name: '入选参数与历史名义排行榜' })).toBeVisible()
    expect(screen.getByText('结果缺少可验证权益路径，排行榜未去重')).toBeVisible()
    expect(screen.getByRole('columnheader', { name: '路径证据' })).toBeVisible()
    expect(screen.getAllByText('未去重').length).toBeGreaterThan(0)
    expect(screen.getByRole('region', { name: '历史名义参数前十的 OOS 收益' })).toBeVisible()
  })

  it('缺失指标保持空状态，不把 null 画成零收益', () => {
    const base = completedTask.result!
    const missingMetrics = {
      net_n_trades: 0,
      net_avg_return: null,
      net_total_return: null,
      net_profit_factor: null,
      net_max_drawdown: null,
      portfolio_total_return: null,
      portfolio_max_drawdown: null,
    }
    render(<BacktestResultCharts result={{
      ...base,
      selected: { ...base.selected!, is: missingMetrics, oos: missingMetrics },
      leaderboard: [{ ...base.leaderboard[0], is: missingMetrics, oos: missingMetrics }],
      independent_leaderboard: [{ ...base.leaderboard[0], is: missingMetrics, oos: missingMetrics }],
      wf: { evidence_complete: false, wf_pass: false, wf_detail: [{ window: 'WF1', train_pf: null, test_pf: null }] },
      baselines: { random: missingMetrics, ma20_60: missingMetrics },
      cost_stress: { multiplier: '2x', metrics: missingMetrics },
    }} />)

    expect(screen.queryAllByTestId('result-echart')).toHaveLength(0)
    expect(screen.getAllByText('暂无可绘制数据')).toHaveLength(5)
  })

  it('切换分类标准后把所选细分方向写入预览请求', async () => {
    vi.spyOn(api, 'backtestCatalog').mockResolvedValue(catalog)
    vi.spyOn(api, 'backtestUniverse').mockImplementation(async (classification = 'industry') => ({
      classification,
      classification_title: classification === 'market' ? '上市板块' : '细分行业',
      group_label: classification === 'market' ? '板块' : '行业',
      classification_mode: 'CURRENT_CLASSIFICATION_FROZEN_UNIVERSE',
      classification_note: '当前分类只用于选择',
      classifications: [
        { key: 'industry', title: '细分行业', group_label: '行业', description: '产业细分', pit_status: 'CURRENT_SNAPSHOT_ONLY', group_count: 1 },
        { key: 'market', title: '上市板块', group_label: '板块', description: '上市制度分组', pit_status: 'CURRENT_SNAPSHOT_ONLY', group_count: 2 },
        { key: 'area', title: '地域', group_label: '地区', description: '公司注册地', pit_status: 'CURRENT_SNAPSHOT_ONLY', group_count: 2 },
      ],
      groups: classification === 'market'
        ? [{ name: '创业板', count: 30 }, { name: '主板', count: 60 }]
        : [{ name: '半导体', count: 30 }],
      industries: classification === 'industry' ? [{ name: '半导体', count: 30 }] : [],
      stocks: [], stock_count: 90,
    }))
    vi.spyOn(api, 'backtestLatest').mockResolvedValue({ task: null })
    const previewSpy = vi.spyOn(api, 'backtestPreview').mockResolvedValue(preview)

    render(<ProfessionalBacktest />)
    await screen.findByRole('heading', { name: '多参数研究回测' })
    fireEvent.change(screen.getByLabelText('分类标准'), { target: { value: 'market' } })
    const group = await screen.findByRole('checkbox', { name: /创业板/ })
    fireEvent.click(group)
    fireEvent.click(screen.getByRole('button', { name: '检查参数空间' }))

    await waitFor(() => expect(previewSpy).toHaveBeenCalledOnce())
    expect(previewSpy.mock.calls[0][0].universe).toEqual({
      classification: 'market', groups: ['创业板'], codes: [],
    })
  })

  it('合格结果仍需人工确认后才启用为今日选股参数', async () => {
    vi.spyOn(api, 'backtestCatalog').mockResolvedValue(catalog)
    vi.spyOn(api, 'backtestUniverse').mockResolvedValue({
      classification: 'industry', classification_title: '细分行业', group_label: '行业',
      classification_mode: 'CURRENT_CLASSIFICATION_FROZEN_UNIVERSE', classification_note: '当前分类只用于选择',
      classifications: [], groups: [], industries: [], stocks: [], stock_count: 30,
    })
    vi.spyOn(api, 'backtestLatest').mockResolvedValue({ task: completedTask, profile_activation: eligible })
    vi.spyOn(api, 'backtestProfile').mockResolvedValue(profileState(true))
    const activate = vi.spyOn(api, 'activateBacktestProfile').mockResolvedValue({
      ...profileState(false), activation: { ...eligible, already_active: true }, idempotent: false,
    })
    vi.spyOn(api, 'backtestStatus').mockResolvedValue({
      ...completedTask, profile_activation: { ...eligible, already_active: true },
    })
    vi.spyOn(window, 'confirm').mockReturnValue(true)

    render(<ProfessionalBacktest />)
    const button = await screen.findByRole('button', { name: '人工启用为今日选股参数' })
    expect(screen.getByText('证据门槛通过，可人工启用')).toBeVisible()
    fireEvent.click(button)

    await waitFor(() => expect(activate).toHaveBeenCalledWith('probt-good'))
    expect(await screen.findByText(/下一次今日扫描会冻结并使用/)).toBeVisible()
  })

  it('超过 512 组不拦截但启动前弹出长耗时确认', async () => {
    const longPreview: BacktestPreview = {
      ...preview,
      estimated_work: {
        ...preview.estimated_work,
        combinations: 576,
        long_running: true,
        warning_threshold: 512,
        note: '组合数超过常规阈值，可能持续数小时',
      },
      prepared: {
        ...preview.prepared,
        parameter_space: {
          ...preview.prepared.parameter_space,
          count: 576,
          long_running: true,
        },
      },
    }
    vi.spyOn(api, 'backtestCatalog').mockResolvedValue(catalog)
    vi.spyOn(api, 'backtestUniverse').mockResolvedValue({
      classification: 'industry', classification_title: '细分行业', group_label: '行业',
      classification_mode: 'CURRENT_CLASSIFICATION_FROZEN_UNIVERSE', classification_note: '当前分类只用于选择',
      classifications: [], groups: [], industries: [], stocks: [], stock_count: 30,
    })
    vi.spyOn(api, 'backtestLatest').mockResolvedValue({ task: null, profile_activation: eligible })
    vi.spyOn(api, 'backtestProfile').mockResolvedValue(profileState(true))
    vi.spyOn(api, 'backtestPreview').mockResolvedValue(longPreview)
    vi.spyOn(api, 'backtestRun').mockResolvedValue({ task_id: 'probt-good', status: 'done', cached: true })
    vi.spyOn(api, 'backtestStatus').mockResolvedValue(completedTask)
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)

    render(<ProfessionalBacktest />)
    await screen.findByRole('heading', { name: '多参数研究回测' })
    fireEvent.click(screen.getByRole('button', { name: '检查参数空间' }))
    fireEvent.click(await screen.findByRole('button', { name: '查看冻结预览' }))
    await screen.findByText(/长耗时任务：已超过 512 组/)
    fireEvent.click(screen.getByRole('button', { name: '启动研究回测' }))

    await waitFor(() => expect(confirm).toHaveBeenCalledWith(expect.stringContaining('长耗时提醒：将运行 576 组参数')))
  })
})
