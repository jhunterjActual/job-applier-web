"""Healthchecks.io delivery adapter. No account setup, scheduler or secret storage.

Call only with an approved check URL loaded from protected configuration and a
fresh validated observation. URLs are credentials and must never be logged.
"""
import http.client
import re
import ssl

import admission_alert_contract as contract


def submit(ping_url, classification, *, version=1):
    """Return accepted/unconfirmed, never a claim of email receipt.

    One attempt only: the scheduler must re-observe before its next attempt.
    Replaying a queued old success could falsely recover a now-failed check.
    Network exceptions and ambiguous acknowledgements stay unconfirmed.
    """
    connection = None
    try:
        if not isinstance(ping_url, str):
            return 'unconfirmed'
        matched = re.fullmatch(r'https://hc-ping\.com/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})', ping_url)
        if not matched:
            return 'unconfirmed'
        if type(version) is not int or version not in (1, 2, 3):
            return 'unconfirmed'
        if version == 3:
            import backup_reporter
            payload = backup_reporter.message(classification)
        elif version == 2:
            import totp_cleanup_alert_contract
            payload = totp_cleanup_alert_contract.message(classification)
        else:
            payload = contract.message(classification)
        if payload is None:
            payload = ('CareerTrellis staging: fresh admission and authenticator cleanup status reports healthy.'
                if version == 2 else 'CareerTrellis staging: fresh admission cleanup status reports healthy.')
        connection = http.client.HTTPSConnection('hc-ping.com', timeout=8,
            context=ssl.create_default_context())
        path = '/' + matched.group(1) + ('/fail' if classification else '')
        connection.request('POST', path, body=payload.encode('utf-8'),
            headers={'Content-Type':'text/plain; charset=utf-8', 'User-Agent':'CareerTrellis-admission-monitor/1'})
        response = connection.getresponse()
        # Reject redirects, rate-limited/not-found bodies and oversized replies.
        return 'accepted' if response.status == 200 and response.read(3) == b'OK' else 'unconfirmed'
    except Exception:
        # Never expose an exception that could contain the capability URL.
        return 'unconfirmed'
    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass
