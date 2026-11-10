import { useCallback, useEffect, useRef, useState } from 'react';
import { getRun, listRuns } from '../api/client.js';
import { formatTime, STATUS_LABELS } from '../lib/messages.js';
import { Icon } from '../Icons.jsx';

export function RunHistory({ threadId, userId }) {
  const [runs, setRuns] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [detail, setDetail] = useState(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const detailVersion = useRef(0);
  const load = useCallback(async (signal) => {
    setLoading(true); setError('');
    try { setRuns(await listRuns(threadId, userId, signal)); }
    catch (error) { if (error.name !== 'AbortError') setError(error.message); }
    finally { if (!signal?.aborted) setLoading(false); }
  }, [threadId, userId]);
  useEffect(() => { const controller = new AbortController(); void load(controller.signal); return () => { controller.abort(); detailVersion.current++; }; }, [load]);
  async function select(runId) {
    const version = ++detailVersion.current;
    setDetailLoading(true); setError(''); setDetail(null);
    try { const run = await getRun(runId, userId); if (version === detailVersion.current) setDetail(run); }
    catch (error) { if (version === detailVersion.current) setError(error.message); }
    finally { if (version === detailVersion.current) setDetailLoading(false); }
  }
  return <div className="run-history">
    <div className="run-history-heading"><p>当前会话最近 50 次运行</p><button className="text-button" disabled={loading} onClick={() => void load()}><Icon name="refresh" />刷新记录</button></div>
    {error && <p className="error-text" role="alert">{error}</p>}
    {loading ? <p className="empty-state">正在读取任务记录…</p> : !runs.length ? <p className="empty-state">还没有任务。发送第一条消息后，记录会显示在这里。</p> : <div className="run-list">{runs.map((run, index) => <button key={run.run_id} className={`run-row${detail?.run_id === run.run_id ? ' selected' : ''}`} onClick={() => void select(run.run_id)}><span><strong>第 {runs.length - index} 次运行</strong><small>{formatTime(run.created_at)}</small></span><span className={`status-badge ${run.status}`}>{STATUS_LABELS[run.status] || run.status}</span><Icon name="arrow" /></button>)}</div>}
    {detailLoading && <p className="empty-state">正在读取运行详情…</p>}
    {detail && <section className="run-detail" aria-label="运行详情"><h3>运行详情</h3><dl><dt>状态</dt><dd>{STATUS_LABELS[detail.status] || detail.status}</dd><dt>创建时间</dt><dd>{formatTime(detail.created_at)}</dd><dt>更新时间</dt><dd>{formatTime(detail.updated_at)}</dd><dt>运行 ID</dt><dd className="identifier">{detail.run_id}</dd><dt>会话 ID</dt><dd className="identifier">{detail.thread_id}</dd></dl>{detail.error && <p className="error-text">{detail.error}</p>}</section>}
  </div>;
}
