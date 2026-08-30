import { useEffect, useState } from 'react'

import { api, type ClassificationCatalogResp } from '../api/client'

const contents = [
  ['quick-start', '每天怎么用'],
  ['selection-logic', '系统怎么选股'],
  ['pools', 'A 池和 B 池'],
  ['classifications', '板块分类标准'],
  ['backtest', '专业回测'],
  ['profile-loop', '回测参数用于选股'],
  ['results', '如何阅读结果'],
  ['progress', '进度与异常'],
  ['boundaries', '系统边界'],
] as const

export default function Guide() {
  const [catalog, setCatalog] = useState<ClassificationCatalogResp | null>(null)

  useEffect(() => {
    let active = true
    api.classifications().then((data) => active && setCatalog(data)).catch(() => undefined)
    return () => { active = false }
  }, [])

  return (
    <div className="manual-shell fade-up">
      <section className="manual-intro">
        <div>
          <span className="guide-eyebrow">AB-Screener 操作手册</span>
          <h1>从更新行情到读懂回测</h1>
          <p>本页解释系统做什么、每天点哪里、筛选依据是什么，以及哪些结论不能直接用于交易。</p>
        </div>
        <div className="manual-quickline">
          <b>最短使用路径</b>
          <span>更新行情</span><i />
          <span>运行扫描</span><i />
          <span>查看 A 池证据</span>
        </div>
      </section>

      <div className="manual-layout">
        <nav className="manual-toc" aria-label="说明书目录">
          <strong>目录</strong>
          {contents.map(([id, label]) => <a key={id} href={`#${id}`}>{label}</a>)}
        </nav>

        <main className="manual-content">
          <section id="quick-start" className="manual-section">
            <h2>每天怎么用</h2>
            <div className="manual-flow">
              <article><b>更新行情</b><p>顶部点“更新”。确认数据日是最新已完成交易日，过期数据不能当作今日结果。</p></article>
              <article><b>运行扫描</b><p>首页点“扫描”。顶部全局进度会显示真实阶段、百分比、耗时和最近推进。</p></article>
              <article><b>先看 A 池</b><p>A 池满足严格形态、资金和环境门禁。B 池只用于观察，不与 A 池混排。</p></article>
              <article><b>核对单股证据</b><p>打开个股详情，检查箱体、突破量、资金流、风险提示和 AI 本地证据。</p></article>
            </div>
          </section>

          <section id="selection-logic" className="manual-section">
            <h2>系统怎么选股</h2>
            <p>核心经济假设是“较长时间横盘吸筹后出现可验证的放量突破”。系统按以下顺序失败关闭：</p>
            <dl className="logic-list">
              <div><dt>数据时点</dt><dd>只使用决策时点已经可用的数据。行情过期、缺字段或交易日不完整时不宣称有效结果。</dd></div>
              <div><dt>技术形态</dt><dd>检查箱体持续时间、振幅、支撑压力触及、中部占用、漂移、突破幅度和双重量能。</dd></div>
              <div><dt>资金与基本面</dt><dd>核对主力资金方向、成交额、估值和可用财务指标。缺失项会披露，不会静默补值。</dd></div>
              <div><dt>市场环境</dt><dd>防守环境禁止新开仓，A 池可以为空。空池是风险结果，不代表程序失效。</dd></div>
              <div><dt>候选分层</dt><dd>严格满足门禁的进入 A 池；放宽或主题观察进入 B 池。</dd></div>
            </dl>
          </section>

          <section id="pools" className="manual-section">
            <h2>A 池和 B 池怎么理解</h2>
            <div className="manual-compare">
              <article><b>A 池，可交易候选</b><p>通过严格筛选和当日市场环境检查。它只是研究候选，仍需人工核对风险，不等于买入指令。</p></article>
              <article><b>B 池，观察名单</b><p>可能是条件放宽、主题补充或尚未确认的形态。用于跟踪，不应冒充 A 池。</p></article>
            </div>
          </section>

          <section id="classifications" className="manual-section">
            <h2>板块分类标准</h2>
            <p>首页资金图和专业回测使用同一套分类定义。切换分类只改变如何分组，不改变原始资金流或股票行情。</p>
            <div className="classification-manual" aria-label="当前分类能力">
              {(catalog?.items || []).map((item) => (
                <article key={item.key}>
                  <div><b>{item.title}</b><span>{item.group_count} 个{item.group_label}</span></div>
                  <p>{item.description}</p>
                  <small>当前覆盖 {item.coverage_pct.toFixed(1)}%。示例：{item.examples.slice(0, 4).join('、') || '暂无'}</small>
                </article>
              ))}
              {!catalog && <div className="loading">正在读取本地分类能力...</div>}
            </div>
            <div className="manual-warning">
              <b>分类时点限制</b>
              <p>{catalog?.limitations || '分类来自当前 stock_basic 快照，不等同历史成员 PIT。'}</p>
              <p>申万、中信和概念板块只有在补齐历史成员、available_at 和版本数据后才能开放正式回测。</p>
            </div>
          </section>

          <section id="backtest" className="manual-section">
            <h2>专业回测怎么操作</h2>
            <ol className="manual-steps">
              <li><b>冻结股票池</b><span>选择细分行业、上市板块或地域，再多选分组。也可直接填写股票代码，代码优先。</span></li>
              <li><b>设置参数空间</b><span>默认搜索横盘最长 60 至 200 日、突破量比、止损和退出窗口。组合上限 512。</span></li>
              <li><b>检查参数空间</b><span>先看有效组合、冻结股票数、动态预热和研究窗口。任何输入变化后都要重新预览。</span></li>
              <li><b>启动并等待</b><span>任务依次完成数据冻结、IS/OOS、WF、基准、成本压力和结论。切页不会中断。</span></li>
            </ol>
            <p className="manual-note">收盘信号最早在下一交易日开盘模拟成交，不存在同一收盘价无摩擦成交路径。</p>
          </section>

          <section id="profile-loop" className="manual-section">
            <h2>怎样把回测参数用于今日选股</h2>
            <ol className="manual-steps">
              <li><b>先取得合格证据</b><span>结果必须完成 OOS、WF、随机与均线基线以及 2 倍成本压力；只看 IS 排名不算“好参数”。</span></li>
              <li><b>人工启用参数</b><span>回测结论达到候选门槛且代码、数据身份仍有效时，结果页才会出现“人工启用为今日选股参数”。系统绝不自动上线最佳曲线。</span></li>
              <li><b>运行今日扫描</b><span>首页会显示当前参数版本。扫描启动时冻结该快照和哈希，A 池技术入场检测使用同一组横盘、突破和量能参数。</span></li>
              <li><b>核对并可回退</b><span>资金流、基本面、流动性和市场环境仍会额外过滤候选；B 池仍是固定宽松观察规则。可随时恢复系统默认，历史审计不会删除。</span></li>
            </ol>
            <div className="manual-warning">
              <b>为什么回测结果与 A 池不一定逐只相同</b>
              <p>闭环统一的是横盘吸筹突破的技术入场参数。今日扫描还承担数据新鲜度、市场环境、资金质量、基本面和评分门禁；这些门禁是为了让当日候选更可用，不能为了复刻回测而绕过。</p>
            </div>
          </section>

          <section id="results" className="manual-section">
            <h2>如何阅读回测结果</h2>
            <dl className="term-grid">
              <div><dt>IS</dt><dd>样本内，只用于选择参数。</dd></div>
              <div><dt>OOS</dt><dd>样本外，用未参与选参的数据验证。</dd></div>
              <div><dt>WF</dt><dd>滚动窗口复验，检查不同时间段稳定性。</dd></div>
              <div><dt>基准</dt><dd>随机和均线策略对照，避免只看自己的曲线。</dd></div>
              <div><dt>成本压力</dt><dd>提高滑点和费用后复算，检验收益是否脆弱。</dd></div>
              <div><dt>最大回撤</dt><dd>历史模拟中从高点到低点的最大跌幅。</dd></div>
            </dl>
            <p>回测页的结论是探索证据。只有门槛通过后，用户才能人工启用为每日 A 池技术参数；它不会自动改变选股，也不会自动晋级正式研究参数。</p>
          </section>

          <section id="progress" className="manual-section">
            <h2>进度与常见异常</h2>
            <dl className="logic-list compact">
              <div><dt>看不到进度</dt><dd>没有活动任务时全局进度自动隐藏。启动扫描、回测或同步后会重新出现。</dd></div>
              <div><dt>三分钟无变化</dt><dd>界面会提示“可能仍在重计算”。点“查看任务”检查，不要反复启动。</dd></div>
              <div><dt>A 池为空</dt><dd>先看市场环境、数据新鲜度和扫描状态。防守期清空 A 池属于正常门禁。</dd></div>
              <div><dt>回测无法预览</dt><dd>常见原因是股票少于 20 只、未知分组、参数组合超过 512 或历史窗口不足。</dd></div>
              <div><dt>分类找不到</dt><dd>当前只开放本地有真实字段的分类。未接入历史成员的数据不会显示。</dd></div>
            </dl>
          </section>

          <section id="boundaries" className="manual-section manual-boundary">
            <h2>系统边界</h2>
            <ul>
              <li>这是研究和选股辅助工具，不是投资建议。</li>
              <li>系统不连接券商，不生成真实订单，真实交易开关保持关闭。</li>
              <li>AI 只解释本地证据，不改变分数、A/B 池或回测结论。</li>
              <li>当前分类是当前快照。历史分类无 PIT 数据时不得宣称行业回测无未来信息。</li>
              <li>失败、证据不足和防守状态必须如实显示，不能改写成通过。</li>
            </ul>
          </section>
        </main>
      </div>
    </div>
  )
}
