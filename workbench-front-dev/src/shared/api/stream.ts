import { abortedError, ApiError, httpError } from './errors'
import { resolveApiUrl } from './request'

export interface SseFrame {
  event: string
  data: string
  id?: string
}

function protocolError(code: string): ApiError {
  return new ApiError('研究事件流不符合约定，已停止接收；本次结果不可视为已完成。', {
    kind: 'protocol',
    code,
  })
}

/** Keeps partial UTF-8 code points between byte chunks; malformed bytes fail closed. */
export class Utf8StreamDecoder {
  private readonly decoder = new TextDecoder('utf-8', { fatal: true })

  push(bytes: Uint8Array): string {
    try {
      return this.decoder.decode(bytes, { stream: true })
    } catch {
      throw protocolError('INVALID_UTF8')
    }
  }

  finish(): string {
    try {
      return this.decoder.decode()
    } catch {
      throw protocolError('INCOMPLETE_UTF8')
    }
  }
}

/** Incremental SSE framing. Handles LF, CRLF (including split pairs), and CR. */
export class SseFrameParser {
  private line = ''
  private dataLines: string[] = []
  private eventName = 'message'
  private lastEventId: string | undefined
  private skipNextLf = false
  private frameChars = 0

  constructor(private readonly maxFrameChars = 65_536) {
    if (!Number.isSafeInteger(maxFrameChars) || maxFrameChars < 1) {
      throw protocolError('INVALID_FRAME_BUDGET')
    }
  }

  push(text: string): SseFrame[] {
    const frames: SseFrame[] = []
    for (const character of text) {
      if (this.skipNextLf) {
        this.skipNextLf = false
        if (character === '\n') continue
      }
      this.frameChars += character.length
      if (this.frameChars > this.maxFrameChars) throw protocolError('FRAME_TOO_LARGE')
      if (character === '\r' || character === '\n') {
        this.consumeLine(frames)
        this.skipNextLf = character === '\r'
      } else {
        this.line += character
      }
    }
    return frames
  }

  finish(): void {
    // An unclosed data frame must not turn into a final business event at EOF.
    if (this.line !== '' || this.dataLines.length > 0 || this.eventName !== 'message') {
      throw protocolError('INCOMPLETE_SSE_FRAME')
    }
  }

  private consumeLine(frames: SseFrame[]): void {
    const line = this.line
    this.line = ''
    if (line === '') {
      if (this.dataLines.length > 0) {
        frames.push({
          event: this.eventName,
          data: this.dataLines.join('\n'),
          ...(this.lastEventId === undefined ? {} : { id: this.lastEventId }),
        })
      }
      this.dataLines = []
      this.eventName = 'message'
      this.frameChars = 0
      return
    }
    if (line.startsWith(':')) return
    const colon = line.indexOf(':')
    const field = colon < 0 ? line : line.slice(0, colon)
    let value = colon < 0 ? '' : line.slice(colon + 1)
    if (value.startsWith(' ')) value = value.slice(1)
    if (field === 'data') this.dataLines.push(value)
    else if (field === 'event') this.eventName = value || 'message'
    else if (field === 'id' && !value.includes('\0')) this.lastEventId = value
    // retry/unknown fields are ignored: this client never reconnects automatically.
  }
}

interface ResearchEventBase {
  run_id: string
  seq: number
}

export interface ResearchCitation {
  source: string
  [key: string]: unknown
}

export type ResearchEvent = ResearchEventBase & (
  | { type: 'run_started' }
  | {
    type: 'step'
    step: string
    status: 'running' | 'completed' | 'failed'
    tool?: string
    message?: string
  }
  | { type: 'citations'; citations: Record<string, ResearchCitation> }
  | { type: 'delta'; text: string }
  | { type: 'draft_ready'; draft_id: string; revision?: string | number }
  | { type: 'error'; code: string; message?: string }
  | { type: 'done'; status: 'success' | 'failed' }
)

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function isText(value: unknown): value is string {
  return typeof value === 'string' && value.trim().length > 0
}

