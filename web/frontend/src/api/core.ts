/**
 * v2 API 核心：请求原语与错误类型（P7.2）。
 * 各领域模块（desk/intelligence/...）从这里取 request / ApiError / 幂等键。
 */
export {
  ApiError,
  request,
  paperWrite,
  newIdempotencyKey,
  type ReqOpts,
} from './client'

// request() already prefixes every path with `/api`.
export const V2_BASE = '/v2'
