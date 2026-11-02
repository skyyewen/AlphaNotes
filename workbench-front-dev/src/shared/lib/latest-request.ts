export interface LatestRequestToken {
  readonly generation: number
  readonly requestId: string
}

/** Guards UI writes; invalidation does not cancel any backend work. */
export function createLatestRequestGate() {
  let generation = 0
  let current: LatestRequestToken | null = null

  return {
    begin(requestId = ''): LatestRequestToken {
      current = Object.freeze({ generation: ++generation, requestId })
      return current
    },
    isCurrent(token: LatestRequestToken): boolean {
      return current !== null
        && token.generation === current.generation
        && token.requestId === current.requestId
    },
    invalidate(): void {
      generation += 1
      current = null
    },
  }
}
