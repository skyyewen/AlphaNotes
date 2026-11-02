import { describe, expect, it } from 'vitest';
import { createSSRApp } from 'vue';
import { renderToString } from 'vue/server-renderer';
import MarkdownContent from './MarkdownContent.vue';

const render = (content: string, citationIds = ['R1-1']) => renderToString(createSSRApp(MarkdownContent, { content, citationIds }));

describe('Markdown report rendering', () => {
  it('renders report structure including lists, emphasis and tables with citations', async () => {
    const html = await render('# 研究结论\n\n**已有证据**\n\n- 第一项\n- 第二项\n\n| 条件 | 证据 |\n| --- | --- |\n| 经营 | [R1-1] |');
    expect(html).toContain('<h1>研究结论</h1>');
    expect(html).toContain('<strong>已有证据</strong>');
    expect(html).toContain('<ul>');
    expect(html).toContain('<li>第一项</li>');
    expect(html).toContain('class="markdown-table"');
    expect(html).toContain('<table>');
    expect(html).toContain('aria-label="查看引用 R1-1"');
  });

  it('keeps unknown citations, code and link labels as text', async () => {
    const html = await render('[R9-9]\n\n`[R1-1]`\n\n```text\n[R1-1]\n```\n\n[来源 [R1-1]](https://example.com)');
    expect(html).toContain('[R9-9]');
    expect(html).toContain('<code>[R1-1]</code>');
    expect(html).toContain('<pre><code>[R1-1]\n</code></pre>');
    expect(html).not.toContain('<button');
    expect(html).toContain('rel="noopener noreferrer"');
  });

  it('escapes source HTML, rejects dangerous links and does not load remote images', async () => {
    const html = await render('<script>alert(1)</script>\n\n<img src=x onerror=alert(1)>\n\n[点击](javascript:alert%281%29)\n\n![图](https://example.com/a.png)');
    expect(html).toContain('&lt;script&gt;');
    expect(html).not.toMatch(/<(script|img)\b/);
    expect(html).not.toContain('href="javascript:');
  });

  it('only enables citations belonging to the displayed snapshot', async () => {
    const html = await render('[R1-1] [R2-1]', ['R2-1']);
    expect(html).not.toContain('aria-label="查看引用 R1-1"');
    expect(html).toContain('aria-label="查看引用 R2-1"');
  });
});
