import { useState } from 'react';
import companyLogo from '../assets/company-logo.svg';

export function Login({ onLogin }) {
  const [userId, setUserId] = useState('');
  const [token, setToken] = useState('');
  const [deviceFingerprint, setDeviceFingerprint] = useState('');
  const [error, setError] = useState('');
  function submit(event) {
    event.preventDefault();
    // App 校验并保存三个字段；失败时把错误显示在当前表单中。
    try { onLogin({ userId, token, deviceFingerprint }); }
    catch (error) { setError(error.message); }
  }
  return <main className="login-page">
    <div className="login-brand"><img src={companyLogo} alt="银河星尘" width="136" height="30" /></div>
    <section className="login-card" aria-labelledby="login-title">
      <div className="login-badge">AI 报表助手</div>
      <h1 id="login-title">欢迎回来</h1>
      <p className="login-intro">填写登录信息，继续你的报表对话。</p>
      <form onSubmit={submit}>
        <div className="login-field">
          <label className="form-label" htmlFor="user-id">用户 ID</label>
          <input
            id="user-id" className="login-input" name="user_id"
            placeholder="例如：zhangsan" autoComplete="username"
            autoCapitalize="none" spellCheck={false} required maxLength={64} autoFocus
            value={userId} onChange={event => { setUserId(event.target.value); setError(''); }}
            aria-describedby={error ? 'login-error' : 'login-hint'}
          />
        </div>
        <div className="login-field">
          <label className="form-label" htmlFor="user-token">Token</label>
          <input
            id="user-token" className="login-input" name="token" type="password"
            placeholder="粘贴公司系统提供的 Token" autoComplete="off"
            autoCapitalize="none" spellCheck={false} required
            value={token} onChange={event => { setToken(event.target.value); setError(''); }}
            aria-describedby={error ? 'login-error' : 'token-hint'}
          />
          <p id="token-hint" className="login-field-hint">可直接粘贴 Token，也支持包含 Bearer 前缀。</p>
        </div>
        <div className="login-field">
          <label className="form-label" htmlFor="device-fingerprint">设备指纹<span className="login-optional">可选</span></label>
          <input
            id="device-fingerprint" className="login-input" name="device_fingerprint"
            placeholder="粘贴设备指纹" autoComplete="off"
            autoCapitalize="none" spellCheck={false}
            value={deviceFingerprint} onChange={event => { setDeviceFingerprint(event.target.value); setError(''); }}
            aria-describedby={error ? 'login-error' : undefined}
          />
        </div>
        {error && <p id="login-error" className="error-text" role="alert">{error}</p>}
        <button type="submit" className="primary login-submit">登录并进入</button>
      </form>
      <p id="login-hint" className="login-hint">登录信息仅在当前标签页保留，退出后清除。<br />相同的用户 ID 可以找回之前的对话。</p>
    </section>
    <p className="login-footer">查询 · 分析 · 解答，让数据为工作提供答案</p>
  </main>;
}
