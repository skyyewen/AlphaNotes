import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from './errors'
import {
  parseResearchEvent,
  SseFrameParser,
  streamResearch,
  Utf8StreamDecoder,
  type ResearchEvent,
} from './stream'

afterEach(() => vi.unstubAllEnvs())

const encoder = new TextEncoder()
const start = { type: 'run_started', run_id: 'run-1', seq: 0 }
const done = { type: 'done', run_id: 'run-1', seq: 9, status: 'success' }

function frame(event: Record<string, unknown>, newline = '\n'): string {
  return `event: ${event.type}${newline}data: ${JSON.stringify(event)}${newline}${newline}`
}

function responseFor(text: string, chunkSize = Number.POSITIVE_INFINITY): Response {
  const bytes = encoder.encode(text)
  const chunks = new ReadableStream<Uint8Array>({
    start(controller) {
      for (let offset = 0; offset < bytes.length; offset += chunkSize) {
        controller.enqueue(bytes.slice(offset, offset + chunkSize))
      }
      controller.close()
    },
  })
  return new Response(chunks, { headers: { 'Content-Type': 'text/event-stream; charset=utf-8' } })
}

function runText(text: string, extra: Partial<Parameters<typeof streamResearch>[0]> = {}) {
  const fetcher = vi.fn<typeof fetch>().mockResolvedValue(responseFor(text))
  return streamResearch({ path: '/api/agent/research/stream', body: { question: '测试' },
    fetcher, onEvent: () => undefined, ...extra })
}

describe('UTF-8 decoder and SSE frame parser', () => {
  it('retains split Chinese bytes and rejects malformed/truncated UTF-8', () => {
    const decoder = new Utf8StreamDecoder()
    const bytes = encoder.encode('财报')
    expect(decoder.push(bytes.slice(0, 1))).toBe('')
    expect(decoder.push(bytes.slice(1, 4))).toBe('财')
    expect(decoder.push(bytes.slice(4)) + decoder.finish()).toBe('报')
    expect(() => new Utf8StreamDecoder().push(new Uint8Array([0xff]))).toThrow(ApiError)
    const incomplete = new Utf8StreamDecoder()
    incomplete.push(new Uint8Array([0xe4]))
    expect(() => incomplete.finish()).toThrow(ApiError)
  })

  it('handles CRLF across chunks, comments, IDs and multiple data lines', () => {
    const parser = new SseFrameParser()
    expect(parser.push(': keepalive\r')).toEqual([])
    expect(parser.push('\nid: event-1\r\nevent: delta\r\ndata: {"text":\r')).toEqual([])
    expect(parser.push('\ndata: "中文"}\r\n\r')).toEqual([
      { event: 'delta', id: 'event-1', data: '{"text":\n"中文"}' },
    ])
    expect(parser.push('\n')).toEqual([])
    parser.finish()
  })

  it('accepts CR-only frames and keeps last event IDs as the SSE standard specifies', () => {
    const parser = new SseFrameParser()
    expect(parser.push('id: a\rdata: first\r\rdata: second\r\r')).toEqual([
      { event: 'message', id: 'a', data: 'first' },
      { event: 'message', id: 'a', data: 'second' },
    ])
  })

  it('does not dispatch an unclosed event and bounds pending frame size', () => {
    const parser = new SseFrameParser()
    expect(parser.push('event: delta\ndata: {"text":"partial"}\n')).toEqual([])
    expect(() => parser.finish()).toThrow(ApiError)
    expect(() => new SseFrameParser(10).push('data: ' + 'x'.repeat(20))).toThrow(ApiError)
    expect(() => new SseFrameParser(0)).toThrow(ApiError)
  })
})

