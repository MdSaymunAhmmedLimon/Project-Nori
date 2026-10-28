/* Shared inbox indicator, including administrator and moderator workspaces. */
(() => {
  const bell = document.getElementById('notification-bell');
  if (!bell) return;
  let loading = false;
  let socket;
  let stopped = false;
  function update(count) {
    bell.setAttribute('aria-label', `Notifications, ${count} unread`);
    document.querySelectorAll('[data-unread-badge]').forEach(badge => {
      badge.hidden = !count;
      badge.textContent = count > 99 ? '99+' : String(count);
    });
    document.querySelectorAll('[data-unread-count]').forEach(node => { node.textContent = count; });
  }
  async function refresh() {
    if (loading || stopped || document.hidden) return;
    loading = true;
    try {
      const response = await fetch('/api/notifications/summary/', {credentials: 'same-origin', cache: 'no-store'});
      if (response.ok) update((await response.json()).unread_count);
    } catch (_) { /* Recover on the next visible refresh. */ }
    finally { loading = false; }
  }
  document.addEventListener('nori:notifications', event => update(event.detail));
  // The ride workspace already maintains its own socket and polling loop.
  if (!document.getElementById('app-config')) {
    const connect = () => {
      if (stopped) return;
      socket = new WebSocket(`${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}/ws/events/`);
      socket.onmessage = event => {
        try { if (JSON.parse(event.data).event !== 'pong') refresh(); } catch (_) { /* Ignore malformed events. */ }
      };
      socket.onclose = () => { if (!stopped) setTimeout(connect, 15000); };
      socket.onerror = () => socket.close();
    };
    connect();
    setInterval(refresh, 30000);
    document.addEventListener('visibilitychange', refresh);
    window.addEventListener('pagehide', () => { stopped = true; socket?.close(); });
    window.addEventListener('pageshow', event => { if (event.persisted) { stopped = false; connect(); refresh(); } });
  }
})();
