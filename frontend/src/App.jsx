import { useCallback, useEffect, useRef, useState } from 'react';
import { Icon, IconSprite } from './Icons.jsx';
import companyLogo from './assets/company-logo.svg';
import { useChat } from './hooks/useChat.js';
import { groupMessagesForDisplay, isActive, STATUS_LABELS } from './lib/messages.js';
import { RunHistory } from './components/RunHistory.jsx';
import { ClarificationCard } from './components/ClarificationCard.jsx';
import { clarificationOf, clarificationState } from './lib/humanInput.js';
import { Login } from './components/Login.jsx';
import { loadUserId, saveUserId } from './storage.js';

function Avatar({ user = false }) {
  return user
    ? <div className="avatar user-avatar" aria-hidden="true"><span className="pig">🐷</span></div>
    : <div className="avatar robot-avatar" aria-hidden="true"><span className="robot-antenna" /><span className="robot-head" /></div>;
}

function InlineText({ text }) {
  return text.split(/(\*\*.+?\*\*|`[^`]+`)/g).map((part, index) => part.startsWith('**') && part.endsWith('**')
    ? <strong key={index}>{part.slice(2, -2)}</strong>
    : part.startsWith('`') && part.endsWith('`') ? <code key={index}>{part.slice(1, -1)}</code> : part);
}

// 用 React 渲染文字，消息里的 HTML 不会被当成代码执行。
function MessageText({ text }) {
  const elements = [];
  let items = [];
  function flushList() {
    if (items.length) elements.push(<ul key={`list-${elements.length}`}>{items}</ul>);
    items = [];
  }
  text.split('\n').forEach((line, index) => {
    if (line.startsWith('- ')) {
      const content = line.slice(2);
      const label = content.match(/^([^：]{1,8}：)(.*)$/);
      items.push(<li key={index}>{label ? <><strong>{label[1]}</strong><InlineText text={label[2]} /></> : <InlineText text={content} />}</li>);
    } else {
      flushList();
      if (/^#{1,4} /.test(line)) elements.push(<h3 key={index}><InlineText text={line.replace(/^#{1,4} /, '')} /></h3>);
      else if (line.trim()) elements.push(<p key={index}><InlineText text={line} /></p>);
    }
  });
  flushList();
  return elements;
}

function Header({ sidebarOpen, onMenu, onHome, onDialog, userId, onLogout }) {
  return <header className="topbar">
    <div className="brand">
      <button type="button" className="mobile-toggle" aria-label="展开侧边栏" aria-expanded={sidebarOpen} onClick={onMenu}><Icon name="menu" /></button>
      <a href="#" aria-label="银河星尘首页" onClick={event => { event.preventDefault(); onHome(); }}><img className="brand-logo" src={companyLogo} alt="银河星尘" width="136" height="30" /></a>
    </div>
    <nav className="account-bar" aria-label="账户导航">
      <button className="account-button" onClick={() => onDialog('messages')}><Icon name="mail" /><span>消息中心</span></button>
      <span className="divider" />
      <button className="account-button" onClick={() => onDialog('profile')} title={userId}><span className="profile-mini" aria-hidden="true">👩🏻‍🚀</span><span className="current-user">{userId}</span></button>
      <button className="account-button" onClick={onLogout}>退出登录</button>
    </nav>
  </header>;
}

function Sidebar({ chat, query, onQuery, onSelect, onNew, onManage, onOpen, onHelp }) {
  const sessions = chat.catalog.sessions.filter(session => session.title.toLowerCase().includes(query.trim().toLowerCase()));
  return <aside className="sidebar" aria-label="会话侧边栏">
    <div className="assistant-label"><span className="ai-badge">AI</span><div><strong>报表助手</strong><small>查询 · 分析 · 解答</small></div><span className="online-dot" /></div>
    <hr className="sidebar-rule" />
    <button className="new-chat" onClick={onNew}><Icon name="plus" />新对话<span className="new-chat-shortcut">＋</span></button>
    <div className="section-label"><span>我的对话</span><button className="icon-button" aria-label="刷新会话列表" disabled={chat.catalogLoading} onClick={chat.refresh}><Icon name="refresh" /></button></div>
    <label className="search-box"><input type="search" placeholder="查找对话" aria-label="查找对话" value={query} onChange={event => onQuery(event.target.value)} /><span><Icon name="search" /></span></label>
    <div className="history-list">{chat.catalogLoading ? <p className="history-empty">正在加载你的对话…</p> : chat.catalogError ? <p className="history-empty">会话加载失败，请点击刷新重试。</p> : !sessions.length && <p className="history-empty">没有找到相关对话</p>}{!chat.catalogLoading && !chat.catalogError && sessions.map(session => <div className={`history-row${session.id === chat.catalog.activeId ? ' active' : ''}`} key={session.id}>
      <button className="history-title" title={session.title} aria-current={session.id === chat.catalog.activeId ? 'true' : undefined} onClick={() => onSelect(session.id)}>{session.title}</button>
      <button className="icon-button" aria-label={`管理对话：${session.title}`} onClick={() => onManage(session)}><Icon name="more" /></button>
    </div>)}</div>
    <button className="open-thread" onClick={onOpen}><Icon name="chat" />通过 ID 打开会话</button>
    <p className="sidebar-note">显示当前用户的历史对话</p>
    <button className="help-button" onClick={onHelp}><Icon name="help" />帮助中心</button>
  </aside>;
}

function Messages({ messages, busy, onPrompt, onCopy, onAnswer, humanInput }) {
  if (!messages.length) return <div className="welcome"><Avatar /><div className="welcome-eyebrow">让数据为你的工作提供答案</div><h1>你好，今天想了解什么？</h1><p>查询销售额、用户增长或订单情况，从一个问题开始。</p><div className="welcome-prompts">{['查询本月销售额', '本月新增用户有多少？', '查看本月订单情况'].map(text => <button disabled={busy} key={text} onClick={() => onPrompt(text)}><Icon name="arrow" />{text}</button>)}</div></div>;
  // 同批包含追问时，中间件已经丢弃其他调用，不展示过滤前的工具名称。
  const toolCallsOf = message => message.toolCalls?.some(call => call.name === 'ask_clarification') ? [] : message.toolCalls || [];
  return groupMessagesForDisplay(messages).map(group => {
    if (group.role === 'user') return <article className="message user" key={group.id}><div className="bubble">{group.messages[0].text}</div><Avatar user /></article>;
    const visibleMessages = group.messages.filter(message => clarificationOf(message) || message.text || toolCallsOf(message).length);
    if (!visibleMessages.length) return null;
    const replyText = visibleMessages.filter(message => message.role === 'assistant' && message.text).map(message => message.text).join('\n\n');
    return <article className="message assistant assistant-turn" aria-label="AI 回复" key={group.id}><Avatar /><div className="message-body">
      {visibleMessages.map(message => {
        const request = clarificationOf(message);
        if (request) return <ClarificationCard key={message.id} request={request} answer={humanInput.answers.get(request.request_id)} active={humanInput.openRequest?.request_id === request.request_id} disabled={busy} onAnswer={onAnswer} />;
        if (message.role === 'tool') return <details className="tool-result" key={message.id}><summary>{message.name === 'query_report' ? '报表查询结果' : message.name || '工具结果'}<span>展开查看</span></summary><pre>{message.text}</pre></details>;
        const visibleCalls = toolCallsOf(message);
        return <div className="assistant-part" key={message.id}>
          {message.text && <div className="bubble"><MessageText text={message.text} /></div>}
          {!!visibleCalls.length && <div className="tool-calls"><Icon name="search" />{visibleCalls.map(call => call.name === 'query_report' ? '查询报表数据' : call.name || '执行工具').join('、')}</div>}
        </div>;
      })}
      {replyText && <div className="message-actions"><button className="icon-button" aria-label="复制回复" onClick={() => onCopy(replyText)}><Icon name="copy" /></button></div>}
    </div></article>;
  });
}

function Composer({ value, onChange, busy, disabled, onSend, onTasks, inputRef, waiting }) {
  useEffect(() => {
    const input = inputRef.current;
    input.style.height = 'auto';
    input.style.height = `${Math.min(160, Math.max(innerWidth <= 640 ? 52 : 65, input.scrollHeight))}px`;
  }, [value, inputRef]);
  const ready = !disabled && Boolean(value.trim());
  return <section className="composer-wrap" aria-label="消息输入区">
    <div className="composer-toolbar"><span className="composer-label"><span className="online-dot" />{busy ? '正在处理你的问题' : waiting ? '助手正在等你补充信息' : '报表助手'}</span><button className="toolbar-button" onClick={onTasks}><Icon name="task" />任务记录</button></div>
    <form className="composer" onSubmit={event => { event.preventDefault(); if (ready) onSend(); }}>
      <textarea ref={inputRef} placeholder={busy ? '可以先写下一个问题，等待当前回复完成…' : waiting ? '也可以在这里回答上面的问题…' : '您想咨询什么...'} aria-label="输入消息" rows="2" value={value} onChange={event => onChange(event.target.value)} onKeyDown={event => { if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); if (ready) onSend(); } }} />
      <div className="composer-bottom"><span className="input-hint">Enter 发送 · Shift + Enter 换行</span><button type="submit" className={`send-button${ready ? ' ready' : ''}`} aria-label={busy ? '正在生成回复' : '发送消息'} disabled={!ready}>{busy ? <span className="spinner" /> : <Icon name="send" />}</button></div>
    </form>
    <p className="composer-footnote">AI 生成内容仅供参考，请结合实际业务核对。</p>
  </section>;
}

function Modal({ title, children, onClose, wide = false }) {
  const ref = useRef(null);
  useEffect(() => { if (!ref.current.open) ref.current.showModal(); }, []);
  return <dialog ref={ref} className={wide ? 'wide-dialog' : ''} aria-labelledby="dialog-title" onCancel={onClose} onClick={event => { if (event.target !== ref.current) return; const rect = ref.current.getBoundingClientRect(); if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) onClose(); }}>
    <div className="dialog-heading"><h2 id="dialog-title">{title}</h2><button className="icon-button" aria-label="关闭" onClick={onClose}><Icon name="close" /></button></div>{children}
  </dialog>;
}

function ManageSession({ session, onRename, onRemove, onCopy }) {
  const [name, setName] = useState(session.title);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  return <><form onSubmit={async event => { event.preventDefault(); if (!name.trim() || saving) return; setSaving(true); setError(''); try { if (!await onRename(name.trim())) setError('保存名称失败，请重试。'); } finally { setSaving(false); } }}><label className="form-label">对话名称<input className="rename-input" aria-label="对话名称" maxLength={60} value={name} onChange={event => setName(event.target.value)} required /></label><p className="dialog-note">名称保存到服务器。移出本机列表只影响当前用户在这台浏览器中的显示，可通过会话 ID 再次打开。</p>{error && <p className="error-text" role="alert">{error}</p>}<button type="button" className="text-button" onClick={onCopy}><Icon name="copy" />复制会话 ID</button><div className="manage-actions"><button className="primary" disabled={saving} type="submit">{saving ? '正在保存…' : '保存名称'}</button><button className="delete-session" disabled={saving} type="button" onClick={onRemove}>移出本机列表</button></div></form></>;
}

function OpenSession({ onOpen }) {
  const [value, setValue] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  return <form onSubmit={async event => { event.preventDefault(); if (loading) return; setLoading(true); setError(''); try { if (!await onOpen(value)) setError('无法打开该会话，请确认 ID 正确且属于当前用户，并检查服务连接。'); } finally { setLoading(false); } }}><p>输入属于当前用户的会话 ID，读取消息和任务记录。</p><input className="rename-input" aria-label="会话 ID" placeholder="粘贴会话 ID" value={value} onChange={event => setValue(event.target.value)} maxLength={64} required />{error && <p className="error-text" role="alert">{error}</p>}<div className="modal-footer"><button className="primary" disabled={loading} type="submit">{loading ? '正在打开…' : '打开会话'}</button></div></form>;
}

export default function App() {
  const [userId, setUserId] = useState(loadUserId);
  function login(id) { saveUserId(id); setUserId(id); }
  function logout() { saveUserId(null); setUserId(null); }
  return userId ? <ChatPage key={userId} userId={userId} onLogout={logout} /> : <Login onLogin={login} />;
}

function ChatPage({ userId, onLogout }) {
  const [query, setQuery] = useState('');
  const [input, setInput] = useState('');
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [dialog, setDialog] = useState(null);
  const [notice, setNotice] = useState('');
  const toastTimer = useRef(null);
  const conversation = useRef(null);
  const inputRef = useRef(null);
  const followEnd = useRef(true);
  const olderScroll = useRef(null);
  const toast = useCallback(message => { clearTimeout(toastTimer.current); setNotice(message); toastTimer.current = setTimeout(() => setNotice(''), 3500); }, []);
  const chat = useChat(userId, toast);
  const { catalog, view, streamingThread } = chat;
  const session = catalog.sessions.find(item => item.id === catalog.activeId);
  const messages = view.messages || [];
  const humanInput = clarificationState(messages);
  const busy = isActive(view.run?.status) || streamingThread === session.id;
  const disabled = busy || Boolean(streamingThread) || Boolean(view.loading) || !view.loaded || Boolean(view.error && !view.loaded);
  const error = view.error || (['error', 'timeout', 'interrupted'].includes(view.run?.status) ? view.run.error || STATUS_LABELS[view.run.status] : '');

  useEffect(() => () => clearTimeout(toastTimer.current), []);
  useEffect(() => {
    document.body.classList.toggle('sidebar-open', sidebarOpen);
    const close = event => { if (event.key === 'Escape') setSidebarOpen(false); };
    document.addEventListener('keydown', close);
    return () => { document.body.classList.remove('sidebar-open'); document.removeEventListener('keydown', close); };
  }, [sidebarOpen]);
  useEffect(() => { followEnd.current = true; setInput(''); setSidebarOpen(false); }, [session.id]);
  useEffect(() => {
    const region = conversation.current;
    if (olderScroll.current) { region.scrollTop = olderScroll.current.top + region.scrollHeight - olderScroll.current.height; olderScroll.current = null; }
    else if (followEnd.current) region.scrollTop = region.scrollHeight;
  }, [messages]);

  async function copy(text, label = '已复制回复') {
    try { await navigator.clipboard.writeText(text); toast(label); }
    catch { toast('复制不可用，请手动复制：' + text); }
  }
  async function send(text = input) {
    if (disabled || !text.trim()) return;
    followEnd.current = true;
    setInput('');
    await chat.send(text);
  }
  async function answer(response) {
    if (disabled) return false;
    followEnd.current = true;
    return chat.send(response.value, response);
  }
  function create() { chat.create(); setQuery(''); inputRef.current.focus(); }
  const openDialog = type => setDialog({ type });
  return <>
    <IconSprite />
    <Header userId={userId} onLogout={onLogout} sidebarOpen={sidebarOpen} onMenu={() => setSidebarOpen(!sidebarOpen)} onHome={() => { followEnd.current = true; conversation.current.scrollTop = conversation.current.scrollHeight; }} onDialog={openDialog} />
    <div className="sidebar-shade" onClick={() => setSidebarOpen(false)} />
    <Sidebar chat={chat} query={query} onQuery={setQuery} onSelect={chat.select} onNew={create} onManage={item => setDialog({ type: 'manage', session: item })} onOpen={() => openDialog('open')} onHelp={() => openDialog('help')} />
    <main className="workspace" aria-label="AI 对话工作台">
      <div className="conversation-header"><div><h1>{session.title}</h1><span className={`status-badge ${busy ? 'running' : !error && humanInput.openRequest ? 'waiting' : view.run?.status || ''}`}>{busy ? '正在生成' : view.loading ? '正在同步' : !error && humanInput.openRequest ? '等待补充信息' : STATUS_LABELS[view.run?.status] || '准备就绪'}</span></div><button className="text-button" onClick={() => void copy(session.id, '已复制会话 ID')}><Icon name="copy" /><span>会话 ID</span></button></div>
      <div ref={conversation} className="conversation" role="region" aria-label="聊天记录" tabIndex="0" onScroll={() => { const region = conversation.current; followEnd.current = region.scrollHeight - region.scrollTop - region.clientHeight < 100; }}><div className="conversation-inner">
        {view.hasMore && <div className="load-older"><button className="text-button" disabled={view.paging || busy} onClick={() => { olderScroll.current = { top: conversation.current.scrollTop, height: conversation.current.scrollHeight }; void chat.loadOlder(); }}>{view.paging ? '正在加载…' : '加载更早的消息'}</button></div>}
        {view.loading && !messages.length ? <div className="loading-history"><span className="spinner" />正在读取聊天记录…</div> : <Messages key={session.id} messages={messages} busy={disabled} onPrompt={send} onCopy={copy} onAnswer={answer} humanInput={humanInput} />}
        {busy && <div className="generation-status" role="status"><span className="thinking-dots"><i /><i /><i /></span>{streamingThread === session.id ? '正在思考并生成回复…' : '任务仍在执行，正在同步消息…'}</div>}
        {error && <div className="chat-error" role="alert"><p>{error}</p><div><button className="text-button" onClick={chat.refresh}><Icon name="refresh" />重新同步</button><button className="text-button" onClick={() => openDialog('tasks')}>查看任务记录</button></div></div>}
      </div></div>
      <Composer value={input} onChange={setInput} busy={busy} disabled={disabled} waiting={Boolean(humanInput.openRequest)} onSend={() => void send()} onTasks={() => openDialog('tasks')} inputRef={inputRef} />
    </main>
    <div className={`toast${notice ? ' visible' : ''}`} role="status">{notice}</div>
    {dialog && <Modal key={dialog.session?.id || dialog.type} title={({ manage: '管理对话', open: '打开已有会话', tasks: '任务记录', help: '帮助中心', messages: '消息中心', profile: '个人中心' })[dialog.type]} wide={dialog.type === 'tasks'} onClose={() => setDialog(null)}>
      {dialog.type === 'tasks' ? <RunHistory threadId={session.id} userId={userId} /> : dialog.type === 'manage' ? <ManageSession session={dialog.session} onCopy={() => void copy(dialog.session.id, '已复制会话 ID')} onRename={async title => { const saved = await chat.rename(dialog.session.id, title); if (saved) setDialog(null); return saved; }} onRemove={() => { if (chat.remove(dialog.session.id)) { setDialog(null); toast('已移出本机列表，服务器记录保留。'); } }} /> : dialog.type === 'open' ? <OpenSession onOpen={async id => { const opened = await chat.open(id); if (opened) { setQuery(''); setDialog(null); } return opened; }} /> : dialog.type === 'help' ? <><p>输入问题，按 Enter 发送；Shift + Enter 换行。</p><p>会话列表、名称和聊天记录从服务器读取，重新登录相同的用户 ID 即可找回。</p><p>任务记录可以查看每次运行的状态和失败原因。</p><div className="dialog-note">当前暂不支持停止后台任务、上传附件和删除服务器记录。</div></> : dialog.type === 'profile' ? <><p>当前用户：<strong>{userId}</strong></p><button className="primary" onClick={onLogout}>退出登录</button></> : <p>暂无新通知。</p>}
    </Modal>}
  </>;
}