describe('research event validation', () => {
  it.each([
    { type: 'delta', run_id: 'run-1', seq: 1 },
    { type: 'draft_ready', run_id: 'run-1', seq: 1 },
    { type: 'done', run_id: 'run-1', seq: 1 },
    { type: 'step', run_id: 'run-1', seq: 1, step: 'search' },
    { type: 'citations', run_id: 'run-1', seq: 1, citations: { one: null } },
    { type: 'delta', seq: 1, text: 'missing run' },
    { type: 'delta', run_id: 'run-1', seq: '1', text: 'wrong sequence type' },
    { type: 'delta', run_id: 'run-1', seq: -1, text: 'negative sequence' },
    { type: 'unknown', run_id: 'run-1', seq: 1 },
  ])('rejects missing or invalid business fields: %j', (event) => {
    expect(() => parseResearchEvent({ event: event.type, data: JSON.stringify(event) })).toThrow(ApiError)
  })

  it('rejects malformed JSON and an SSE event/payload type mismatch', () => {
    expect(() => parseResearchEvent({ event: 'delta', data: '{broken' })).toThrow(ApiError)
    expect(() => parseResearchEvent({ event: 'done', data: JSON.stringify(start) })).toThrow(ApiError)
  })

  it.each(['__proto__', 'constructor', 'prototype', '1', 'R0-1', 'R1-0', 'R1-1 extra'])(
    'rejects a non-contract citation key before it can reach a store: %s', (id) => {
      const event = { type: 'citations', run_id: 'run-1', seq: 1,
        citations: { [id]: { source: 'unsafe.md' } } }
      expect(() => parseResearchEvent({ event: 'citations', data: JSON.stringify(event) }))
        .toThrow(ApiError)
    },
  )

  it('rejects mismatching embedded citation IDs', () => {
    const event = { type: 'citations', run_id: 'run-1', seq: 1,
      citations: { 'R1-1': { source: 'report.md', citation_id: 'R2-1' } } }
    expect(() => parseResearchEvent({ event: 'citations', data: JSON.stringify(event) }))
      .toThrow(ApiError)
  })
})

