export type ApiErrorKind =
  | 'http'
  | 'network'
  | 'invalid_response'
  | 'protocol'
  | 'backend'
  | 'aborted'

export interface ApiErrorOptions {
  kind: ApiErrorKind
  code: string
  status?: number
}

/** Safe to display: never contains response bodies, request headers, or secrets. */
export class ApiError extends Error {
  readonly kind: ApiErrorKind
  readonly code: string
  readonly status?: number

  constructor(message: string, options: ApiErrorOptions) {
    super(message)
    this.name = 'ApiError'
    this.kind = options.kind
    this.code = options.code
    this.status = options.status
  }
}

export function abortedError(): ApiError {
  return new ApiError('已停止浏览器等待；后台任务不一定已取消。', {
    kind: 'aborted',
    code: 'REQUEST_ABORTED',
  })
}

export function httpError(status: number): ApiError {
  return new ApiError(`接口返回 HTTP ${status}，请稍后重试或检查服务。`, {
    kind: 'http',
    code: 'HTTP_ERROR',
    status,
  })
}
