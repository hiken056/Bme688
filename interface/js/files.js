document.addEventListener('DOMContentLoaded', () => {
    const list = document.getElementById('measurement-list');
    const summary = document.getElementById('files-summary');
    const empty = document.getElementById('files-empty');
    const loading = document.getElementById('files-loading');
    const locked = document.getElementById('files-locked');
    const search = document.getElementById('file-search');
    const selectionBar = document.getElementById('selection-bar');
    const selectionCount = document.getElementById('selection-count');
    const selectionClear = document.getElementById('selection-clear');
    const selectionDelete = document.getElementById('selection-delete');
    const selectionDownload = document.getElementById('selection-download');
    const notice = document.getElementById('files-notice');
    const deleteDialog = document.getElementById('delete-dialog');
    const deleteMessage = document.getElementById('delete-message');
    const deleteCancel = document.getElementById('delete-cancel');
    const deleteConfirm = document.getElementById('delete-confirm');

    let measurements = [];
    let filesLoaded = false;
    let measurementRunning = false;
    let downloadInProgress = false;
    let deleteInProgress = false;
    let noticeTimer = null;
    const selected = new Set();
    const openRuns = new Set();

    function escapeHtml(value) {
        return String(value)
            .replaceAll('&', '&amp;')
            .replaceAll('<', '&lt;')
            .replaceAll('>', '&gt;')
            .replaceAll('"', '&quot;')
            .replaceAll("'", '&#039;');
    }

    function runPaths(groupIndex, runIndex) {
        return measurements[groupIndex].runs[runIndex].files.map(file => file.path);
    }

    function groupPaths(groupIndex) {
        return measurements[groupIndex].runs.flatMap((run, runIndex) =>
            runPaths(groupIndex, runIndex)
        );
    }

    function setSelected(paths, checked) {
        paths.forEach(path => checked ? selected.add(path) : selected.delete(path));
        updateSelection();
    }

    function setCheckboxState(checkbox, paths) {
        const selectedCount = paths.filter(path => selected.has(path)).length;
        checkbox.checked = selectedCount === paths.length;
        checkbox.indeterminate = selectedCount > 0 && selectedCount < paths.length;
    }

    function updateSelection() {
        list.querySelectorAll('.file-select').forEach(checkbox => {
            checkbox.checked = selected.has(checkbox.dataset.path);
        });
        list.querySelectorAll('.run-select').forEach(checkbox => {
            setCheckboxState(checkbox, runPaths(Number(checkbox.dataset.group), Number(checkbox.dataset.run)));
        });
        list.querySelectorAll('.group-select').forEach(checkbox => {
            setCheckboxState(checkbox, groupPaths(Number(checkbox.dataset.group)));
        });

        selectionBar.hidden = selected.size === 0 || measurementRunning;
        selectionCount.textContent = `${selected.size} ${selected.size === 1 ? 'file' : 'files'} selected`;
    }

    function showNotice(message, error = false) {
        notice.textContent = message;
        notice.classList.toggle('is-error', error);
        notice.classList.add('is-visible');
        clearTimeout(noticeTimer);
        noticeTimer = setTimeout(() => notice.classList.remove('is-visible'), 2500);
    }

    function formatSize(bytes) {
        if (bytes < 1024) return `${bytes} B`;
        if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
        return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
    }

    function formatRecordedAt(value) {
        const date = new Date(value);
        if (Number.isNaN(date.getTime())) return value;
        return date.toLocaleString([], {
            day: 'numeric', month: 'short', year: 'numeric',
            hour: '2-digit', minute: '2-digit', hour12: false
        });
    }

    function normalizeSearch(value) {
        return String(value)
            .normalize('NFD')
            .replace(/[\u0300-\u036f]/g, '')
            .toLowerCase()
            .replace(/[^a-z0-9:]+/g, ' ')
            .trim();
    }

    function downloadIcon() {
        return '<span class="download-icon" aria-hidden="true"></span>';
    }

    function showLocked() {
        measurementRunning = true;
        selected.clear();
        search.disabled = true;
        loading.hidden = true;
        empty.hidden = true;
        list.hidden = true;
        summary.hidden = true;
        locked.hidden = false;
        updateSelection();
    }

    function showFiles() {
        measurementRunning = false;
        search.disabled = false;
        loading.hidden = true;
        locked.hidden = true;
        summary.hidden = false;
        render(search.value);
    }

    async function loadFiles() {
        loading.hidden = false;
        empty.hidden = true;
        list.hidden = true;
        try {
            const response = await fetch('/api/files');
            const payload = await response.json();
            if (response.status === 409) {
                showLocked();
                return;
            }
            if (!response.ok || payload.status !== 'success') {
                throw new Error(payload.detail || payload.message || 'Could not load files');
            }
            measurements = payload.measurements;
            filesLoaded = true;
            showFiles();
        } catch (error) {
            loading.hidden = true;
            empty.hidden = false;
            empty.querySelector('h2').textContent = 'Could not load files';
            empty.querySelector('p').textContent = error.message;
        }
    }

    async function checkStatus() {
        try {
            const response = await fetch('/api/status');
            const payload = await response.json();
            if (payload.status !== 'success') return;
            if (payload.is_running) {
                showLocked();
            } else if (measurementRunning || !filesLoaded) {
                await loadFiles();
            }
        } catch (error) {
            if (!filesLoaded) loadFiles();
        }
    }

    function render(query = '') {
        const needle = normalizeSearch(query);
        const filtered = measurements
            .map((group, groupIndex) => ({
                ...group,
                groupIndex,
                runs: group.runs.map((run, runIndex) => ({ ...run, runIndex }))
            }))
            .filter(group => normalizeSearch(`${group.name} ${group.display_name}`).includes(needle));

        const totalRuns = measurements.reduce((count, group) => count + group.runs.length, 0);
        const totalFiles = measurements.reduce((count, group) =>
            count + group.runs.reduce((sum, run) => sum + run.files.length, 0), 0);
        summary.innerHTML = `<span>${measurements.length} measurements</span><i></i><span>${totalRuns} recordings</span><i></i><span>${totalFiles} files</span>`;

        empty.hidden = filtered.length !== 0;
        if (!filtered.length) {
            empty.querySelector('h2').textContent = needle ? 'No matching files' : 'No measurement files';
            empty.querySelector('p').textContent = needle
                ? 'Try another search.'
                : 'Files will appear here after the first recording.';
        }
        list.hidden = filtered.length === 0;
        list.innerHTML = filtered.map(group => `
            <section class="measurement-group">
                <div class="measurement-heading">
                    <label class="select-control" aria-label="Select all files in ${escapeHtml(group.display_name)}">
                        <input class="group-select" type="checkbox" data-group="${group.groupIndex}">
                        <span></span>
                    </label>
                    <span class="folder-icon" aria-hidden="true"></span>
                    <div class="measurement-title">
                        <h2>${escapeHtml(group.display_name)}</h2>
                        <p>${group.runs.length} ${group.runs.length === 1 ? 'recording' : 'recordings'} · ${formatSize(group.size_bytes)}</p>
                    </div>
                    <button class="folder-download download-action" type="button" data-group="${group.groupIndex}">
                        ${downloadIcon()}<span>Download folder</span>
                    </button>
                </div>
                <div class="run-list">
                    ${group.runs.map(run => runTemplate(group.groupIndex, run)).join('')}
                </div>
            </section>
        `).join('');

        bindListEvents();
        updateSelection();
    }

    function runTemplate(groupIndex, run) {
        const runId = `${groupIndex}:${run.runIndex}`;
        const open = openRuns.has(runId);
        const duration = run.duration_minutes ? `${run.duration_minutes} min` : '—';
        return `
            <div class="run-item${open ? ' is-open' : ''}">
                <div class="run-head">
                    <label class="select-control" aria-label="Select this recording">
                        <input class="run-select" type="checkbox" data-group="${groupIndex}" data-run="${run.runIndex}">
                        <span></span>
                    </label>
                    <button class="run-toggle" type="button" data-run-id="${runId}" aria-expanded="${open}">
                        <span class="run-chevron" aria-hidden="true"></span>
                        <span class="run-primary">
                            <strong>${escapeHtml(formatRecordedAt(run.recorded_at))}</strong>
                            <small>${escapeHtml(run.name)}</small>
                        </span>
                    </button>
                    <span class="run-meta">${duration}</span>
                    <span class="run-meta">${run.files.length} files</span>
                    <span class="run-size">${formatSize(run.size_bytes)}</span>
                    <button class="run-download icon-download" type="button" data-group="${groupIndex}" data-run="${run.runIndex}" aria-label="Download this recording">
                        ${downloadIcon()}
                    </button>
                </div>
                <div class="run-files">
                    ${run.files.map(file => `
                        <div class="file-row">
                            <label class="select-control" aria-label="Select ${escapeHtml(file.name)}">
                                <input class="file-select" type="checkbox" data-path="${escapeHtml(file.path)}">
                                <span></span>
                            </label>
                            <span class="csv-badge">${escapeHtml(file.name.split('.').pop().toUpperCase())}</span>
                            <span class="file-name">${escapeHtml(file.name)}</span>
                            <span class="file-size">${formatSize(file.size_bytes)}</span>
                            <button class="file-download" type="button" data-path="${escapeHtml(file.path)}" aria-label="Download ${escapeHtml(file.name)}">
                                ${downloadIcon()}<span>Download</span>
                            </button>
                        </div>
                    `).join('')}
                </div>
            </div>
        `;
    }

    function bindListEvents() {
        list.querySelectorAll('.run-toggle').forEach(button => {
            button.addEventListener('click', () => {
                const run = button.closest('.run-item');
                const open = run.classList.toggle('is-open');
                button.setAttribute('aria-expanded', String(open));
                open ? openRuns.add(button.dataset.runId) : openRuns.delete(button.dataset.runId);
            });
        });
        list.querySelectorAll('.group-select').forEach(checkbox => {
            checkbox.addEventListener('change', () =>
                setSelected(groupPaths(Number(checkbox.dataset.group)), checkbox.checked)
            );
        });
        list.querySelectorAll('.run-select').forEach(checkbox => {
            checkbox.addEventListener('change', () =>
                setSelected(runPaths(Number(checkbox.dataset.group), Number(checkbox.dataset.run)), checkbox.checked)
            );
        });
        list.querySelectorAll('.file-select').forEach(checkbox => {
            checkbox.addEventListener('change', () => setSelected([checkbox.dataset.path], checkbox.checked));
        });
        list.querySelectorAll('.folder-download').forEach(button => {
            button.addEventListener('click', () => {
                const groupIndex = Number(button.dataset.group);
                downloadFiles(groupPaths(groupIndex), measurements[groupIndex].name);
            });
        });
        list.querySelectorAll('.run-download').forEach(button => {
            button.addEventListener('click', () => {
                const groupIndex = Number(button.dataset.group);
                const runIndex = Number(button.dataset.run);
                downloadFiles(runPaths(groupIndex, runIndex), measurements[groupIndex].runs[runIndex].name);
            });
        });
        list.querySelectorAll('.file-download').forEach(button => {
            button.addEventListener('click', () => downloadFiles([button.dataset.path], 'measurement'));
        });
    }

    async function downloadFiles(paths, archiveName) {
        if (downloadInProgress || measurementRunning) return;
        downloadInProgress = true;
        document.body.classList.add('files-busy');
        try {
            const response = await fetch('/api/files/download', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ paths, archive_name: archiveName })
            });
            if (!response.ok) {
                const payload = await response.json().catch(() => ({}));
                if (response.status === 409) showLocked();
                throw new Error(payload.detail || 'Download failed');
            }

            const blob = await response.blob();
            const disposition = response.headers.get('Content-Disposition') || '';
            const match = disposition.match(/filename\*=utf-8''([^;]+)|filename="?([^";]+)"?/i);
            const filename = decodeURIComponent(match?.[1] || match?.[2] || `${archiveName}.zip`);
            const url = URL.createObjectURL(blob);
            const link = document.createElement('a');
            link.href = url;
            link.download = filename;
            document.body.appendChild(link);
            link.click();
            link.remove();
            setTimeout(() => URL.revokeObjectURL(url), 1000);
            showNotice('Download started');
        } catch (error) {
            showNotice(error.message, true);
        } finally {
            downloadInProgress = false;
            document.body.classList.remove('files-busy');
        }
    }

    selectionClear.addEventListener('click', () => {
        selected.clear();
        updateSelection();
    });
    selectionDelete.addEventListener('click', () => {
        if (!selected.size) return;
        deleteMessage.textContent = `${selected.size} ${selected.size === 1 ? 'file' : 'files'} will be permanently deleted. This cannot be undone.`;
        deleteDialog.hidden = false;
        deleteCancel.focus();
    });
    deleteCancel.addEventListener('click', () => {
        deleteDialog.hidden = true;
    });
    deleteDialog.addEventListener('click', event => {
        if (event.target === deleteDialog) deleteDialog.hidden = true;
    });
    document.addEventListener('keydown', event => {
        if (event.key === 'Escape' && !deleteDialog.hidden) deleteDialog.hidden = true;
    });
    deleteConfirm.addEventListener('click', async () => {
        if (deleteInProgress || !selected.size) return;
        deleteInProgress = true;
        deleteConfirm.disabled = true;
        deleteCancel.disabled = true;
        try {
            const response = await fetch('/api/files/delete', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ paths: [...selected] })
            });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok) {
                if (response.status === 409) showLocked();
                throw new Error(payload.detail || 'Delete failed');
            }

            selected.clear();
            deleteDialog.hidden = true;
            await loadFiles();
            showNotice(`${payload.deleted_count} ${payload.deleted_count === 1 ? 'file' : 'files'} deleted`);
        } catch (error) {
            showNotice(error.message, true);
        } finally {
            deleteInProgress = false;
            deleteConfirm.disabled = false;
            deleteCancel.disabled = false;
        }
    });
    selectionDownload.addEventListener('click', () =>
        downloadFiles([...selected], 'bme688-selected')
    );
    search.addEventListener('input', () => render(search.value));

    checkStatus();
    setInterval(checkStatus, 2500);
});