const CITATION_ID_PATTERN = /^R[1-9]\d*-[1-9]\d*$/

/** Checks the envelope and the fields used by the UI before invoking callbacks. */
export function parseResearchEvent(frame: SseFrame): ResearchEvent {
  let value: unknown
  try {
    value = JSON.parse(frame.data)
  } catch {
    throw protocolError('INVALID_EVENT_JSON')
  }
  if (!isObject(value) || !isText(value.type) || !isText(value.run_id)
    || value.run_id.length > 128 || !Number.isSafeInteger(value.seq)
    || (value.seq as number) < 0) throw protocolError('INVALID_EVENT_ENVELOPE')
  if (frame.event !== value.type) throw protocolError('EVENT_TYPE_MISMATCH')

  switch (value.type) {
    case 'run_started':
      break
    case 'step':
      if (!isText(value.step) || !['running', 'completed', 'failed'].includes(String(value.status))
        || (value.tool !== undefined && !isText(value.tool))
        || (value.message !== undefined && typeof value.message !== 'string')) {
        throw protocolError('INVALID_STEP_EVENT')
      }
      break
    case 'citations':
      if (!isObject(value.citations) || Object.entries(value.citations).some(
        ([id, citation]) => id.length > 64 || !CITATION_ID_PATTERN.test(id)
          || !isObject(citation) || !isText(citation.source)
          || (citation.citation_id !== undefined && citation.citation_id !== id),
      )) throw protocolError('INVALID_CITATIONS_EVENT')
      break
    case 'delta':
      if (typeof value.text !== 'string') throw protocolError('INVALID_DELTA_EVENT')
      break
    case 'draft_ready':
      if (!isText(value.draft_id)
        || (value.revision !== undefined && !isText(value.revision)
          && !(Number.isSafeInteger(value.revision) && (value.revision as number) >= 0))) {
        throw protocolError('INVALID_DRAFT_EVENT')
      }
      break
    case 'error':
      if (!isText(value.code) || (value.message !== undefined && typeof value.message !== 'string')) {
        throw protocolError('INVALID_ERROR_EVENT')
      }
      break
    case 'done':
      if (value.status !== 'success' && value.status !== 'failed') {
        throw protocolError('INVALID_DONE_EVENT')
      }
      break
    default:
      throw protocolError('UNKNOWN_EVENT_TYPE')
  }
  return value as unknown as ResearchEvent
}

export interface ResearchStreamOptions {
  path: string
  body: unknown
  /** If supplied, the server cannot silently switch to another research run. */
  runId?: string
  signal?: AbortSignal
  onEvent: (event: ResearchEvent) => void | Promise<void>
  fetcher?: typeof fetch
  maxFrameChars?: number
  maxEvents?: number
}

export interface ResearchStreamResult {
  status: 'success'
  runId: string
  lastSeq: number
  draftId?: string
}

function readWithSignal(
  reader: ReadableStreamDefaultReader<Uint8Array>,
  signal?: AbortSignal,
): Promise<ReadableStreamReadResult<Uint8Array>> {
  if (signal?.aborted) return Promise.reject(abortedError())
  return new Promise((resolve, reject) => {
    const onAbort = () => {
      signal?.removeEventListener('abort', onAbort)
      void reader.cancel().catch(() => undefined)
      reject(abortedError())
    }
    signal?.addEventListener('abort', onAbort, { once: true })
    reader.read().then(
      (result) => {
        signal?.removeEventListener('abort', onAbort)
        resolve(result)
      },
      (error: unknown) => {
        signal?.removeEventListener('abort', onAbort)
        reject(error)
      },
    )
  })
}

/**
 * One POST, no retries/reconnection, and no mock fallback.
 * Abort stops browser consumption only; it is not a backend cancellation protocol.
 * A draft_ready event is provisional until the promise resolves with success.
 */
