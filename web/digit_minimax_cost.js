import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

// Live cost strip for DIGIT MiniMax Video. Watches provider, resolution,
// duration, batch_count, and connected inputs, then asks
// /digit/minimax/estimate for the total and renders a two-line summary:
//
//   Fal · strict filter · minimax/h3/text-to-video 2K
//   Est. $2.60  (4 clips × 5s)

const WATCHED_WIDGETS = [
    "provider",
    "resolution",
    "duration",
    "batch_count",
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

function widgetValue(node, name) {
    const w = (node.widgets || []).find((w) => w.name === name);
    return w ? w.value : undefined;
}

function titleCase(name) {
    return name ? name.charAt(0).toUpperCase() + name.slice(1) : "";
}

function shortRoute(summary) {
    const route = summary.route || "";
    if (summary.provider === "muapi") {
        return route.replace(/^minimax-h3-/, "") || "h3";
    }
    return route.replace(/^minimax\/h3\//, "") || route;
}

function formatMoney(value) {
    return value == null ? "?" : `$${value.toFixed(2)}`;
}

function renderSummary(data, node) {
    const resolution = widgetValue(node, "resolution") || "";
    const s = data.summary;
    if (!s) {
        return ["Cost estimate unavailable"];
    }
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
    name: "DIGIT.MiniMaxCost",

    async nodeCreated(node) {
        if (node.comfyClass !== "DigitMiniMaxVideo") return;

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
                    resolution: widgetValue(node, "resolution") || "2K",
                    duration: String(widgetValue(node, "duration") ?? "5"),
                    batch_count: widgetValue(node, "batch_count") || 1,
                    mode: detectMode(node),
                };
                try {
                    const response = await api.fetchApi("/digit/minimax/estimate", {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify(body),
                    });
                    if (requestId !== requestCounter) return;
                    const data = await response.json();
                    strip.value = renderSummary(data, node).join("\n");
                } catch (error) {
                    if (requestId !== requestCounter) return;
                    strip.value = "Cost estimate unavailable";
                }
                node.setDirtyCanvas(true, false);
            }, 250);
        };

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

        const onConnectionsChange = node.onConnectionsChange;
        node.onConnectionsChange = function (...args) {
            if (onConnectionsChange) onConnectionsChange.apply(this, args);
            refresh();
        };

        setTimeout(refresh, 100);
    },
});
