import { useEffect, useId, useRef, useState } from 'react';
import { getThreadTokenUsage } from '../api/client.js';
import { Icon } from '../Icons.jsx';

const EMPTY_USAGE = {
  total_input_tokens: 0, total_output_tokens: 0, total_tokens: 0,
  llm_call_count: 0, total_runs: 0,
};
const formatCount = value => typeof value === 'number' ? value.toLocaleString('zh-CN') : '—';

export function TokenUsageIndicator({ threadId, userId, draft, busy, active = true, onShowRuns }) {
  const [open, setOpen] = useState(false);
  const [usage, setUsage] = useState(null);
  const [loading, setLoading] = useState(!draft);
  const [error, setError] = useState('');
  const [refreshVersion, setRefreshVersion] = useState(0);
  const root = useRef(null);
  const trigger = useRef(null);
  const panelId = useId();

  useEffect(() => {
    if (!active) return;
    if (draft) { setUsage(EMPTY_USAGE); setLoading(false); setError(''); return; }
    const controller = new AbortController();
    async function load() {
      setLoading(true); setError('');
      try {
        const result = await getThreadTokenUsage(threadId, userId, controller.signal);
        if (!controller.signal.aborted) setUsage(result);
      } catch (failure) {
        if (!controller.signal.aborted) setError(failure.message);
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    }
    void load();
    return () => controller.abort();
    // busy 变化时重查：开始时读已有合计，结束后读本轮落库后的最新合计。
  }, [threadId, userId, draft, busy, active, refreshVersion]);

  useEffect(() => {
    if (!open) return;
    function outside(event) { if (!root.current?.contains(event.target)) setOpen(false); }
    function escape(event) {
      if (event.key === 'Escape') { setOpen(false); trigger.current?.focus(); }
    }
    document.addEventListener('pointerdown', outside);
    document.addEventListener('keydown', escape);
    return () => {
      document.removeEventListener('pointerdown', outside);
      document.removeEventListener('keydown', escape);
    };
  }, [open]);

  const input = usage?.total_input_tokens ?? 0;
  const output = usage?.total_output_tokens ?? 0;
  const inputShare = input + output > 0 ? input / (input + output) * 100 : 0;
  const total = formatCount(usage?.total_tokens);
  return <div className="token-usage" ref={root}>
    <button ref={trigger} type="button" className={`token-usage-trigger${open ? ' expanded' : ''}${error ? ' has-error' : ''}`}
      aria-expanded={open} aria-controls={panelId} aria-label={`查看当前会话 Token 用量，${error ? '更新失败' : loading && !usage ? '读取中' : `累计 ${total}`}`}
      onClick={() => setOpen(!open)}>
      <Icon name="coins" /><span>Token</span><strong>{loading && !usage ? '读取中' : total}</strong>
      {busy && <span className="token-running-dot" title="本次用量统计中" />}
      <Icon name="chevron-down" />
    </button>
    {open && <section id={panelId} className="token-usage-panel" aria-label="当前会话 Token 用量">
      <div className="token-panel-heading"><div><span className="token-eyebrow">用量概览</span><h2>当前会话</h2></div><span className="token-scope">累计消耗</span></div>
      <div className="token-total"><span>总 Token</span><strong>{total}</strong><small>{busy ? '本次统计中 · 运行结束后更新' : loading ? '正在更新用量…' : draft ? '发送第一条消息后开始统计' : '已结束运行的累计用量'}</small></div>
      <div className="token-breakdown">
        <div><span><i className="token-key input" />输入 Token</span><strong>{formatCount(usage?.total_input_tokens)}</strong></div>
        <div><span><i className="token-key output" />输出 Token</span><strong>{formatCount(usage?.total_output_tokens)}</strong></div>
      </div>
      <div className="token-distribution" aria-hidden="true"><span className="input" style={{ width: `${inputShare}%` }} /><span className="output" style={{ width: `${input + output ? 100 - inputShare : 0}%` }} /></div>
      <dl className="token-counts"><div><dt title="仅统计返回了有效用量的模型调用">模型调用</dt><dd>{formatCount(usage?.llm_call_count)}<small> 次</small></dd></div><div><dt>已结束运行</dt><dd>{formatCount(usage?.total_runs)}<small> 次</small></dd></div></dl>
      {error && <p className="token-error" role="alert">{usage ? '用量更新失败，当前显示上次结果。' : '暂时无法读取用量。'}<span>{error}</span></p>}
      <p className="token-note">包含失败、取消前已记录的消耗。Token 数量不代表金额。</p>
      <div className="token-panel-footer"><button type="button" className="text-button" disabled={draft} title={draft ? '发送第一条消息后可查看运行用量' : undefined} onClick={() => { setOpen(false); onShowRuns(); }}>查看每次运行<Icon name="arrow" /></button><button type="button" className="text-button" disabled={loading || draft} onClick={() => setRefreshVersion(value => value + 1)}><Icon name="refresh" className={loading ? 'is-spinning' : ''} />{loading ? '更新中' : error ? '重试' : '刷新'}</button></div>
    </section>}
  </div>;
}
