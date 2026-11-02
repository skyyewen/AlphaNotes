import type { Evidence, SourceItem } from './types';

// Presentation fixtures only: not company disclosures, user strategies, or tool execution.
export const DEMO_SOURCES: SourceItem[] = [
  { id: 'demo-document', title: '公司资料 · 示例片段.md', kind: 'fundamental', note: '示例资料 / 不含真实财务数据', citation_id: 'R1-1' },
  { id: 'demo-strategy', title: '策略检查 · 示例规则.md', kind: 'strategy', note: '示例规则 / 不代表已采用策略', citation_id: 'R2-1' },
];

export const DEMO_EVIDENCE: Record<string, Evidence> = {
  'R1-1': {
    citation_id: 'R1-1', source: '公司资料 · 示例片段.md', heading_path: '经营描述与证据性质',
    excerpt: '【界面示例】经营描述应与具体报告期对应。管理层的未来预期需要与已发生事实区分，并结合后续披露核对。',
    source_type: 'document', version: '示例版本', available_at: '未接入真实披露时间',
  },
  'R2-1': {
    citation_id: 'R2-1', source: '策略检查 · 示例规则.md', heading_path: '研究前的核对条件',
    excerpt: '【界面示例】先明确策略的适用对象、观察周期与失效条件。缺少数据或未定义的条件，保留为无法判断。',
    source_type: 'wiki', version: '仅作展示', available_at: '未读取 gupiao Wiki',
  },
};

export const DEMO_ANSWER = `一、资料中的事实与观点
这份示例用于展示研究结构：先定位原文，再区分已发生事实、管理层预期与研究者推断。[R1-1]

二、与规则对照
检查适用对象、观察周期和失效条件。真实规则还需要从 gupiao Wiki 中选择，不能用原型中的阈值代替。[R2-1]

三、暂时无法判断的部分
目前没有真实标的、行情快照或财报指标。因此不能判断策略条件是否满足，也不会生成买卖建议。

四、下一步
准备真实资料和规则版本，再接入后端研究接口。所有示例只在当前页面内存中展示，不会被当作已归档研究。`;
