import { describe, expect, it } from 'vitest'

import { pageMeta } from '../src/layout/Topbar'


describe('顶栏路由元数据', () => {
  it.each([
    ['/', '每日选股'],
    ['/stock/000001.SZ', '个股详情'],
    ['/backtest', '研究回测'],
  ])('为 %s 返回对应标题', (path, expectedTitle) => {
    expect(pageMeta(path).title).toBe(expectedTitle)
  })

  it('未知路由使用最小产品首页元数据', () => {
    expect(pageMeta('/unknown')).toEqual({
      kicker: 'Daily Workflow',
      title: '每日选股',
      sub: '更新行情 · 扫描候选 · 核对证据',
    })
  })
})
