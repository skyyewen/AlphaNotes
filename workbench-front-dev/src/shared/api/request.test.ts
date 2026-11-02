import axios, { AxiosError, AxiosHeaders, CanceledError, type AxiosAdapter } from 'axios'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from './errors'
import { getApiBaseUrl, request } from './request'

afterEach(() => vi.unstubAllEnvs())

function clientFor(data: string, status = 200, inspect?: AxiosAdapter) {
  const adapter = vi.fn<AxiosAdapter>(async (config) => {
    if (inspect) return inspect(config)
    return { data, status, statusText: '', headers: {}, config }
  })
  return { client: axios.create({ adapter }), adapter }
}

describe('JSON request', () => {
  it('uses an empty default base and returns the JSON unchanged', async () => {
    vi.stubEnv('VITE_API_BASE_URL', '')
    const { client, adapter } = clientFor('[{"id":"one"}]')
    expect(getApiBaseUrl()).toBe('')
    expect(await request('/api/records', { client })).toEqual([{ id: 'one' }])
    expect(adapter.mock.calls[0]?.[0].url).toBe('/api/records')
  })

  it('honors only an explicitly configured base URL', async () => {
    vi.stubEnv('VITE_API_BASE_URL', ' http://localhost:8000/ ')
    const { client, adapter } = clientFor('{"ok":true}')
    await request('/api/health', { client })
    expect(adapter.mock.calls[0]?.[0].url).toBe('http://localhost:8000/api/health')
  })

  it('uses a dedicated long timeout only on the requested operation', async () => {
    const { client, adapter } = clientFor('{}')
    await request('/api/research/run', { client, method: 'POST', body: {}, timeoutMs: 200_000 })
    expect(adapter.mock.calls[0]?.[0].timeout).toBe(200_000)
    await request('/api/documents', { client })
    expect(adapter.mock.calls[1]?.[0].timeout).not.toBe(200_000)
    expect(adapter).toHaveBeenCalledTimes(2)
  })

  it('posts JSON, passes custom headers/signal, and makes one attempt', async () => {
    vi.stubEnv('VITE_API_BASE_URL', '')
    const signal = new AbortController().signal
    const { client, adapter } = clientFor('{"id":"new"}')
    const result = await request('/api/records', {
      client,
      method: 'POST',
      body: { title: '研究记录' },
      headers: { 'X-Request-Id': 'request-1' },
      signal,
    })
    expect(result).toEqual({ id: 'new' })
    expect(adapter).toHaveBeenCalledTimes(1)
    const config = adapter.mock.calls[0]?.[0]
    expect(config?.method).toBe('post')
    expect(config?.data).toBe('{"title":"研究记录"}')
    expect(config?.signal).toBe(signal)
    expect(AxiosHeaders.from(config?.headers).get('X-Request-Id')).toBe('request-1')
  })

  it('accepts FormData without JSON serializing it', async () => {
    const body = new FormData()
    body.append('file', new Blob(['# document']), 'document.md')
    const { client, adapter } = clientFor('{"document_id":"doc-1"}')
    await request('/api/documents', { client, method: 'POST', body })
    expect(adapter.mock.calls[0]?.[0].data).toBe(body)
  })

  it('accepts a no-content response', async () => {
    const { client } = clientFor('', 204)
    expect(await request('/api/records', { client, method: 'POST' })).toBeUndefined()
  })

  it('rejects HTTP errors without leaking response content or retrying a POST', async () => {
    const { client, adapter } = clientFor('{"detail":"private token"}', 503)
    const error = await request('/api/records', { client, method: 'POST' }).catch((value: unknown) => value)
    expect(error).toBeInstanceOf(ApiError)
    expect(error).toMatchObject({ kind: 'http', code: 'HTTP_ERROR', status: 503 })
    expect(String(error)).not.toContain('private token')
    expect(adapter).toHaveBeenCalledTimes(1)
  })

  it('separates a network failure from invalid response JSON', async () => {
    const { client } = clientFor('', 200, async (config) => {
      throw new AxiosError('secret endpoint or header', 'ERR_NETWORK', config)
    })
    const networkError = await request('/api/records', { client }).catch((value: unknown) => value)
    expect(networkError).toMatchObject({ kind: 'network', code: 'NETWORK_ERROR' })
    expect(String(networkError)).not.toContain('secret')
    const invalid = clientFor('<html>proxy failed</html>')
    await expect(request('/api/records', { client: invalid.client })).rejects.toMatchObject({
      kind: 'invalid_response', code: 'INVALID_JSON',
    })
  })

  it('classifies an Axios-rejected HTTP response safely', async () => {
    const { client } = clientFor('', 200, async (config) => {
      throw new AxiosError('private response', 'ERR_BAD_RESPONSE', config, undefined, {
        data: 'secret', status: 500, statusText: '', headers: {}, config,
      })
    })
    await expect(request('/api/records', { client })).rejects.toMatchObject({ kind: 'http', status: 500 })
  })

  it('honors an already aborted request without contacting the adapter', async () => {
    const controller = new AbortController()
    controller.abort()
    const { client, adapter } = clientFor('{}')
    await expect(request('/api/records', { client, signal: controller.signal })).rejects.toMatchObject({
      kind: 'aborted', code: 'REQUEST_ABORTED',
    })
    expect(adapter).not.toHaveBeenCalled()
  })

  it('recognizes an Axios cancellation', async () => {
    const { client } = clientFor('', 200, async () => { throw new CanceledError('internal details') })
    await expect(request('/api/records', { client })).rejects.toMatchObject({ kind: 'aborted' })
  })

  it('rejects absolute paths and GET bodies locally', async () => {
    const { client, adapter } = clientFor('{}')
    await expect(request('https://unexpected.example/path', { client })).rejects.toMatchObject({
      code: 'INVALID_API_PATH',
    })
    await expect(request('//unexpected.example/path', { client })).rejects.toMatchObject({
      code: 'INVALID_API_PATH',
    })
    await expect(request('/api/records', { client, body: {} })).rejects.toMatchObject({
      code: 'INVALID_REQUEST_BODY',
    })
    expect(adapter).not.toHaveBeenCalled()
  })
})
