"use strict";
document.addEventListener("DOMContentLoaded", async () => {
    const authorization = document.querySelector('meta[name="authorization-url"]');
    if (authorization) {
        const destination = new URL(authorization.content);
        if (destination.origin === "https://api.workos.com" && destination.pathname === "/user_management/authorize") {
            window.location.replace(destination.href);
        }
        return;
    }
    if (document.body.dataset.complete !== "true") return;
    const query = new URLSearchParams(window.location.search);
    const payload = {
        csrf: document.querySelector('meta[name="csrf-token"]').content,
        code: query.get("code") || "", state: query.get("state") || "", error: query.get("error") || ""
    };
    // Remove callback credentials before a fetch, timer, link or result render.
    history.replaceState(null, "", "/auth/complete");
    const started = performance.now();
    const timer = setInterval(() => {
        const seconds = Math.floor((performance.now() - started) / 1000);
        document.getElementById("auth-elapsed").textContent = `${String(Math.floor(seconds / 60)).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
    }, 1000);
    const controller = new AbortController();
    let cancelled = false;
    document.getElementById("auth-cancel").addEventListener("click", async () => {
        cancelled = true;
        controller.abort();
        try {
            const response = await fetch("/auth/cancel", {
                method: "POST", credentials: "same-origin", cache: "no-store",
                headers: { "Content-Type": "application/json" }, body: JSON.stringify({ csrf: payload.csrf })
            });
            if (!response.ok) throw new Error("incomplete");
            window.location.replace("/auth/result");
        } catch (_) {
            document.getElementById("auth-progress").textContent = "Cancellation could not be confirmed. This attempt will expire automatically; it will not be retried.";
        }
    }, { once: true });
    const timeout = setTimeout(() => controller.abort(), 25000);
    try {
        const response = await fetch("/auth/complete", {
            method: "POST", credentials: "same-origin", cache: "no-store",
            headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload), signal: controller.signal
        });
        if (!response.ok) throw new Error("incomplete");
        const result = await response.json();
        if (result.redirect !== "/auth/result") throw new Error("incomplete");
        if (!cancelled) window.location.replace("/auth/result");
    } catch (_) {
        document.getElementById("auth-progress").textContent = "Sign-in could not be confirmed. If registration completed, try signing in. This attempt will not be retried automatically.";
    } finally {
        payload.code = ""; payload.state = "";
        clearInterval(timer); clearTimeout(timeout);
    }
});
