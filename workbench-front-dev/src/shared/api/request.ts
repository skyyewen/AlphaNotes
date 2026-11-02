import axios, { AxiosHeaders, type AxiosInstance } from 'axios'
import { abortedError, ApiError, httpError } from './errors'

export interface RequestOptions {
  method?: 'GET' | 'POST'
  body?: unknown
  headers?: Record<string, string>
  signal?: AbortSignal
  /** Per-request timeout; ordinary requests keep the 15s client default. */
  timeoutMs?: number
  /** Injectable only for tests or an explicitly configured Axios client. */
  client?: Pick<AxiosInstance, 'request'>
}

const defaultClient = axios.create({ timeout: 15_000 })

/** An empty base URL uses the current origin. No remote service is assumed. */
export function getApiBaseUrl(): string {
  return (import.meta.env.VITE_API_BASE_URL ?? '').trim().replace(/\/+$/, '')
}

export function resolveApiUrl(path: string): string {
  if (!path.startsWith('/') || path.startsWith('//')) {
    throw new ApiError('接口路径必须是以单个 / 开始的相对路径。', {
      kind: 'protocol',
      code: 'INVALID_API_PATH',
    })
  }
  return `${getApiBaseUrl()}${path}`
}

/** Returns the JSON body unchanged; it does not assume a success/data envelope. */
export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const method = options.method ?? 'GET'
  if (options.signal?.aborted) throw abortedError()
  if (method === 'GET' && options.body !== undefined) {
    throw new ApiError('GET 请求不能携带请求体。', {
      kind: 'protocol',
      code: 'INVALID_REQUEST_BODY',
    })
  }

  const url = resolveApiUrl(path)
  const headers = AxiosHeaders.from(options.headers)
  if (!headers.has('Accept')) headers.set('Accept', 'application/json')
  if (options.body instanceof FormData) {
    // The browser must supply the multipart boundary.
    headers.delete('Content-Type')
  } else if (options.body !== undefined && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json')
  }

  try {
    const response = await (options.client ?? defaultClient).request<string>({
      url,
      method,
      data: options.body,
      headers,
      signal: options.signal,
      ...(options.timeoutMs === undefined ? {} : { timeout: options.timeoutMs }),
      responseType: 'text',
      // Axios normally silently accepts invalid JSON. Parse explicitly below.
      transformResponse: [(data: unknown) => data],
      validateStatus: () => true,
    })
    if (options.signal?.aborted) throw abortedError()
    if (response.status < 200 || response.status >= 300) throw httpError(response.status)
    if (response.status === 204 || response.status === 205) return undefined as T
    if (typeof response.data !== 'string') {
      throw new ApiError('接口未返回有效的 JSON 文本。', {
        kind: 'invalid_response',
        code: 'INVALID_JSON',
      })
    }
    try {
      return JSON.parse(response.data) as T
    } catch {
      throw new ApiError('接口返回的 JSON 格式不正确。', {
        kind: 'invalid_response',
        code: 'INVALID_JSON',
      })
    }
  } catch (error: unknown) {
    if (error instanceof ApiError) throw error
    if (options.signal?.aborted || axios.isCancel(error)) throw abortedError()
    if (axios.isAxiosError(error) && error.response) throw httpError(error.response.status)
    throw new ApiError('无法连接接口，请检查网络和后端服务。', {
      kind: 'network',
      code: 'NETWORK_ERROR',
    })
  }
}
