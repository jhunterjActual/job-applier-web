(() => {
    'use strict';

    const address = 'feedback@jhunterj.com';
    const message = document.getElementById('suggested-message');
    const draft = document.getElementById('email-draft');
    const status = document.getElementById('copy-status');
    const source = new URLSearchParams(window.location.search).get('from');
    const subjects = new Map([
        ['sign-in', 'CareerTrellis — Sign-in help'],
        ['access', 'CareerTrellis — Access question'],
        ['dashboard', 'CareerTrellis — Feedback'],
    ]);
    const subject = subjects.get(source) || 'CareerTrellis — Feedback';
    draft.href = `mailto:${address}?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(message.value)}`;

    // Copy controls enhance the always-visible address and selectable message.
    if (!navigator.clipboard || !window.isSecureContext) return;

    function enableCopy(id, value, success, fallback) {
        const button = document.getElementById(id);
        button.hidden = false;
        button.addEventListener('click', async () => {
            button.disabled = true;
            status.textContent = '';
            try {
                await navigator.clipboard.writeText(value());
                status.textContent = success;
            } catch (_) {
                status.textContent = fallback;
            } finally {
                button.disabled = false;
            }
        });
    }

    enableCopy('copy-address', () => address,
        'Email address copied.',
        'Could not copy automatically. Select the email address above and copy it manually.');
    enableCopy('copy-message', () => message.value,
        'Suggested message copied. Paste it into your email and add your feedback.',
        'Could not copy automatically. Select the suggested message and copy it manually.');
})();
