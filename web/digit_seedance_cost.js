import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

// Live cost strip for DIGIT Seedance Video. Watches provider, model,
// resolution, duration, batch_count, muapi_route, and connected inputs, then
// asks /digit/seedance/estimate for the total and renders a two-line summary:
//
//   Muapi · reduced filter · mini-spicy 720p
//   Est. $3.00  (4 clips × 5s)

const WATCHED_WIDGETS = [
    "provider",
    "model",
    "resolution",
    "duration",
    "batch_count",
    "muapi_route",
];

function detectMode(node) {
    const linked = (name) => {
        const input = (node.inputs || []).find((i) => i.name === name);
        return input && input.link != null;
    };
    const anyRef = (node.inputs || []).some(
        (i) =>
            /^reference_(image|video|audio)\d+$/.test(i.name) && i.link != null
    );
    if (anyRef) return "reference_to_video";
    if (linked("first_frame") && linked("last_frame")) return "first_last_frame";
    if (linked("first_frame")) return "image_to_video";
    return "text_to_video";
}

function hasVideoRefs(node) {
    return (node.inputs || []).some(
        (i) => /^reference_video\d+$/.test(i.name) && i.link != null
    );
}

function widgetValue(node, name) {
    const w = (node.widgets || []).find((w) => w.name === name);
    return w ? w.value : undefined;
}

function titleCase(name) {
    return name ? name.charAt(0).toUpperCase() + name.slice(1) : "";
}

function shortRoute(summary) {
    if (summary.provider === "muapi") {
        // seedance-2-mini-spicy-text-to-video -> mini-spicy
        const m = summary.route.match(/seedance-2-(mini-spicy|mini|spicy|vip)/);
        const tier = m ? m[1] : "global";
        return `${tier}`;
    }
    return summary.route;
}

function formatMoney(value) {
    return value == null ? "?" : `$${value.toFixed(2)}`;
}

function renderSummary(data, node) {
    const resolution = widgetValue(node, "resolution") || "";
    if (data.range) {
        const low = data.low;
        const high = data.high;
        const line1 = `${titleCase(low.provider)} · ${low.filter} · ${shortRoute(low)} ${resolution}`;
        const line2 =
            low.total == null || high.total == null
                ? `Est. n/a — ${low.note || high.note || "no published price"}`
                : `Est. ${formatMoney(low.total)}–${formatMoney(high.total)}  (${low.clips} clip${low.clips > 1 ? "s" : ""} × 4–15s auto)`;
        return [line1, line2];
    }
    const s = data.summary;
    const line1 = `${titleCase(s.provider)} · ${s.filter} · ${shortRoute(s)} ${resolution}`;
    let line2;
    if (s.total == null) {
        line2 = `Est. n/a — ${s.note || "no published price"}`;
    } else {
        line2 = `Est. ${formatMoney(s.total)}  (${s.clips} clip${s.clips > 1 ? "s" : ""} × ${s.duration}s)`;
        if (s.note) line2 += ` — ${s.note}`;
    }
    return [line1, line2];
}

app.registerExtension({
    name: "DIGIT.SeedanceCost",

    async nodeCreated(node) {
        if (node.comfyClass !== "DigitDanceVideo") return;

        const strip = node.addWidget("text", "cost_estimate", "", () => {}, {
            multiline: true,
            serialize: false,
        });
        if (strip.inputEl) {
            strip.inputEl.readOnly = true;
            strip.inputEl.rows = 2;
            strip.inputEl.style.fontFamily = "monospace";
            strip.inputEl.style.fontSize = "11px";
            strip.inputEl.style.color = "#a3e635";
        }

        let debounceTimer = null;
        let requestCounter = 0;

        const refresh = () => {
            clearTimeout(debounceTimer);
            debounceTimer = setTimeout(async () => {
                const requestId = ++requestCounter;
                const body = {
                    provider: widgetValue(node, "provider") || "fal",
                    model: widgetValue(node, "model") || "seedance-2.0",
                    resolution: widgetValue(node, "resolution") || "720p",
                    duration: String(widgetValue(node, "duration") ?? "5"),
                    batch_count: widgetValue(node, "batch_count") || 1,
                    muapi_route: widgetValue(node, "muapi_route") || "auto",
                    mode: detectMode(node),
                    has_video_refs: hasVideoRefs(node),
                };
                try {
                    const response = await api.fetchApi("/digit/seedance/estimate", {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify(body),
                    });
                    if (requestId !== requestCounter) return; // stale response
                    const data = await response.json();
                    strip.value = renderSummary(data, node).join("\n");
                } catch (error) {
                    if (requestId !== requestCounter) return;
                    strip.value = "Cost estimate unavailable";
                }
                node.setDirtyCanvas(true, false);
            }, 250);
        };

        // Re-estimate when a watched widget changes.
        for (const name of WATCHED_WIDGETS) {
            const widget = (node.widgets || []).find((w) => w.name === name);
            if (!widget) continue;
            const original = widget.callback;
            widget.callback = function (...args) {
                const result = original ? original.apply(this, args) : undefined;
                refresh();
                return result;
            };
        }

        // Re-estimate when connections change (mode detection).
        const onConnectionsChange = node.onConnectionsChange;
        node.onConnectionsChange = function (...args) {
            if (onConnectionsChange) onConnectionsChange.apply(this, args);
            refresh();
        };

        // First estimate once the node settles.
        setTimeout(refresh, 100);
    },
});
