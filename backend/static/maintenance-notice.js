/* Independent of dashboard initialization: never reload or replace user forms. */
(() => {
    'use strict';
    const banner = document.getElementById('maintenance-notice');
    const announcer = document.getElementById('maintenance-announcer');
    if (!banner || !announcer) return;
    const labels = {
        planned: 'Planned maintenance', in_progress: 'Maintenance in progress',
        completed: 'Maintenance completed', cancelled: 'Maintenance cancelled',
    };
    let fingerprint = '';
    let expiresAt = 0;
    let expiresAtWall = 0;
    let inFlight = false;

    function clearNotice() {
        if (!fingerprint) return;
        banner.replaceChildren();
        banner.hidden = true;
        fingerprint = '';
        expiresAt = 0;
        expiresAtWall = 0;
        announcer.textContent = 'The maintenance announcement is no longer active.';
    }

    function windowText(notice) {
        const formatter = new Intl.DateTimeFormat('en-US', {
            timeZone: notice.time_zone, year: 'numeric', month: 'short', day: 'numeric',
            hour: 'numeric', minute: '2-digit', timeZoneName: 'short',
        });
        return `Window: ${formatter.format(new Date(notice.starts_at))} – ${formatter.format(new Date(notice.ends_at))} (${notice.time_zone})`;
    }

    function isExpired() {
        // Some mobile monotonic clocks pause during device sleep. Either clock
        // can expire the notice; moving the wall clock back cannot extend it.
        return expiresAt && (performance.now() >= expiresAt || Date.now() >= expiresAtWall);
    }

    function render(payload, requestedAt, requestedWall) {
        const notice = payload.notice;
        if (notice === null) { clearNotice(); return; }
        if (!notice || !Object.hasOwn(labels, notice.status)
            || typeof notice.message !== 'string' || typeof notice.impact !== 'string') return;
        const remaining = Date.parse(notice.expires_at) - Date.parse(payload.server_time);
        if (!Number.isFinite(remaining)) return;
        if (remaining <= 0) { clearNotice(); return; }
        const window = windowText(notice);
        // Anchor before response transit so a slow response never extends expiry.
        expiresAt = requestedAt + remaining;
        expiresAtWall = requestedWall + remaining;
        if (isExpired()) { clearNotice(); return; }
        const next = JSON.stringify(notice);
        if (next === fingerprint) return;
        const heading = document.createElement('h2');
        heading.textContent = labels[notice.status];
        const message = document.createElement('p');
        message.textContent = notice.message;
        const dates = document.createElement('p');
        dates.className = 'notice-window';
        dates.textContent = window;
        const impact = document.createElement('p');
        impact.textContent = `Expected impact: ${notice.impact}`;
        banner.replaceChildren(heading, message, dates, impact);
        banner.hidden = false;
        fingerprint = next;
        announcer.textContent = `${heading.textContent}. ${message.textContent} ${window}. ${impact.textContent}`;
    }

    async function refresh() {
        if (inFlight || document.hidden) return;
        inFlight = true;
        const controller = new AbortController();
        const requestedAt = performance.now();
        const requestedWall = Date.now();
        const timeout = setTimeout(() => controller.abort(), 8000);
        try {
            const response = await fetch('/api/maintenance-notice', {
                cache: 'no-store', credentials: 'same-origin', signal: controller.signal,
                headers: { Accept: 'application/json' },
            });
            if (response.ok) render(await response.json(), requestedAt, requestedWall);
        } catch (_) {
            // Keep a known valid notice during outages, but never past its expiry.
        } finally {
            clearTimeout(timeout);
            inFlight = false;
        }
    }

    setInterval(() => {
        if (isExpired()) clearNotice();
    }, 1000);
    setInterval(refresh, 30000);
    document.addEventListener('visibilitychange', () => {
        if (isExpired()) clearNotice();
        if (!document.hidden) void refresh();
    });
    window.addEventListener('focus', refresh);
    void refresh();
})();
