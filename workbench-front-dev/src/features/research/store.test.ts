import { beforeEach, describe, expect, it } from 'vitest';
import { createPinia, setActivePinia } from 'pinia';
import { useResearchStore } from './store';
import { parseResearchResult } from './api';
import { splitAnswerCitations, type ResearchResult } from './types';

function result(patch: Partial<ResearchResult> = {}): ResearchResult {
  return {
    run_id: 'run-A', status: 'success', question: '核对证据',
    context: { symbol: '601318.SH', materials_cutoff_date: '2026-10-26', market_cutoff_date: '2026-10-13', strategy_id: 'weekly_platform', strategy_version: 'X2' },
    started_at: '2026-10-27T00:00:00+00:00', finished_at: '2026-10-27T00:00:01+00:00', archive_status: 'completed', confirmed_at: null,
    answer: '证据[R1-1]，规则[R2-1]，数据[R3-1]。',
    citations: {
      'R1-1': { source: 'report.md', excerpt: '原文事实', available_date: '2026-04-08', ingested_at: '2026-10-26T00:00:00+00:00', source_type: 'disclosure' },
      'R2-1': { source: 'wiki/rule.md', excerpt: '规则原文', source_kind: 'wiki', version: 'X2' },
      'R3-1': { source: 'daily.parquet', excerpt: '{"close":1}', source_kind: 'market', adjustment: 'none', actual_range: { end_date: '2026-10-13' } },
    },
    tool_snapshots: [{ tool: 'query_stock_data', result: { snapshot: { adjustment: 'none', data: [{ close: 1 }], actual_range: { end_date: '2026-10-13' } } } }],
    steps: [{ step: 'model', status: 'running' }, { step: 'model', status: 'completed' }, { step: 'tool', tool: 'search_fundamentals', status: 'running' }, { step: 'tool', tool: 'search_fundamentals', status: 'completed' }, { step: 'model', status: 'running' }, { step: 'model', status: 'completed' }],
    ...patch,
  };
}

describe('synchronous research and immutable history view', () => {
  beforeEach(() => setActivePinia(createPinia()));

  it('does not archive presentation fixtures', () => {
    const store = useResearchStore(); store.previewDemo();
    expect(store.viewMode).toBe('demo'); expect(store.canConfirm).toBe(false); expect(store.runId).toBeNull();
  });

  it('opens confirmation only after a complete successful persisted response', () => {
    const store = useResearchStore(); const ticket = store.beginResearch();
    expect(store.canConfirm).toBe(false);
    expect(store.acceptResult(result(), ticket)).toBe(true);
    expect(store.canConfirm).toBe(true);
    expect(store.markSaving()).toBe(true); expect(store.canConfirm).toBe(false);
    expect(store.markSaved('wrong-run')).toBe(false);
    expect(store.markSaved('run-A', '2026-10-27T00:01:00Z')).toBe(true);
    expect(store.status).toBe('saved'); expect(store.canConfirm).toBe(false); expect(store.confirmedAt).toContain('00:01');
  });

  it('renders a legacy unpersisted result without enabling confirmation', () => {
    const store = useResearchStore(); store.acceptResult(result({ archive_status: undefined }), store.beginResearch());
    expect(store.answer).toContain('证据'); expect(store.status).toBe('completed'); expect(store.canConfirm).toBe(false);
  });

  it('rejects failed research and preserves no archivable result', () => {
    const store = useResearchStore(); const ticket = store.beginResearch();
    expect(store.acceptResult(result({ status: 'failed', archive_status: 'failed' }), ticket)).toBe(false);
    expect(store.canConfirm).toBe(false); expect(store.answer).toBe('');
  });

  it('ignores a late response after opening a new context or preview', () => {
    const store = useResearchStore(); const old = store.beginResearch();
    store.resetResearch(); store.context.symbol = '600519.SH';
    const current = store.beginResearch();
    expect(store.acceptResult(result(), old)).toBe(false); expect(store.runId).toBeNull();
    store.previewDemo(); const demo = store.answer;
    expect(store.acceptResult(result(), current)).toBe(false); expect(store.answer).toBe(demo);
  });

  it('freezes inputs during a research and does not rewrite them from direct form mutation', () => {
    const store = useResearchStore(); store.context.symbol = '601318'; store.beginResearch();
    expect(store.updateContext({ symbol: '600519' })).toBe(false);
    store.context.symbol = 'external'; expect(store.frozenContext?.symbol).toBe('601318');
  });

  it('hydrates independent dates, evidence kinds and the actual market snapshot', () => {
    const store = useResearchStore(); store.acceptResult(result(), store.beginResearch());
    expect(store.citations['R1-1'].available_date).toBe('2026-04-08');
    expect(store.citations['R1-1'].ingested_at).toContain('2026-10-26');
    expect(store.citations['R2-1'].source_type).toBe('wiki'); expect(store.citations['R2-1'].version).toBe('X2');
    expect(store.citations['R3-1'].source_type).toBe('market'); expect(store.marketSnapshot?.adjustment).toBe('none');
    expect(store.context.materials_cutoff_date).toBe('2026-10-26'); expect(store.context.market_cutoff_date).toBe('2026-10-13');
    expect(store.steps.map(item => item.id)).toEqual(['model_1', 'tool_1', 'model_2']);
  });

  it('replays an archived snapshot as read-only and ignores a replaced history request', () => {
    const store = useResearchStore(); const old = store.beginHistory(); const current = store.beginHistory();
    const snapshot = result({ archive_status: 'archived', confirmed_at: '2026-10-27T01:00:00Z' });
    expect(store.acceptHistory(snapshot, old)).toBe(false);
    expect(store.acceptHistory(snapshot, current)).toBe(true);
    expect(store.viewMode).toBe('history'); expect(store.canConfirm).toBe(false); expect(store.recordId).toBe('run-A');
    expect(store.context.strategy_version).toBe('X2'); expect(store.answer).toBe(snapshot.answer);
    store.resetResearch(); expect(store.canConfirm).toBe(false); expect(store.viewMode).toBe('live');
  });

  it('a stale confirmation cannot update another view through saving state', () => {
    const store = useResearchStore(); store.acceptResult(result(), store.beginResearch()); store.markSaving();
    store.previewDemo(); expect(store.markSaved('run-A')).toBe(false); expect(store.viewMode).toBe('demo');
  });

  it('splits reference tokens while preserving model content as plain text', () => {
    const segments = splitAnswerCitations('<script>alert(1)</script> [R1-1] [R2-1]');
    expect(segments.filter(segment => segment.kind === 'citation').map(segment => segment.value)).toEqual(['R1-1', 'R2-1']);
    expect(segments[0].value).toContain('<script>');
  });
});

describe('research response contract', () => {
  it('requires actual snapshot fields before a success can be used', () => {
    expect(parseResearchResult(result()).run_id).toBe('run-A');
    expect(() => parseResearchResult({ status: 'success', run_id: 'x' })).toThrow();
    expect(() => parseResearchResult(result({ citations: { 'R1-1': { source: 'x', excerpt: 'text', citation_id: 'R2-1' } } }))).toThrow();
    expect(() => parseResearchResult(result({ finished_at: null }))).toThrow();
    expect(() => parseResearchResult(result({ archive_status: 'failed' }))).toThrow();
    expect(() => parseResearchResult({ ...result(), steps: [null] })).toThrow();
  });
});
