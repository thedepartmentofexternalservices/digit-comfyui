import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

const PLACEHOLDER_RE = /^\(no .+ found\)$/;
const STORAGE_UNAVAILABLE = "(storage unavailable)";
const RETRY_MS = [2000, 5000, 10000];

function isPlaceholder(value) {
    return typeof value === "string" && PLACEHOLDER_RE.test(value);
}

function isUsableName(value) {
    return Boolean(value) && !isPlaceholder(value) && value !== STORAGE_UNAVAILABLE;
}

function isSentinelList(items) {
    if (!Array.isArray(items) || items.length === 0) return true;
    if (items.length === 1 && (isPlaceholder(items[0]) || items[0] === STORAGE_UNAVAILABLE || items[0] === "")) {
        return true;
    }
    return false;
}

function keepValueInOptions(widget, incoming, keepCurrent = true) {
    if (!widget || !widget.options) return;
    const current = widget.value;
    const values = Array.isArray(incoming) ? incoming.filter((name) => isUsableName(name)) : [];
    if (keepCurrent && isUsableName(current) && !values.includes(current)) {
        values.unshift(current);
    }
    const next = values.length ? values : (keepCurrent && isUsableName(current) ? [current] : [""]);
    widget.options.values = next;
    if (!keepCurrent && isUsableName(current) && !next.includes(current)) {
        widget.value = next[0] || "";
    }
}

function notify(message, isError) {
    const toast = app.extensionManager && app.extensionManager.toast;
    if (toast && toast.add) {
        toast.add({
            severity: isError ? "error" : "info",
            summary: message,
            life: 4000,
        });
        return;
    }
    if (isError) console.warn("[DIGIT]", message);
}

