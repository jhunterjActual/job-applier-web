/* Recovery handoff only. No exchange, session, clipboard, or network effects. */
(() => {
    'use strict';
    const query = window.location.search;
    // Scrub before parsing or displaying even malformed provider responses.
    window.history.replaceState(null, '', '/auth/callback');
    const field = document.getElementById('recovery-response');
    if (!field) return;
    try {
        const entries = Array.from(new URLSearchParams(query).entries());
        if (entries.length !== 2) return;
        const values = Object.fromEntries(entries);
        if (Object.keys(values).length !== 2 || !Object.hasOwn(values, 'code') ||
            !Object.hasOwn(values, 'state') || !/^r1_[A-Za-z0-9_-]{40}$/.test(values.state) ||
            !/^[\x21-\x7e]{1,2048}$/.test(values.code)) return;
        const payload = JSON.stringify({code: values.code, state: values.state});
        if (payload.length > 4096) return;
        field.value = payload;
    } catch (_) {
        // Never display raw errors or partial authorization material.
        field.value = '';
    }
})();