describe('POST research SSE', () => {
  it('awaits ordered callbacks, captures the run, and resolves only on explicit success', async () => {
    vi.stubEnv('VITE_API_BASE_URL', '')
    const events = [start,
      { type: 'step', run_id: 'run-1', seq: 1, step: 'search', status: 'running' },
      { type: 'citations', run_id: 'run-1', seq: 2, citations: { 'R1-1': { source: '报告.md' } } },
      { type: 'delta', run_id: 'run-1', seq: 3, text: '资料不足，不能下结论。' },
      { type: 'draft_ready', run_id: 'run-1', seq: 4, draft_id: 'draft-1', revision: 1 }, done]
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(responseFor(events.map((event) => frame(event)).join(''), 1))
    const seen: ResearchEvent[] = []
    const result = await streamResearch({
      path: '/api/agent/research/stream', body: { question: '分析' }, fetcher,
      onEvent: async (event) => { await Promise.resolve(); seen.push(event) },
    })
    expect(seen.map((event) => event.type)).toEqual(events.map((event) => event.type))
    expect(result).toEqual({ status: 'success', runId: 'run-1', lastSeq: 9, draftId: 'draft-1' })
    expect(fetcher).toHaveBeenCalledTimes(1)
    const [url, init] = fetcher.mock.calls[0] ?? []
    expect(url).toBe('/api/agent/research/stream')
    expect(init).toMatchObject({ method: 'POST', body: '{"question":"分析"}' })
  })

  it('accepts multiline JSON and CRLF framing in the full stream', async () => {
    const multiline = 'event: delta\r\ndata: {"type":"delta","run_id":"run-1",\r\ndata: "seq":1,"text":"中文"}\r\n\r\n'
    await expect(runText(frame(start, '\r\n') + multiline + frame(done, '\r\n'))).resolves.toMatchObject({
      status: 'success', runId: 'run-1',
    })
  })

  it('rejects a run different from the request expectation', async () => {
    await expect(runText(frame(start) + frame(done), { runId: 'other' })).rejects.toMatchObject({
      code: 'RUN_ID_MISMATCH',
    })
  })

  it('rejects mixed runs without calling the callback for the invalid event', async () => {
    const onEvent = vi.fn()
    const wrong = { type: 'delta', run_id: 'other', seq: 1, text: 'wrong run' }
    await expect(runText(frame(start) + frame(wrong) + frame(done), { onEvent })).rejects.toMatchObject({
      code: 'RUN_ID_MISMATCH',
    })
    expect(onEvent).toHaveBeenCalledTimes(1)
  })

  it('does not dispatch prototype-shaped citation maps to the consumer', async () => {
    const onEvent = vi.fn()
    const poisoned = { type: 'citations', run_id: 'run-1', seq: 1,
      citations: { ['__proto__']: { source: 'unsafe.md' } } }
    await expect(runText(frame(start) + frame(poisoned) + frame(done), { onEvent }))
      .rejects.toMatchObject({ code: 'INVALID_CITATIONS_EVENT' })
    expect(onEvent).toHaveBeenCalledTimes(1)
  })

  it.each([0, -1])('rejects duplicate or old sequences (%s)', async (seq) => {
    const stale = { type: 'delta', run_id: 'run-1', seq, text: 'stale' }
    await expect(runText(frame(start) + frame(stale) + frame(done))).rejects.toBeInstanceOf(ApiError)
  })

  it('rejects decreasing positive sequence numbers', async () => {
    const newer = { type: 'delta', run_id: 'run-1', seq: 2, text: 'new' }
    const older = { ...newer, seq: 1, text: 'old' }
    await expect(runText(frame(start) + frame(newer) + frame(older))).rejects.toMatchObject({
      code: 'NON_MONOTONIC_SEQUENCE',
    })
  })

  it('fails closed on malformed JSON in the stream', async () => {
    await expect(runText(frame(start) + 'event: delta\ndata: {broken}\n\n' + frame(done)))
      .rejects.toMatchObject({ code: 'INVALID_EVENT_JSON' })
  })

  it('rejects duplicate run_started, an absent start, and business events after done', async () => {
    await expect(runText(frame(start) + frame({ ...start, seq: 1 }))).rejects.toMatchObject({
      code: 'DUPLICATE_RUN_STARTED',
    })
    await expect(runText(frame(done))).rejects.toMatchObject({ code: 'MISSING_RUN_STARTED' })
    await expect(runText(frame(start) + frame(done) + frame({ ...done, seq: 10 }))).rejects.toMatchObject({
      code: 'EVENT_AFTER_DONE',
    })
  })

  it('does not treat EOF or an unclosed done frame as a successful completion', async () => {
    await expect(runText(frame(start))).rejects.toMatchObject({ code: 'STREAM_ENDED_WITHOUT_DONE' })
    await expect(runText(frame(start) + frame(done).slice(0, -1))).rejects.toMatchObject({
      code: 'INCOMPLETE_SSE_FRAME',
    })
  })

  it('rejects backend error/failed done, even after a draft_ready event', async () => {
    const draft = { type: 'draft_ready', run_id: 'run-1', seq: 1, draft_id: 'draft-1' }
    const backendError = { type: 'error', run_id: 'run-1', seq: 2, code: 'INTERNAL', message: 'private token' }
    const error = await runText(frame(start) + frame(draft) + frame(backendError)).catch((value: unknown) => value)
    expect(error).toMatchObject({ kind: 'backend', code: 'RESEARCH_FAILED' })
    expect(String(error)).not.toContain('private token')
    await expect(runText(frame(start) + frame({ ...done, status: 'failed' }))).rejects.toMatchObject({
      kind: 'backend',
    })
  })

  it('rejects duplicate draft events and an exceeded event/frame budget', async () => {
    const draft = { type: 'draft_ready', run_id: 'run-1', seq: 1, draft_id: 'draft-1' }
    await expect(runText(frame(start) + frame(draft) + frame({ ...draft, seq: 2 }))).rejects.toMatchObject({
      code: 'DUPLICATE_DRAFT_READY',
    })
    await expect(runText(frame(start) + frame(done), { maxEvents: 1 })).rejects.toMatchObject({
      code: 'TOO_MANY_EVENTS',
    })
    await expect(runText(frame(start), { maxFrameChars: 8 })).rejects.toMatchObject({ code: 'FRAME_TOO_LARGE' })
  })

  it('safely rejects HTTP, network and wrong content-type errors without retries', async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response('secret', { status: 503 }))
    await expect(runText('', { fetcher })).rejects.toMatchObject({ kind: 'http', status: 503 })
    expect(fetcher).toHaveBeenCalledTimes(1)
    const offline = vi.fn<typeof fetch>().mockRejectedValue(new Error('private endpoint'))
    const error = await runText('', { fetcher: offline }).catch((value: unknown) => value)
    expect(error).toMatchObject({ kind: 'network' })
    expect(String(error)).not.toContain('private endpoint')
    const wrongType = vi.fn<typeof fetch>().mockResolvedValue(new Response('{}'))
    await expect(runText('', { fetcher: wrongType })).rejects.toMatchObject({ code: 'INVALID_STREAM_CONTENT_TYPE' })
  })

  it('stops a terminal stream without waiting for the server to close the connection', async () => {
    const cancel = vi.fn()
    const response = new Response(new ReadableStream<Uint8Array>({
      start(controller) { controller.enqueue(encoder.encode(frame(start) + frame(done))) },
      cancel,
    }), { headers: { 'Content-Type': 'text/event-stream' } })
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(response)
    await expect(runText('', { fetcher })).resolves.toMatchObject({ status: 'success' })
    expect(cancel).toHaveBeenCalledTimes(1)
  })

  it('honors pre-abort and abort while waiting for another reader chunk', async () => {
    const preAborted = new AbortController()
    preAborted.abort()
    const unusedFetcher = vi.fn<typeof fetch>()
    await expect(runText('', { signal: preAborted.signal, fetcher: unusedFetcher })).rejects.toMatchObject({
      kind: 'aborted',
    })
    expect(unusedFetcher).not.toHaveBeenCalled()

    const controller = new AbortController()
    const cancel = vi.fn()
    const response = new Response(new ReadableStream<Uint8Array>({
      start(streamController) { streamController.enqueue(encoder.encode(frame(start))) },
      cancel,
    }), { headers: { 'Content-Type': 'text/event-stream' } })
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(response)
    // Timer runs after the callback/microtask chain and while reader.read() is pending.
    const promise = runText('', { fetcher, signal: controller.signal,
      onEvent: () => { setTimeout(() => controller.abort(), 0) } })
    await expect(promise).rejects.toMatchObject({ kind: 'aborted', code: 'REQUEST_ABORTED' })
    expect(cancel).toHaveBeenCalledTimes(1)
  })

  it('does not complete a draft if the reader connection fails', async () => {
    const draft = { type: 'draft_ready', run_id: 'run-1', seq: 1, draft_id: 'draft-1' }
    let pulls = 0
    const response = new Response(new ReadableStream<Uint8Array>({
      pull(controller) {
        if (++pulls === 1) controller.enqueue(encoder.encode(frame(start) + frame(draft)))
        else controller.error(new Error('private network details'))
      },
    }), { headers: { 'Content-Type': 'text/event-stream' } })
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(response)
    await expect(runText('', { fetcher })).rejects.toMatchObject({
      kind: 'network', code: 'STREAM_NETWORK_ERROR',
    })
  })

  it('does not report success when the consumer callback fails', async () => {
    await expect(runText(frame(start) + frame(done), { onEvent: () => { throw new Error('store bug') } }))
      .rejects.toMatchObject({ code: 'EVENT_CALLBACK_FAILED' })
  })
})