export async function streamResearch(options: ResearchStreamOptions): Promise<ResearchStreamResult> {
  if (options.signal?.aborted) throw abortedError()
  const url = resolveApiUrl(options.path)
  const parser = new SseFrameParser(options.maxFrameChars)
  const decoder = new Utf8StreamDecoder()
  const maxEvents = options.maxEvents ?? 2_000
  if (!Number.isSafeInteger(maxEvents) || maxEvents < 1) throw protocolError('INVALID_EVENT_BUDGET')
  let body: string | undefined
  try {
    body = JSON.stringify(options.body)
  } catch {
    throw protocolError('INVALID_REQUEST_BODY')
  }
  if (body === undefined) throw protocolError('INVALID_REQUEST_BODY')

  let reader: ReadableStreamDefaultReader<Uint8Array> | undefined
  let runId = options.runId
  let lastSeq = -1
  let started = false
  let finished = false
  let draftId: string | undefined
  let count = 0

  const consume = async (frames: SseFrame[]): Promise<void> => {
    for (const frame of frames) {
      if (options.signal?.aborted) throw abortedError()
      if (finished) throw protocolError('EVENT_AFTER_DONE')
      if (++count > maxEvents) throw protocolError('TOO_MANY_EVENTS')
      const event = parseResearchEvent(frame)
      if (!started) {
        if (event.type !== 'run_started') throw protocolError('MISSING_RUN_STARTED')
        started = true
        runId ??= event.run_id
      } else if (event.type === 'run_started') {
        throw protocolError('DUPLICATE_RUN_STARTED')
      }
      if (event.run_id !== runId) throw protocolError('RUN_ID_MISMATCH')
      if (event.seq <= lastSeq) throw protocolError('NON_MONOTONIC_SEQUENCE')
      lastSeq = event.seq
      if (event.type === 'draft_ready') {
        if (draftId !== undefined) throw protocolError('DUPLICATE_DRAFT_READY')
        draftId = event.draft_id
      }
      try {
        await options.onEvent(event)
      } catch {
        if (options.signal?.aborted) throw abortedError()
        throw protocolError('EVENT_CALLBACK_FAILED')
      }
      if (options.signal?.aborted) throw abortedError()
      if (event.type === 'error' || (event.type === 'done' && event.status === 'failed')) {
        // Do not expose arbitrary server messages as user-facing errors.
        throw new ApiError('后端研究任务失败，本次结果未完成。', {
          kind: 'backend',
          code: 'RESEARCH_FAILED',
        })
      }
      if (event.type === 'done') finished = true
    }
  }

  try {
    const response = await (options.fetcher ?? globalThis.fetch)(url, {
      method: 'POST',
      headers: { Accept: 'text/event-stream', 'Content-Type': 'application/json' },
      body,
      signal: options.signal,
    })
    if (options.signal?.aborted) throw abortedError()
    if (!response.ok) throw httpError(response.status)
    if (!response.headers.get('Content-Type')?.toLowerCase().includes('text/event-stream')) {
      throw protocolError('INVALID_STREAM_CONTENT_TYPE')
    }
    if (!response.body) throw protocolError('MISSING_STREAM_BODY')
    reader = response.body.getReader()
    while (!finished) {
      const chunk = await readWithSignal(reader, options.signal)
      if (chunk.done) {
        await consume(parser.push(decoder.finish()))
        parser.finish()
        break
      }
      await consume(parser.push(decoder.push(chunk.value)))
    }
    if (!finished || !started || runId === undefined) throw protocolError('STREAM_ENDED_WITHOUT_DONE')
    // A terminal event ends browser consumption, even if the server keeps the connection open.
    parser.finish()
    decoder.finish()
    return { status: 'success', runId, lastSeq, ...(draftId === undefined ? {} : { draftId }) }
  } catch (error: unknown) {
    if (error instanceof ApiError) throw error
    if (options.signal?.aborted
      || (error instanceof Error && error.name === 'AbortError')) throw abortedError()
    throw new ApiError('研究连接已断开，本次结果未完成。', {
      kind: 'network',
      code: 'STREAM_NETWORK_ERROR',
    })
  } finally {
    if (reader) {
      await reader.cancel().catch(() => undefined)
      reader.releaseLock()
    }
  }
}
