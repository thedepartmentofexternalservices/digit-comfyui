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
    widget.options.values = values.length ? values : (keepCurrent && isUsableName(current) ? [current] : [""]);
    if (!keepCurrent && isUsableName(current) && !values.includes(current)) {
        widget.value = values[0] || "";
    }
}

app.registerExtension({
    name: "DIGIT.ImageSaver",

    async nodeCreated(node) {
        const hasShotNodes = ["DigitImageSaver", "DigitImageLoader", "DigitVideoSaver", "DigitUberSaver"];
        const projectOnlyNodes = ["DigitSRTMaker"];

        const isHasShotNode = hasShotNodes.includes(node.comfyClass);
        const isProjectOnlyNode = projectOnlyNodes.includes(node.comfyClass);

        if (!isHasShotNode && !isProjectOnlyNode) return;

        const rootWidget = node.widgets.find(w => w.name === "projekts_root");
        const projectWidget = node.widgets.find(w => w.name === "project");
        const shotWidget = node.widgets.find(w => w.name === "shot");
        const subfolderWidget = node.widgets.find(w => w.name === "subfolder");
        const taskWidget = node.widgets.find(w => w.name === "task");
        const folderWidget = node.widgets.find(w => w.name === "folder");
        const isUberSaver = node.comfyClass === "DigitUberSaver";

        if (!rootWidget || !projectWidget) return;
        if (isHasShotNode && !shotWidget) return;

        const filepathWidget = node.addWidget("text", "filepath_display", "", () => {}, {
            serialize: false,
        });
        filepathWidget.inputEl && (filepathWidget.inputEl.readOnly = true);

        const statusWidget = node.addWidget("text", "projekts_status", "", () => {}, {
            serialize: false,
        });
        statusWidget.inputEl && (statusWidget.inputEl.readOnly = true);

        function addPick(sourceWidget, name) {
            if (!sourceWidget) return null;
            const pick = node.addWidget("combo", name, sourceWidget.value || "", (value) => {
                sourceWidget.value = value;
                node.setDirtyCanvas(true);
                if (sourceWidget.callback) sourceWidget.callback(value);
            }, { values: [sourceWidget.value || ""], serialize: false });
            return pick;
        }

        const shotPick = isHasShotNode ? addPick(shotWidget, "shot_pick") : null;
        const subfolderPick = isHasShotNode ? addPick(subfolderWidget, "subfolder_pick") : null;
        const taskPick = isHasShotNode ? addPick(taskWidget, "task_pick") : null;

        const onExecuted = node.onExecuted;
        node.onExecuted = function(data) {
            if (onExecuted) onExecuted.call(this, data);
            if (data && data.filepath_text && data.filepath_text.length > 0) {
                filepathWidget.value = data.filepath_text[0];
            }
        };

        let refreshGen = 0;
        let retryIndex = 0;
        let retryTimer = null;
        let sawConfigure = false;

        function setStatus(message) {
            statusWidget.value = message || "";
            node.setDirtyCanvas(true);
        }

        function clearRetry() {
            retryIndex = 0;
            if (retryTimer) {
                clearTimeout(retryTimer);
                retryTimer = null;
            }
        }

        function scheduleRetry(reason) {
            if (retryIndex >= RETRY_MS.length) {
                setStatus(`${reason}. Click Refresh PROJEKTS.`);
                return;
            }
            const delay = RETRY_MS[retryIndex];
            retryIndex += 1;
            setStatus(`${reason}. Retrying in ${delay / 1000}s…`);
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

        function shotCombo() {
            return shotPick || (shotWidget && shotWidget.options ? shotWidget : null);
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
                return "No projects in this root. Refresh PROJEKTS to retry.";
            }
            keepValueInOptions(projectWidget, projects);
            return "";
        }

        async function refreshShots(gen, opts = {}) {
            if (!shotWidget) return "";
            const resetIfMissing = Boolean(opts.resetIfMissing);
            const root = opts.root !== undefined ? opts.root : rootWidget.value;
            const project = opts.project !== undefined ? opts.project : projectWidget.value;
            const target = shotCombo();
            if (!root || !isUsableName(project)) {
                keepValueInOptions(target, [], false);
                if (resetIfMissing) shotWidget.value = "";
                return "";
            }
            const shots = await fetchJson(
                `/digit/shots?root=${encodeURIComponent(root)}&project=${encodeURIComponent(project)}`
            );
            if (gen !== refreshGen) return "";
            if (isSentinelList(shots)) {
                keepValueInOptions(target, [], false);
                if (resetIfMissing) {
                    shotWidget.value = "";
                    if (target) target.value = "";
                }
                const saved = shotWidget.value;
                if (!resetIfMissing && isUsableName(saved)) {
                    return `Saved shot ${saved} not in current list — project has no shots. Create shot or Refresh.`;
                }
                return "No shots in this project. Type a name and click Create shot.";
            }
            keepValueInOptions(target, shots, !resetIfMissing);
            if (resetIfMissing && !shots.includes(shotWidget.value)) {
                shotWidget.value = shots[0] || "";
                if (target) target.value = shotWidget.value;
            } else if (target && isUsableName(shotWidget.value)) {
                target.value = shotWidget.value;
            }
            const saved = shotWidget.value;
            if (!resetIfMissing && isUsableName(saved) && !shots.includes(saved)) {
                return `Saved shot ${saved} not in current list. Create shot or Refresh.`;
            }
            node.setDirtyCanvas(true);
            return "";
        }

        async function refreshSubfolders(gen) {
            if (!subfolderWidget || !shotWidget) return;
            const root = rootWidget.value;
            const project = projectWidget.value;
            const shot = shotWidget.value;
            if (!root || !isUsableName(project) || !isUsableName(shot)) return;
            const items = await fetchJson(
                `/digit/subfolders?root=${encodeURIComponent(root)}&project=${encodeURIComponent(project)}&shot=${encodeURIComponent(shot)}`
            );
            if (gen !== refreshGen) return;
            const target = subfolderPick || (subfolderWidget && subfolderWidget.options ? subfolderWidget : null);
            if (target) keepValueInOptions(target, isSentinelList(items) ? [] : items);
        }

        async function refreshTasks(gen) {
            if (!taskWidget || !shotWidget || !subfolderWidget) return;
            const root = rootWidget.value;
            const project = projectWidget.value;
            const shot = shotWidget.value;
            const subfolder = subfolderWidget.value;
            if (!root || !isUsableName(project) || !isUsableName(shot) || !isUsableName(subfolder)) return;
            const items = await fetchJson(
                `/digit/tasks?root=${encodeURIComponent(root)}&project=${encodeURIComponent(project)}&shot=${encodeURIComponent(shot)}&subfolder=${encodeURIComponent(subfolder)}`
            );
            if (gen !== refreshGen) return;
            const target = taskPick || (taskWidget && taskWidget.options ? taskWidget : null);
            if (target) keepValueInOptions(target, isSentinelList(items) ? [] : items);
        }

        function seedFolders(items) {
            const values = Array.isArray(items) ? items.filter((name) => isUsableName(name)) : [];
            if (!values.includes("comfy/comp")) values.unshift("comfy/comp");
            return values;
        }

        async function refreshFolders(gen) {
            if (!folderWidget || !shotWidget) return;
            const root = rootWidget.value;
            const project = projectWidget.value;
            const shot = shotWidget.value;
            if (!root || !isUsableName(project) || !isUsableName(shot)) {
                keepValueInOptions(folderWidget, ["comfy/comp"]);
                return;
            }
            const items = await fetchJson(
                `/digit/folders?root=${encodeURIComponent(root)}&project=${encodeURIComponent(project)}&shot=${encodeURIComponent(shot)}`
            );
            if (gen !== refreshGen) return;
            keepValueInOptions(folderWidget, seedFolders(items));
            if (!isUsableName(folderWidget.value)) {
                folderWidget.value = "comfy/comp";
            }
        }

        async function refreshDestination(gen) {
            if (folderWidget) {
                await refreshFolders(gen);
                return;
            }
            await refreshSubfolders(gen);
            if (gen !== refreshGen) return;
            await refreshTasks(gen);
        }

        async function refreshHealth(gen) {
            try {
                const resp = await api.fetchApi("/digit/health");
                if (gen !== refreshGen) return null;
                const payload = await resp.json();
                if (!payload || !payload.ok) {
                    return "PROJEKTS storage degraded. Refresh to retry.";
                }
                const count = (payload.roots || []).reduce((sum, item) => sum + (item.project_count || 0), 0);
                return `PROJEKTS OK — ${count} project${count === 1 ? "" : "s"}`;
            } catch (_err) {
                return null;
            }
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
                    await refreshDestination(gen);
                    if (gen !== refreshGen) return;
                }
                const health = await refreshHealth(gen);
                if (gen !== refreshGen) return;
                setStatus(shotWarning || projectWarning || health || "");
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
                    await refreshDestination(gen);
                }
                if (gen !== refreshGen) return;
                setStatus(shotWarning || projectWarning);
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
                await refreshDestination(gen);
                if (gen !== refreshGen) return;
                setStatus(warning);
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

        const origOnWidgetChanged = node.onWidgetChanged;
        node.onWidgetChanged = function(name, value, oldValue) {
            if (origOnWidgetChanged) origOnWidgetChanged.apply(this, arguments);
            if (value === oldValue) return;
            if (name === "projekts_root") onRootChanged(value);
            if (name === "project") onProjectChanged(value);
        };

        if (shotWidget) {
            const origShotCallback = shotWidget.callback;
            shotWidget.callback = async function(value) {
                if (origShotCallback) origShotCallback.call(this, value);
                if (value !== undefined) shotWidget.value = value;
                if (shotPick && isUsableName(value)) shotPick.value = value;
                clearRetry();
                const gen = ++refreshGen;
                try {
                    await refreshDestination(gen);
                } catch (err) {
                    if (gen !== refreshGen) return;
                    scheduleRetry(err && err.status ? `Refresh failed (${err.status})` : "Refresh failed");
                }
            };
        }

        if (subfolderWidget) {
            const origSubfolderCallback = subfolderWidget.callback;
            subfolderWidget.callback = async function(value) {
                if (origSubfolderCallback) origSubfolderCallback.call(this, value);
                clearRetry();
                const gen = ++refreshGen;
                try {
                    await refreshTasks(gen);
                } catch (err) {
                    if (gen !== refreshGen) return;
                    scheduleRetry(err && err.status ? `Refresh failed (${err.status})` : "Refresh failed");
                }
            };
        }

        if (isHasShotNode) {
            node.addWidget("button", "create_shot", "Create shot", async () => {
                const root = rootWidget.value;
                const project = projectWidget.value;
                const shot = (shotWidget.value || "").trim();
                if (!root || !isUsableName(project)) {
                    setStatus("Pick a project first.");
                    return;
                }
                if (!isUsableName(shot)) {
                    setStatus("Type a shot name first.");
                    return;
                }
                setStatus(`Creating shot ${shot}…`);
                try {
                    const resp = await api.fetchApi("/digit/create_shot", {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({
                            root,
                            project,
                            shot,
                            subfolder: subfolderWidget ? subfolderWidget.value : "",
                            task: taskWidget ? taskWidget.value : "",
                        }),
                    });
                    const payload = await resp.json();
                    if (resp.status !== 200) {
                        setStatus((payload && payload.error) || `Create shot failed (${resp.status})`);
                        return;
                    }
                    shotWidget.value = payload.shot || shot;
                    keepValueInOptions(shotCombo(), payload.shots || [shotWidget.value], false);
                    if (shotPick) shotPick.value = shotWidget.value;
                    const gen = ++refreshGen;
                    await refreshDestination(gen);
                    if (gen !== refreshGen) return;
                    setStatus(`Created shot ${shotWidget.value}`);
                    node.setDirtyCanvas(true);
                } catch (_err) {
                    setStatus("Create shot failed. Refresh PROJEKTS to retry.");
                }
            });
        }

        if (isUberSaver && folderWidget) {
            node.addWidget("button", "create_folder", "+ Folder", async () => {
                const root = rootWidget.value;
                const project = projectWidget.value;
                const shot = shotWidget && shotWidget.value;
                if (!root || !isUsableName(project) || !isUsableName(shot)) {
                    setStatus("Pick a project and shot first.");
                    return;
                }
                const typed = window.prompt(
                    "Folder under this shot (example: comfy/comp/v001)",
                    folderWidget.value || "comfy/comp"
                );
                if (typed === null) return;
                const folder = typed.trim().replace(/\\/g, "/").replace(/^\/+|\/+$/g, "");
                if (!folder) {
                    setStatus("Type a folder path.");
                    return;
                }
                setStatus(`Creating ${folder}…`);
                try {
                    const resp = await api.fetchApi("/digit/create_folder", {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({ root, project, shot, folder }),
                    });
                    const payload = await resp.json();
                    if (resp.status !== 200) {
                        setStatus((payload && payload.error) || `Create folder failed (${resp.status})`);
                        return;
                    }
                    const created = payload.folder || folder;
                    keepValueInOptions(folderWidget, seedFolders(payload.folders || [created]), false);
                    folderWidget.value = created;
                    setStatus(`Created ${created}`);
                    node.setDirtyCanvas(true);
                } catch (_err) {
                    setStatus("Create folder failed. Refresh PROJEKTS to retry.");
                }
            });
        }

        node.addWidget("button", "refresh_projekts", "Refresh PROJEKTS", () => {
            clearRetry();
            setStatus("Refreshing…");
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
