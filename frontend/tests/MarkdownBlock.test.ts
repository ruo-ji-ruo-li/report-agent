// tests/MarkdownBlock.test.ts
// @vitest-environment happy-dom
import { describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import MarkdownBlock from '../src/components/MarkdownBlock.vue'

describe('MarkdownBlock(spec-f §8.5)', () => {
  it('渲染加粗与列表', () => {
    const w = mount(MarkdownBlock, { props: { text: '**加粗**\n- 条目' } })
    expect(w.html()).toContain('<strong>加粗</strong>')
    expect(w.html()).toContain('<li>条目</li>')
  })

  it('消毒 script 与事件属性(LLM 输出不可信)', () => {
    const w = mount(MarkdownBlock, { props: { text: '<script>alert(1)</script><a onclick="x()">链接</a>' } })
    // html:false 将原始标签整体转义为文本,不会生成真实 script 元素或 onclick 属性
    expect(w.find('script').exists()).toBe(false)
    expect(w.find('[onclick]').exists()).toBe(false)
    expect(w.html()).not.toContain('<script>')
  })

  it('空文本渲染空容器', () => {
    const w = mount(MarkdownBlock, { props: { text: '' } })
    expect(w.find('.md').exists()).toBe(true)
  })

  it('流式更新 80ms 窗内至少渲染一次,不饿死到流末', async () => {
    vi.useFakeTimers()
    const w = mount(MarkdownBlock, { props: { text: '' } })
    await w.setProps({ text: 'a' })
    await vi.advanceTimersByTimeAsync(20)
    await w.setProps({ text: 'ab' })
    await vi.advanceTimersByTimeAsync(20)
    await w.setProps({ text: 'abc' })
    await vi.advanceTimersByTimeAsync(60) // 距首改 100ms > 80ms 窗
    expect(w.find('.md').html()).toContain('abc')
    vi.useRealTimers()
  })
})
