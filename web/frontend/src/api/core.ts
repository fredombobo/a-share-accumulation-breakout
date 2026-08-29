/**
 * v2 API 核心：请求原语与错误类型（P7.2）。
 * 各领域模块（desk/intelligence/...）从这里取 request / ApiError / 幂等键。
 */
export {
  ApiError,
  request,
  type ReqOpts,
} from './client'

// request() already prefixes every path with /api.  Keeping /api here produced
// /api/api/v2/* and made all v2 pages silently fail behind mocked UI tests.
export const V2_BASE = '/v2'
