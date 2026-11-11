import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

// 宽表格在消息内部横向滚动，避免把整个聊天页面撑宽。
function MarkdownTable({ children }) {
  return <div className="markdown-table" role="region" aria-label="数据表格" tabIndex={0}>
    <table>{children}</table>
  </div>;
}

function MarkdownLink({ href, title, children }) {
  return <a href={href} title={title} target="_blank" rel="noopener noreferrer">
    {children}
  </a>;
}

const components = { table: MarkdownTable, a: MarkdownLink };

export function MarkdownMessage({ text }) {
  return <div className="markdown-message">
    <ReactMarkdown remarkPlugins={[remarkGfm]} components={components} skipHtml>
      {text}
    </ReactMarkdown>
  </div>;
}
