"use strict";
// One response display only. No local/session storage, clipboard, or retry.
function clearRecoveryCodes() {
    for (const field of document.querySelectorAll("[data-sensitive], input[name='code']")) {
        field.value = "";
        if (field.tagName === "TEXTAREA") field.textContent = "";
    }
}
function bindRecoveryForms() {
    for (const form of document.querySelectorAll("form")) {
        const destination = new URL(form.getAttribute("action"), location.origin);
        if (destination.origin !== location.origin ||
            !/^\/account\/security\/recovery\/(start|verify|confirm)$/.test(destination.pathname)) continue;
        form.querySelector("button").disabled = false;
        form.addEventListener("submit", async event => {
            event.preventDefault();
            if (document.body.dataset.submitting === "true") return;
            document.body.dataset.submitting = "true";
            for (const button of document.querySelectorAll("button")) button.disabled = true;
            const payload = Object.fromEntries(new FormData(form));
            clearRecoveryCodes();
            const controller = new AbortController();
            const timer = setTimeout(() => controller.abort(), 25000);
            try {
                const response = await fetch(destination.href, {
                    method: "POST", mode: "cors", credentials: "same-origin", redirect: "error",
                    referrerPolicy: "no-referrer", cache: "no-store", signal: controller.signal,
                    headers: {"Content-Type": "application/json"}, body: JSON.stringify(payload)
                });
                if (!response.headers.get("Content-Type")?.startsWith("text/html")) throw new Error();
                const page = new DOMParser().parseFromString(await response.text(), "text/html");
                document.body.replaceWith(page.body);
                history.replaceState(null, "", "/account/security/recovery");
                bindRecoveryForms();
                document.querySelector("h1")?.focus();
            } catch (_) {
                const main = document.createElement("main");
                const message = document.createElement("p");
                message.setAttribute("role", "status");
                message.tabIndex = -1;
                message.textContent = "Recovery codes could not be confirmed. Do not repeat this action. Contact support to check its outcome.";
                main.append(message);
                document.body.replaceChildren(main);
                message.focus();
            } finally {
                payload.code = "";
                clearTimeout(timer);
            }
        });
    }
}
window.addEventListener("pagehide", clearRecoveryCodes);
document.addEventListener("DOMContentLoaded", bindRecoveryForms);
