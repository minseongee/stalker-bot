/**
 * sidebar.js — 모든 유저 페이지에 공통 사이드바를 주입합니다.
 * 각 페이지에서 <script src="/static/sidebar.js"></script> 한 줄만 추가하면 됩니다.
 *
 * 페이지 body에 data-page="key" 속성을 지정하면 해당 nav 항목이 활성화됩니다.
 *   data-page="home" | "chart" | "watchlist" | "alerts"
 */

const NAV = [
  { key: 'home',      href: '/dashboard',     icon: '🏠', label: '홈' },
  { key: 'chart',     href: '/chart-editor',  icon: '✏️', label: '차트 수정' },
  { key: 'watchlist', href: '/watchlist',     icon: '⭐', label: '관심 종목' },
  { key: 'alerts',    href: '/alert-history', icon: '🔔', label: '알림 내역' },
];

const CSS = `
  :root {
    --sidebar-w: 220px;
    --bg-body: #0f1117;
    --bg-sidebar: #13151f;
    --bg-card: #16213e;
    --border: #1e2130;
    --border-blue: #1e3a5f;
    --accent: #7dd3fc;
    --text: #e0e0e0;
    --muted: #666;
    --hover: #1a2a40;
  }
  * { margin:0; padding:0; box-sizing:border-box; }
  body {
    display:flex; min-height:100vh;
    background:var(--bg-body); color:var(--text);
    font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
  }

  /* ── 사이드바 ── */
  #sidebar {
    width:var(--sidebar-w); min-height:100vh;
    background:var(--bg-sidebar); border-right:1px solid var(--border);
    display:flex; flex-direction:column;
    position:sticky; top:0; height:100vh; overflow-y:auto;
    flex-shrink:0;
  }
  .sb-logo {
    display:flex; align-items:center; gap:9px;
    padding:20px 18px 14px;
    border-bottom:1px solid var(--border);
  }
  .sb-logo-icon { font-size:1.3rem; }
  .sb-logo-name { font-size:0.95rem; font-weight:700; color:#fff; }
  .sb-nav { flex:1; padding:10px 0; }
  .sb-nav a {
    display:flex; align-items:center; gap:10px;
    padding:10px 18px; text-decoration:none;
    color:var(--muted); font-size:0.88rem;
    border-left:3px solid transparent;
    transition:background .15s, color .15s;
  }
  .sb-nav a:hover { background:var(--hover); color:var(--text); }
  .sb-nav a.active {
    background:rgba(125,211,252,.07);
    color:var(--accent); border-left-color:var(--accent);
  }
  .sb-nav-icon { width:18px; text-align:center; font-size:0.95rem; }
  .sb-user {
    padding:14px 18px; border-top:1px solid var(--border);
    display:flex; align-items:center; gap:10px;
  }
  .sb-avatar {
    width:30px; height:30px; border-radius:50%;
    background:var(--border-blue); object-fit:cover; flex-shrink:0;
  }
  .sb-username {
    font-size:0.82rem; color:var(--text); flex:1;
    white-space:nowrap; overflow:hidden; text-overflow:ellipsis;
  }
  .sb-logout {
    font-size:0.75rem; color:var(--muted);
    background:none; border:1px solid var(--border);
    border-radius:5px; padding:3px 8px; cursor:pointer;
    text-decoration:none; flex-shrink:0;
    transition:color .15s, border-color .15s;
  }
  .sb-logout:hover { color:var(--text); border-color:#444; }
  .sb-admin-link {
    display:none; align-items:center; gap:10px;
    padding:10px 18px; text-decoration:none;
    color:#f59e0b; font-size:0.88rem;
    border-left:3px solid transparent;
    border-top:1px solid var(--border);
    transition:background .15s, color .15s;
  }
  .sb-admin-link:hover { background:rgba(245,158,11,.08); }
  .sb-admin-link.visible { display:flex; }

  /* ── 페이지 컨텐츠 래퍼 ── */
  #page-wrap { flex:1; overflow-y:auto; min-width:0; }
`;

(function () {
  const activePage = document.body.dataset.page || '';

  // CSS 주입
  const style = document.createElement('style');
  style.textContent = CSS;
  document.head.appendChild(style);

  // 사이드바 HTML 생성
  const nav = document.createElement('nav');
  nav.id = 'sidebar';
  nav.innerHTML = `
    <div class="sb-logo">
      <span class="sb-logo-icon">📈</span>
      <span class="sb-logo-name">Stalker Bot</span>
    </div>
    <div class="sb-nav">
      ${NAV.map(n => `
        <a href="${n.href}" class="${n.key === activePage ? 'active' : ''}">
          <span class="sb-nav-icon">${n.icon}</span>
          ${n.label}
        </a>`).join('')}
    </div>
    <a id="sb-admin-link" class="sb-admin-link" href="/administrator">
      <span class="sb-nav-icon">⚙️</span>관리자 패널
    </a>
    <div class="sb-user">
      <img id="sb-avatar" class="sb-avatar" src="" alt="">
      <span id="sb-username" class="sb-username">...</span>
      <a href="/auth/logout" class="sb-logout">로그아웃</a>
    </div>
  `;

  // 기존 body 자식들을 #page-wrap으로 감싸기
  const wrap = document.createElement('div');
  wrap.id = 'page-wrap';
  while (document.body.firstChild) wrap.appendChild(document.body.firstChild);
  document.body.appendChild(nav);
  document.body.appendChild(wrap);

  // 유저 정보 로드
  fetch('/api/me').then(r => {
    if (!r.ok) { location.href = '/login'; return null; }
    return r.json();
  }).then(user => {
    if (!user) return;
    document.getElementById('sb-username').textContent = user.global_name || user.username;
    document.getElementById('sb-avatar').src = user.avatar
      ? `https://cdn.discordapp.com/avatars/${user.id}/${user.avatar}.png?size=64`
      : `https://cdn.discordapp.com/embed/avatars/${parseInt(user.id) % 5}.png`;
    if (user.is_admin) document.getElementById('sb-admin-link').classList.add('visible');
    if (typeof window.__onSidebarUser === 'function') window.__onSidebarUser(user);
  });
})();