app.registerExtension({
    name: "DIGIT.ImageSaver",

    async nodeCreated(node) {
        const hasShotNodes = ["DigitImageSaver", "DigitImageLoader", "DigitVideoSaver"];
        const projectOnlyNodes = ["DigitSRTMaker"];

        const isHasShotNode = hasShotNodes.includes(node.comfyClass);
        const isProjectOnlyNode = projectOnlyNodes.includes(node.comfyClass);

        if (!isHasShotNode && !isProjectOnlyNode) return;

        const rootWidget = node.widgets.find(w => w.name === "projekts_root");
        const projectWidget = node.widgets.find(w => w.name === "project");
        const shotWidget = node.widgets.find(w => w.name === "shot");
        const folderWidget = node.widgets.find(w => w.name === "folder");
        const leftoverSubfolder = node.widgets.find(w => w.name === "subfolder");
        const leftoverTask = node.widgets.find(w => w.name === "task");

        if (!rootWidget || !projectWidget) return;
        if (isHasShotNode && !shotWidget) return;

        function hideWidget(widget) {
            if (!widget) return;
            widget.hidden = true;
            widget.computeSize = () => [0, -4];
        }
        hideWidget(leftoverSubfolder);
        hideWidget(leftoverTask);

        let refreshGen = 0;
        let retryIndex = 0;
        let retryTimer = null;
        let sawConfigure = false;

        function clearRetry() {
            retryIndex = 0;
            if (retryTimer) {
                clearTimeout(retryTimer);
                retryTimer = null;
            }
        }

        function scheduleRetry(reason) {
            if (retryIndex >= RETRY_MS.length) {
                notify(`${reason}. Click Refresh.`, true);
                return;
            }
            const delay = RETRY_MS[retryIndex];
            retryIndex += 1;
            retryTimer = setTimeout(() => {
                refreshAll();
            }, delay);
        }

        async function fetchJson(url) {
            const resp = await api.fetchApi(url);
            if (resp.status !== 200) {
                const err = new Error(`${resp.status}`);
                err.status = resp.status;
                throw err;
            }
            return resp.json();
        }

        async function refreshRoots(gen) {
            const roots = await fetchJson("/digit/roots");
            if (gen !== refreshGen) return;
            if (!Array.isArray(roots) || roots.length === 0) {
                throw new Error("no PROJEKTS roots");
            }
            keepValueInOptions(rootWidget, roots);
        }

        async function refreshProjects(gen) {
            const root = rootWidget.value;
            if (!root) {
                keepValueInOptions(projectWidget, []);
                return "";
            }
            const projects = await fetchJson(`/digit/projects?root=${encodeURIComponent(root)}`);
            if (gen !== refreshGen) return "";
            if (isSentinelList(projects)) {
                keepValueInOptions(projectWidget, []);
                return "No projects in this root.";
            }
            keepValueInOptions(projectWidget, projects);
            return "";
        }

        async function refreshShots(gen, opts = {}) {
            if (!shotWidget) return "";
            const resetIfMissing = Boolean(opts.resetIfMissing);
            const root = opts.root !== undefined ? opts.root : rootWidget.value;
            const project = opts.project !== undefined ? opts.project : projectWidget.value;
            if (!root || !isUsableName(project)) {
                keepValueInOptions(shotWidget, [], false);
                if (resetIfMissing) shotWidget.value = "";
                return "";
            }
            const shots = await fetchJson(
                `/digit/shots?root=${encodeURIComponent(root)}&project=${encodeURIComponent(project)}`
            );
            if (gen !== refreshGen) return "";
            if (isSentinelList(shots)) {
                keepValueInOptions(shotWidget, [], false);
                if (resetIfMissing) shotWidget.value = "";
                const saved = shotWidget.value;
                if (!resetIfMissing && isUsableName(saved)) {
                    return `Saved shot ${saved} is not in this project.`;
                }
                return "No shots yet. Click + Shot.";
            }
            keepValueInOptions(shotWidget, shots, !resetIfMissing);
            if (resetIfMissing && !shots.includes(shotWidget.value)) {
                shotWidget.value = shots[0] || "";
            }
            node.setDirtyCanvas(true);
            return "";
        }

        function seedFolders(incoming) {
            const values = Array.isArray(incoming) ? incoming.filter((name) => isUsableName(name)) : [];
            if (!values.includes("comfy/comp")) values.unshift("comfy/comp");
            return values;
        }

        async function refreshFolders(gen, opts = {}) {
            if (!folderWidget) return "";
            const resetIfMissing = Boolean(opts.resetIfMissing);
            const root = opts.root !== undefined ? opts.root : rootWidget.value;
            const project = opts.project !== undefined ? opts.project : projectWidget.value;
            const shot = opts.shot !== undefined ? opts.shot : shotWidget.value;
            if (!root || !isUsableName(project) || !isUsableName(shot)) {
                keepValueInOptions(folderWidget, ["comfy/comp"], false);
                folderWidget.value = "comfy/comp";
                return "";
            }
            const folders = await fetchJson(
                `/digit/folders?root=${encodeURIComponent(root)}&project=${encodeURIComponent(project)}&shot=${encodeURIComponent(shot)}`
            );
            if (gen !== refreshGen) return "";
            const usable = isSentinelList(folders) ? ["comfy/comp"] : seedFolders(folders);
            keepValueInOptions(folderWidget, usable, !resetIfMissing);
            if (resetIfMissing && !usable.includes(folderWidget.value)) {
                folderWidget.value = "comfy/comp";
            }
            if (!isUsableName(folderWidget.value)) {
                folderWidget.value = "comfy/comp";
            }
            node.setDirtyCanvas(true);
            return "";
        }

        async function refreshAll() {
            const gen = ++refreshGen;
            try {
                await refreshRoots(gen);
                if (gen !== refreshGen) return;
                const projectWarning = await refreshProjects(gen);
                if (gen !== refreshGen) return;
                let shotWarning = "";
                if (isHasShotNode) {
                    shotWarning = await refreshShots(gen);
                    if (gen !== refreshGen) return;
                    await refreshFolders(gen);
                    if (gen !== refreshGen) return;
                }
                if (gen !== refreshGen) return;
                if (projectWarning || shotWarning) notify(shotWarning || projectWarning, true);
                clearRetry();
            } catch (err) {
                if (gen !== refreshGen) return;
                const reason = err && err.status
                    ? `Refresh failed (${err.status})`
                    : "Refresh failed";
                scheduleRetry(reason);
            }
        }

        async function onRootChanged(value) {
            if (value !== undefined) rootWidget.value = value;
            clearRetry();
            const gen = ++refreshGen;
            try {
                const projectWarning = await refreshProjects(gen);
                if (gen !== refreshGen) return;
                let shotWarning = "";
                if (isHasShotNode) {
                    shotWarning = await refreshShots(gen, { resetIfMissing: true });
                    if (gen !== refreshGen) return;
                    await refreshFolders(gen, { resetIfMissing: true });
                }
                if (gen !== refreshGen) return;
                if (projectWarning || shotWarning) notify(shotWarning || projectWarning, true);
            } catch (err) {
                if (gen !== refreshGen) return;
                scheduleRetry(err && err.status ? `Refresh failed (${err.status})` : "Refresh failed");
            }
        }

        async function onProjectChanged(value) {
            if (value !== undefined) projectWidget.value = value;
            if (!isHasShotNode) return;
            clearRetry();
            const gen = ++refreshGen;
            try {
                const warning = await refreshShots(gen, {
                    project: projectWidget.value,
                    resetIfMissing: true,
                });
                if (gen !== refreshGen) return;
                await refreshFolders(gen, { resetIfMissing: true });
                if (gen !== refreshGen) return;
                if (warning) notify(warning, true);
            } catch (err) {
                if (gen !== refreshGen) return;
                scheduleRetry(err && err.status ? `Refresh failed (${err.status})` : "Refresh failed");
            }
        }

        const origRootCallback = rootWidget.callback;
        rootWidget.callback = async function(value) {
            if (origRootCallback) origRootCallback.call(this, value);
            await onRootChanged(value);
        };

        const origProjectCallback = projectWidget.callback;
        projectWidget.callback = async function(value) {
            if (origProjectCallback) origProjectCallback.call(this, value);
            await onProjectChanged(value);
        };

        async function onShotChanged(value) {
            if (!isHasShotNode) return;
            if (value !== undefined) shotWidget.value = value;
            clearRetry();
            const gen = ++refreshGen;
            try {
                await refreshFolders(gen, { shot: shotWidget.value, resetIfMissing: true });
            } catch (err) {
                if (gen !== refreshGen) return;
                scheduleRetry(err && err.status ? `Refresh failed (${err.status})` : "Refresh failed");
            }
        }

        const origOnWidgetChanged = node.onWidgetChanged;
        node.onWidgetChanged = function(name, value, oldValue) {
            if (origOnWidgetChanged) origOnWidgetChanged.apply(this, arguments);
            if (value === oldValue) return;
            if (name === "projekts_root") onRootChanged(value);
            if (name === "project") onProjectChanged(value);
            if (name === "shot") onShotChanged(value);
        };

        if (shotWidget) {
            const origShotCallback = shotWidget.callback;
            shotWidget.callback = async function(value) {
                if (origShotCallback) origShotCallback.call(this, value);
                await onShotChanged(value);
            };
        }

        if (isHasShotNode) {
            node.addWidget("button", "create_shot", "+ Shot", async () => {
                const root = rootWidget.value;
                const project = projectWidget.value;
                if (!root || !isUsableName(project)) {
                    notify("Pick a project first.", true);
                    return;
                }
                const typed = window.prompt("New shot name", "");
                if (typed == null) return;
                const shot = typed.trim();
                if (!isUsableName(shot)) {
                    notify("Type a shot name.", true);
                    return;
                }
                try {
                    const resp = await api.fetchApi("/digit/create_shot", {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({
                            root,
                            project,
                            shot,
                            folder: folderWidget && isUsableName(folderWidget.value)
                                ? folderWidget.value
                                : "comfy/comp",
                        }),
                    });
                    const payload = await resp.json();
                    if (resp.status !== 200) {
                        notify((payload && payload.error) || `+ Shot failed (${resp.status})`, true);
                        return;
                    }
                    const created = payload.shot || shot;
                    keepValueInOptions(shotWidget, payload.shots || [created], false);
                    shotWidget.value = created;
                    const gen = ++refreshGen;
                    await refreshFolders(gen, { shot: created });
                    node.setDirtyCanvas(true);
                    notify(`Created ${created}`);
                } catch (_err) {
                    notify("+ Shot failed. Click Refresh.", true);
                }
            });

            node.addWidget("button", "create_folder", "+ Folder", async () => {
                const root = rootWidget.value;
                const project = projectWidget.value;
                const shot = shotWidget.value;
                if (!root || !isUsableName(project) || !isUsableName(shot)) {
                    notify("Pick a project and shot first.", true);
                    return;
                }
                const typed = window.prompt(
                    "Folder (e.g. comfy/comp, comfy/comp/v001, or plates)",
                    folderWidget && folderWidget.value ? folderWidget.value : "comfy/comp"
                );
                if (typed == null) return;
                const folder = typed.trim().replace(/\\/g, "/").replace(/^\/+|\/+$/g, "");
                if (!folder) {
                    notify("Type a folder path.", true);
                    return;
                }
                try {
                    const resp = await api.fetchApi("/digit/create_folder", {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({ root, project, shot, folder }),
                    });
                    const payload = await resp.json();
                    if (resp.status !== 200) {
                        notify((payload && payload.error) || `+ Folder failed (${resp.status})`, true);
                        return;
                    }
                    const created = payload.folder || folder;
                    if (folderWidget) {
                        keepValueInOptions(folderWidget, seedFolders(payload.folders || [created]), false);
                        folderWidget.value = created;
                    }
                    node.setDirtyCanvas(true);
                    notify(`Created ${created}`);
                } catch (_err) {
                    notify("+ Folder failed. Click Refresh.", true);
                }
            });
        }

        node.addWidget("button", "refresh_projekts", "Refresh", () => {
            clearRetry();
            refreshAll();
        });

        const origOnConfigure = node.onConfigure;
        node.onConfigure = function(info) {
            if (origOnConfigure) origOnConfigure.apply(this, arguments);
            sawConfigure = true;
            clearRetry();
            refreshAll();
        };

        queueMicrotask(() => {
            if (!sawConfigure) refreshAll();
        });
    }
});
