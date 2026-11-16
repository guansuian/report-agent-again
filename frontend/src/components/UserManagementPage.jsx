import { useEffect, useRef, useState } from 'react';
import { Alert, Avatar, Button, Card, ConfigProvider, Empty, Listy, Skeleton } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import companyLogo from '../assets/company-logo.svg';
import { Icon } from '../Icons.jsx';
import { listMemories } from '../api/client.js';
import { formatTime } from '../lib/messages.js';

export function UserManagementPage({ userId, onBack, onLogout }) {
  const [memories, setMemories] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [refreshVersion, setRefreshVersion] = useState(0);
  const titleRef = useRef(null);

  useEffect(() => { titleRef.current?.focus(); }, []);

  useEffect(() => {
    const controller = new AbortController();

    async function load() {
      setLoading(true);
      setError(null);
      setMemories(null);
      try {
        const result = await listMemories(userId, controller.signal);
        if (controller.signal.aborted) return;
        if (!Array.isArray(result?.results)) {
          throw new Error('记忆列表接口返回格式不正确。');
        }
        setMemories(result.results);
      } catch (error) {
        if (!controller.signal.aborted) setError(error);
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    }

    void load();
    return () => controller.abort();
  }, [userId, refreshVersion]);

  return <ConfigProvider locale={zhCN} theme={{ token: { colorPrimary: '#6172bc', borderRadius: 10 } }}>
    <div className="user-management-page">
      <header className="user-management-topbar">
        <a href="#" onClick={event => { event.preventDefault(); onBack(); }} aria-label="返回报表对话"><img src={companyLogo} alt="银河星尘" width="136" height="30" /></a>
        <div className="user-management-account"><Avatar icon={<Icon name="user" />} /><span className="current-user">{userId}</span><Button type="text" onClick={onLogout}>退出登录</Button></div>
      </header>

      <main className="user-management-content">
        <Button type="text" icon={<Icon name="arrow-left" />} onClick={onBack}>返回聊天</Button>
        <div className="user-management-heading"><h1 ref={titleRef} tabIndex={-1}>用户管理</h1><p>查看 AI 已保存的长期偏好</p></div>

        <Card className="user-management-profile">
          <div className="user-management-identity"><Avatar size={46} icon={<Icon name="user" />} /><div><strong>{userId}</strong><p>当前登录用户</p></div></div>
        </Card>

        <section className="user-management-memories" aria-labelledby="user-memory-title" aria-busy={loading}>
          <div className="user-memory-heading"><h2 id="user-memory-title">长期记忆</h2><div><span aria-live="polite">{memories ? `${memories.length} 条记忆` : loading ? '正在读取' : '读取失败'}</span><Button icon={<Icon name="refresh" />} loading={loading} onClick={() => setRefreshVersion(value => value + 1)}>刷新</Button></div></div>

          {error ? <Alert
            type="error" showIcon
            title="长期记忆加载失败"
            description={error.status === 404 ? '未找到记忆列表接口，请确认后端已接入该接口并重启服务。' : error.message}
          /> : <Card className="user-memory-list">
            {loading ? <div role="status" aria-label="正在读取长期记忆"><Skeleton active paragraph={{ rows: 4 }} /></div> : memories?.length ? <Listy
              items={memories} rowKey="id" rowHoverable={false}
              classNames={{ item: 'user-memory-item' }}
              itemRender={memory => <div className="user-memory-entry">
                <span className="user-memory-icon"><Icon name="bookmark" /></span>
                <div><p className="user-memory-text">{memory.memory}</p>
                  {memory.updated_at ? <span className="user-memory-time">更新于 {formatTime(memory.updated_at)}</span> : memory.created_at ? <span className="user-memory-time">记录于 {formatTime(memory.created_at)}</span> : null}
                </div>
              </div>}
            /> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="还没有保存的长期记忆"><p className="user-memory-empty-note">在聊天中告诉助手你的长期偏好，例如“以后回答请先给结论”。</p></Empty>}
          </Card>}
          {!error && !loading && Boolean(memories?.length) && <p className="user-memory-note">这些偏好可供新的会话参考。</p>}
        </section>
      </main>
    </div>
  </ConfigProvider>;
}
