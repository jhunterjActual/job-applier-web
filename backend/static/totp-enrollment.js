"use strict";
// Fetch's CORS mode preserves Origin with no-referrer. Never fall back to a
// native no-referrer POST (Origin:null) or retry an uncertain request.
function bindEnrollmentForms() {
    for (const form of document.querySelectorAll("form")) {
        const destination = new URL(form.getAttribute("action"), location.origin);
        if (destination.origin !== location.origin ||
            !/^\/account\/security\/(start|verify|check|close)$/.test(destination.pathname)) continue;
        form.querySelector("button").disabled = false;
        form.addEventListener("submit", async (event) => {
            event.preventDefault();
            if (document.body.dataset.submitting === "true") return;
            document.body.dataset.submitting = "true";
            for (const button of document.querySelectorAll("button")) button.disabled = true;
            const payload = Object.fromEntries(new FormData(form));
            const controller = new AbortController();
            const timer = setTimeout(() => controller.abort(), 25000);
            try {
                const response = await fetch(destination.href, {
                    method: "POST", mode: "cors", credentials: "same-origin", redirect: "error",
                    referrerPolicy: "no-referrer", cache: "no-store", signal: controller.signal,
                    headers: {"Content-Type": "application/json"}, body: JSON.stringify(payload)
                });
                if (!response.headers.get("Content-Type")?.startsWith("text/html")) throw new Error("unavailable");
                const page = new DOMParser().parseFromString(await response.text(), "text/html");
                document.body.replaceWith(page.body);
                history.replaceState(null, "", "/account/security");
                bindEnrollmentForms();
                document.querySelector("h1")?.focus();
            } catch (_) {
                // Clear setup keys/codes from the page on ambiguous transport.
                const main = document.createElement("main");
                const message = document.createElement("p");
                message.setAttribute("role", "status");
                message.tabIndex = -1;
                message.textContent = "Setup could not be confirmed. No retry was made. Reopen account security to check its status.";
                const link = document.createElement("a");
                link.href = "/account/security";
                link.textContent = "Reopen account security";
                main.append(message, link);
                document.body.replaceChildren(main);
                message.focus();
            } finally {
                payload.code = "";
                clearTimeout(timer);
            }
        });
    }
}
document.addEventListener("DOMContentLoaded", bindEnrollmentForms);
