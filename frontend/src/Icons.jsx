export function Icon({ name, className = "" }) {
  return <svg className={`icon ${className}`} aria-hidden="true"><use href={`#i-${name}`} /></svg>;
}

export function IconSprite() {
  return (<svg className="sprite" xmlns="http://www.w3.org/2000/svg" aria-hidden="true"><defs>
    <symbol id="i-plus" viewBox="0 0 24 24"><path d="M12 5v14M5 12h14"/></symbol>
    <symbol id="i-search" viewBox="0 0 24 24"><circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 4 4"/></symbol>
    <symbol id="i-refresh" viewBox="0 0 24 24"><path d="M20 8a9 9 0 1 0 1 8M20 3v5h-5"/></symbol>
    <symbol id="i-copy" viewBox="0 0 24 24"><rect x="5" y="6" width="12" height="15" rx="1"/><path d="M9 3h11v15M8 9h6"/></symbol>
    <symbol id="i-send" viewBox="0 0 24 24"><path d="m21 3-6 18-4-8-8-4 18-6ZM11 13l5-5"/></symbol>
    <symbol id="i-mail" viewBox="0 0 24 24"><rect x="2" y="4" width="20" height="16" rx="3"/><path d="m6 8 6 5 6-5"/></symbol>
    <symbol id="i-more" viewBox="0 0 24 24"><circle cx="12" cy="5" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="12" cy="19" r="1"/></symbol>
    <symbol id="i-help" viewBox="0 0 24 24"><rect x="3" y="3" width="18" height="18" rx="4"/><path d="m12 7 1.6 3.2 3.5.5-2.5 2.5.6 3.5-3.2-1.6-3.2 1.6.6-3.5-2.5-2.5 3.5-.5L12 7Z"/></symbol>
    <symbol id="i-task" viewBox="0 0 24 24"><rect x="4" y="3" width="14" height="17" rx="2"/><path d="M8 7h6M8 11h3m6 5 4 4"/><circle cx="15" cy="14" r="4"/></symbol>
    <symbol id="i-arrow" viewBox="0 0 24 24"><path d="M5 12h14m-6-6 6 6-6 6"/></symbol>
    <symbol id="i-close" viewBox="0 0 24 24"><path d="m6 6 12 12M6 18 18 6"/></symbol>
    <symbol id="i-menu" viewBox="0 0 24 24"><path d="M4 6h16M4 12h16M4 18h16"/></symbol>
    <symbol id="i-cut" viewBox="0 0 24 24"><circle cx="5" cy="18" r="3"/><circle cx="19" cy="18" r="3"/><path d="m4 3 13 13M20 3 7 16"/></symbol>
    <symbol id="i-chat" viewBox="0 0 24 24"><path d="M21 11a9 9 0 0 1-9 9 11 11 0 0 1-4-1l-5 2 1-5a9 9 0 1 1 17-5Z"/><path d="M8 10h8M8 14h5"/></symbol>
    <symbol id="i-stop" viewBox="0 0 24 24"><rect x="6" y="6" width="12" height="12" rx="2" fill="currentColor" stroke="none"/></symbol>
  </defs></svg>);
}
