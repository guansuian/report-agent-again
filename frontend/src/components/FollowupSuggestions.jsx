import { useEffect, useState } from 'react';
import { Alert, Button, ConfigProvider, Modal, Spin } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import { Icon } from '../Icons.jsx';

export function FollowupSuggestions({ state, value, onChange, inputRef, disabled, active }) {
  const [pending, setPending] = useState(null);
  const visible = state.loading || state.error || state.questions.length > 0;

  useEffect(() => { if (disabled || !active) setPending(null); }, [disabled, active]);

  function fillDraft(question) {
    if (disabled || !active) return;
    setPending(null);
    state.dismiss();
    onChange(question);
    requestAnimationFrame(() => {
      const input = inputRef.current;
      if (!input) return;
      input.focus();
      input.setSelectionRange(question.length, question.length);
    });
  }

  function choose(question) {
    if (value.trim()) setPending(question);
    else fillDraft(question);
  }

  if (!visible || disabled || !active) return null;
  return <ConfigProvider locale={zhCN} theme={{ token: { colorPrimary: '#6f79db', borderRadius: 10 } }}>
    <section className="followup-suggestions" aria-label="推荐问题" aria-busy={state.loading}>
      <div className="followup-heading"><span><Icon name="chat" />接下来可以问</span><Button type="text" size="small" shape="circle" aria-label="关闭推荐问题" icon={<Icon name="close" />} onClick={state.dismiss} /></div>
      {state.loading ? <div className="followup-loading" role="status"><Spin size="small" /><span>正在生成推荐问题…</span></div>
        : state.error ? <Alert type="warning" showIcon title="推荐问题生成失败" description={state.error} />
          : <div className="followup-questions">{state.questions.map((question, index) => <Button className="followup-chip" key={`${index}:${question}`} onClick={() => choose(question)}>{question}</Button>)}</div>}
    </section>
    <Modal
      title="输入框里还有内容"
      open={pending !== null}
      onCancel={() => setPending(null)}
      width={480}
      footer={[
        <Button key="cancel" onClick={() => setPending(null)}>取消</Button>,
        <Button key="append" onClick={() => fillDraft(`${value}\n${pending}`)}>追加到草稿</Button>,
        <Button key="replace" type="primary" onClick={() => fillDraft(pending)}>替换草稿</Button>,
      ]}
    >
      <p>将推荐问题填入输入框，编辑后再点击发送。</p>
      <div className="followup-draft"><span>当前草稿</span><p>{value}</p></div>
      <div className="followup-preview"><span>推荐问题</span><p>{pending}</p></div>
    </Modal>
  </ConfigProvider>;
}
