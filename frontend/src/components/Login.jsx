import { useState } from 'react';
import companyLogo from '../assets/company-logo.svg';
import { normalizeUserId } from '../storage.js';

export function Login({ onLogin }) {
  const [value, setValue] = useState('');
  const [error, setError] = useState('');
  function submit(event) {
    event.preventDefault();
    try { onLogin(normalizeUserId(value)); }
    catch (error) { setError(error.message); }
  }
  return <main className="login-page">
    <div className="login-brand"><img src={companyLogo} alt="银河星尘" width="136" height="30" /></div>
    <section className="login-card" aria-labelledby="login-title">
      <div className="login-badge">AI 报表助手</div>
      <h1 id="login-title">欢迎回来</h1>
      <p className="login-intro">输入用户 ID，继续你的报表对话。</p>
      <form onSubmit={submit}>
        <label className="form-label" htmlFor="user-id">用户 ID</label>
        <input id="user-id" className="login-input" name="user_id" placeholder="例如：zhangsan" autoComplete="username" autoCapitalize="none" spellCheck={false} value={value} onChange={event => { setValue(event.target.value); setError(''); }} required maxLength={64} aria-describedby={error ? 'login-error' : 'login-hint'} autoFocus />
        {error && <p id="login-error" className="error-text" role="alert">{error}</p>}
        <button type="submit" className="primary login-submit">登录并进入</button>
      </form>
      <p id="login-hint" className="login-hint">再次使用相同的 ID，可以找回之前的对话。</p>
    </section>
    <p className="login-footer">查询 · 分析 · 解答，让数据为工作提供答案</p>
  </main>;
}
