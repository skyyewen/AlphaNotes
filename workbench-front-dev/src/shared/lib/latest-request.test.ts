import { describe, expect, it } from 'vitest'
import { createLatestRequestGate } from './latest-request'

describe('createLatestRequestGate', () => {
  it('only accepts the most recent request', () => {
    const gate = createLatestRequestGate()
    const old = gate.begin('first')
    const latest = gate.begin('second')
    expect(gate.isCurrent(old)).toBe(false)
    expect(gate.isCurrent(latest)).toBe(true)
  })

  it('rejects stale callbacks even if the request ID is reused', () => {
    const gate = createLatestRequestGate()
    const old = gate.begin('same-id')
    const latest = gate.begin('same-id')
    expect(gate.isCurrent(old)).toBe(false)
    expect(gate.isCurrent(latest)).toBe(true)
  })

  it('invalidates pending callbacks without claiming to cancel a backend', () => {
    const gate = createLatestRequestGate()
    const token = gate.begin()
    gate.invalidate()
    expect(gate.isCurrent(token)).toBe(false)
    expect(gate.isCurrent(gate.begin())).toBe(true)
  })
})
