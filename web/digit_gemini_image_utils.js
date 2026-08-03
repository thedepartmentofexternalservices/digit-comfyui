/** Shared UI helpers for DIGIT Gemini image nodes. */

export const MODELS_1K_ONLY = new Set(["gemini-3.1-flash-lite-image"]);
export const RESOLUTIONS_ALL = ["1K", "2K", "4K"];
export const RESOLUTIONS_1K_ONLY = ["1K"];

const DEPRECATED_MODEL_ALIASES = {
    "gemini-3-pro-image-preview": "gemini-3-pro-image",
    "gemini-3.1-flash-image-preview": "gemini-3.1-flash-image",
};

export function resolveGeminiImageModel(model) {
    return DEPRECATED_MODEL_ALIASES[model] ?? model;
}

/**
 * Restrict the resolution dropdown when Nano Banana 2 Lite is selected (1K only).
 * @param {object} node - ComfyUI node instance
 * @param {string} modelWidgetName - Widget name for the image model combo
 */
export function setupGeminiImageResolutionFilter(node, modelWidgetName = "model") {
    const modelWidget = node.widgets.find((w) => w.name === modelWidgetName);
    const resolutionWidget = node.widgets.find((w) => w.name === "resolution");
    if (!modelWidget || !resolutionWidget) return;

    function updateResolutionOptions() {
        const model = resolveGeminiImageModel(modelWidget.value);
        const isLite = MODELS_1K_ONLY.has(model);
        const options = isLite ? RESOLUTIONS_1K_ONLY : RESOLUTIONS_ALL;
        resolutionWidget.options.values = options;
        if (!options.includes(resolutionWidget.value)) {
            resolutionWidget.value = "1K";
        }
        node.setDirtyCanvas(true);
    }

    const origCallback = modelWidget.callback;
    modelWidget.callback = function (value) {
        if (origCallback) origCallback.call(this, value);
        updateResolutionOptions();
    };

    updateResolutionOptions();
}
