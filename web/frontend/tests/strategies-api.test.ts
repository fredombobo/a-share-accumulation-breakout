import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  fetchStrategies,
  fetchStrategyVersions,
} from '../src/api/strategies'
import type { StrategyInfo } from '../src/types/strategies'


const strategy: StrategyInfo = {
  strategy_definition_id: 'accumulation_breakout_v1',
  version: 'v1',
  research_status: 'EXPERIMENTAL',
  economic_assumption: '横盘吸筹后放量突破',
  failure_conditions: '跌回箱体',
  config_path: 'configs/strategies/accumulation_breakout_v1.yaml',
  strategy_hash: 'hash-1',
}


function response(body: unknown): Response {
  return {
    ok: true,
    status: 200,
    json: async () => body,
  } as Response
}


afterEach(() => {
  vi.unstubAllGlobals()
})


describe('策略注册表 API 契约', () => {
  it('从后端 registry 包装对象提取策略数组', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      response({ strategies: [strategy], count: 1 }),
    )
    vi.stubGlobal('fetch', fetchMock)

    await expect(fetchStrategies()).resolves.toEqual([strategy])
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v2/strategies',
      expect.objectContaining({ headers: expect.any(Object) }),
    )
  })

  it('缺少 strategies 数组时显示可理解错误而不是页面崩溃', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response({ count: 0 })))

    await expect(fetchStrategies()).rejects.toThrow('策略注册表响应格式无效')
  })

  it('版本接口按后端单对象契约返回', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response(strategy)))

    await expect(fetchStrategyVersions('accumulation_breakout_v1')).resolves.toEqual(
      strategy,
    )
  })
})
