document.addEventListener('DOMContentLoaded', () => {
    const btn        = document.getElementById('main-toggle-btn');
    const statusLog  = document.getElementById('status-log');
    const preview = window.ClassifierPreview;
    const duration   = document.getElementById('measurement-duration');
    const sessionDialog = document.getElementById('session-dialog');
    const sessionForm   = document.getElementById('session-form');
    const sessionError  = document.getElementById('session-error');
    const sessionCancel = document.getElementById('session-cancel');
    const sessionStart  = document.getElementById('session-start');
    const classifierWarning = document.getElementById('session-classifier-warning');
    const classifierWarningText = document.getElementById('session-classifier-warning-text');
    const sessionMatch = document.getElementById('session-match-profiles');

    let isRunning = false;
    let sensorsStarted = false;
    let liveInterval = null;
    let activeFolderName = null;

    // keep settings and files closed while measuring
    const navigationWarnings = {
        'files.html': 'Stop measurement before downloading files!',
        'config.html': 'Stop measurement before changing config!',
        'classifier.html': 'Stop measurement before opening Classifier!'
    };
    document.addEventListener('click', (e) => {
        const link = e.target.closest('a[href]');
        if (!link || !isRunning) return;
        const target = new URL(link.href).pathname.split('/').pop();
        if (!navigationWarnings[target]) return;

        e.preventDefault();
        btn.style.boxShadow = '0 0 40px var(--danger)';
        setTimeout(() => btn.style.boxShadow = '', 400);

        const p = statusLog.querySelector('.running-text');
        if (p) {
            const oldText = p.textContent;
            const oldColor = p.style.color;
            p.textContent = navigationWarnings[target];
            p.style.color = 'var(--danger)';
            setTimeout(() => {
                p.textContent = oldText;
                p.style.color = oldColor;
            }, 2000);
        }
    });

    // set the Pi clock from the browser
    function syncRTC() {
        if (preview.isDemo) return Promise.resolve();
        const ts = Date.now();
        console.log('[Auto-Sync] RTC =>', new Date(ts).toISOString());
        
        return fetch('/api/sync-time', {
            method: 'POST', 
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ timestamp_ms: ts }) 
        }).catch(e => console.error("Failed to sync RTC", e));
    }
    syncRTC();

    function saveFileDuration() {
        if (preview.isDemo) return Promise.resolve({ status: 'success' });
        return fetch('/api/file-duration', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ file_duration_minutes: Number(duration.value) })
        })
            .then(r => r.json())
            .then(res => {
                if (res.status !== 'success') throw new Error(res.message || 'Save failed');
                return res;
            });
    }

    if (!preview.isDemo) fetch('/api/file-duration')
        .then(r => r.json())
        .then(res => {
            if (res.status === 'success') {
                duration.value = String(res.file_duration_minutes);
            }
        })
        .catch(e => console.error('Failed to load file length', e));

    duration.addEventListener('change', () => {
        saveFileDuration().catch(e => {
            statusLog.innerHTML = `<p class="stopped-text" style="color:var(--danger);">${e.message}</p>`;
        });
    });

    function openSessionDialog() {
        sessionError.textContent = '';
        updateSessionWarning();
        sessionDialog.showModal();
        window.SampleForm.open();
    }

    function updateSessionWarning() {
        const model = preview.activeModel();
        const check = model ? preview.compatibility(model) : null;
        classifierWarning.hidden = !check || check.compatible;
        classifierWarningText.textContent = check?.status === 'mismatch'
            ? 'Profiles don’t match the selected model. This measurement will record data without predictions.'
            : 'The classifier setup cannot be verified. This measurement will record data without predictions.';
        sessionMatch.hidden = check?.status !== 'mismatch';
    }

    sessionMatch.addEventListener('click', () => {
        sessionDialog.close();
        window.dispatchEvent(new Event('classifier-match-request'));
    });
    window.addEventListener('classifier-config-change', updateSessionWarning);
    window.addEventListener('classifier-preview-change', updateSessionWarning);

    sessionCancel.addEventListener('click', () => sessionDialog.close());

    sessionForm.addEventListener('submit', (e) => {
        e.preventDefault();
        const form = window.SampleForm.read();
        if (form.error) {
            sessionError.textContent = form.error;
            document.getElementById(form.focus)?.focus();
            return;
        }
        window.SampleForm.remember(form.cycles);
        startMeasurement(form, message => { sessionError.textContent = message; });
    });

    // a test needs nothing filled in, it starts straight away
    function startTest() {
        startMeasurement(window.SampleForm.read(), message => {
            statusLog.innerHTML = '<p class="stopped-text" style="color:var(--danger);"></p>';
            statusLog.querySelector('p').textContent = message;
        });
    }

    function startMeasurement(form, showError) {
        const measurementTitle = form.title;
        if (preview.isDemo) {
            preview.saveSession({ running: true, title: measurementTitle, duration: Number(duration.value) });
            window.Guide.start(form.mode, form.cycles);
            startUI(Number(duration.value), measurementTitle);
            return;
        }

        sessionStart.disabled = true;
        btn.disabled = true;
        sessionError.textContent = '';

        Promise.all([syncRTC(), saveFileDuration()])
            .then(() => fetch('/api/start-sensor', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    measurement_title: measurementTitle,
                    file_duration_minutes: Number(duration.value),
                    mode: form.mode,
                    cycles: form.cycles
                })
            }))
            .then(r => r.json())
            .then(res => {
                if (res.status !== 'success' && res.error) {
                    // the sensors did not start: the card says why, the form stays for another try
                    window.ErrorCard.show(window.ErrorCard.cardFor(res.error, res.message));
                    return;
                }
                if (res.status !== 'success') throw new Error(res.message || 'Start failed');
                sessionDialog.close();
                window.Guide.start(form.mode, form.cycles);
                startUI(res.file_duration_minutes, res.measurement_title);
            })
            .catch(e => showError(e.message))
            .finally(() => {
                sessionStart.disabled = false;
                btn.disabled = false;
            });
    }


    function startUI(fileDurationMinutes = null, folderName = null) {
        if (isRunning) return;
        isRunning = true;
        if (sessionDialog.open) sessionDialog.close();
        if (fileDurationMinutes) duration.value = String(fileDurationMinutes);
        activeFolderName = folderName || activeFolderName;
        duration.disabled = true;
        btn.textContent = 'STOP';
        btn.classList.replace('start', 'stop');
        statusLog.innerHTML = `<p class="running-text" style="color:var(--neon-blue);">Starting measurement...</p>`;
        window.dispatchEvent(new CustomEvent('measurement-ui-change', { detail: { running: true } }));

        if (preview.isDemo) {
            statusLog.innerHTML = '<p class="running-text" style="color:var(--success);"></p>';
            statusLog.querySelector('p').textContent = 'Recording';
            return;
        }

        // refresh recording logs every two seconds
        liveInterval = setInterval(() => {
            fetch('/api/logs')
                .then(r => r.json())
                .then(res => {
                    // an answer that arrives after stop must not bring "recording" back
                    if (!isRunning) return;
                    if (res.status === 'success' && res.logs) {
                        // file messages push the start messages out of the log tail
                        sensorsStarted = sensorsStarted || ['[S', 'Collecting data', '[OK] Sensor', '[FILE]'].some(text => res.logs.includes(text));
                        if (sensorsStarted) {
                            statusLog.innerHTML = '<p class="running-text" style="color:var(--success);">Recording</p>';
                        } else {
                            statusLog.innerHTML = `<p class="running-text" style="color:var(--neon-blue);">Initializing sensors...</p><pre class="debug-log" style="color:#ff6b6b">${res.logs}</pre>`;
                        }
                    }
                }).catch(e => {});
        }, 2000);
    }

    function stopUI() {
        if (!isRunning) return;
        isRunning = false;
        sensorsStarted = false;
        activeFolderName = null;
        duration.disabled = false;
        btn.textContent = 'START';
        btn.classList.replace('stop', 'start');
        statusLog.innerHTML = `<p class="stopped-text">Measurement stopped.</p>`;
        
        clearInterval(liveInterval);
        liveInterval = null;
        window.dispatchEvent(new CustomEvent('measurement-ui-change', { detail: { running: false } }));
    }

    function stopMeasurement() {
        if (preview.isDemo) preview.saveSession({ running: false });
        else fetch('/api/stop-sensor', { method: 'POST' });
        stopUI();
    }

    btn.addEventListener('click', () => {
        if (!isRunning && window.SampleForm.mode() === 'test') startTest();
        else if (!isRunning) openSessionDialog();
        else stopMeasurement();
    });

    // the last cycle ends the measurement
    window.addEventListener('guide-finished', () => {
        // the device stops on its own, the demo stops here
        if (isRunning) {
            if (preview.isDemo) stopMeasurement();
            else stopUI();
        }
        statusLog.innerHTML = '<p class="success-text">All cycles done. You can remove the sample.</p>';
    });

    // a measurement that stopped on its own: one card per failure on each device
    function showFailure(failure) {
        if (!failure || Date.now() - failure.id > 3600000) return;
        let seen = null;
        try { seen = localStorage.getItem('bmeSeenFailure'); } catch (e) {}
        if (seen === String(failure.id)) return;
        try { localStorage.setItem('bmeSeenFailure', String(failure.id)); } catch (e) {}
        window.ErrorCard.show(window.ErrorCard.cardFor(failure.code, failure.message));
    }

    // keep several open devices in sync
    function checkMeasurementStatus() {
        fetch('/api/status')
            .then(r => r.json())
            .then(res => {
                if (res.status === 'success') {
                    if (res.is_running && !isRunning) {
                        startUI(res.file_duration_minutes, res.measurement_title);
                    }
                    else if (!res.is_running && isRunning) stopUI();
                    showFailure(res.failure);
                }
            }).catch(e => {});
    }

    if (preview.isDemo) {
        const session = preview.readSession();
        if (session.running) startUI(session.duration, session.title);
    } else {
        checkMeasurementStatus();
        setInterval(checkMeasurementStatus, 2500);
    }

    function renderMiniGrid() {
        const gridEl = document.getElementById('mini-sensor-grid');
        if (!gridEl) return;
        
        gridEl.innerHTML = '';

        let savedPresets = [];
        let savedAssignments = {
            1: 'p_std', 2: 'p_std', 3: 'p_std', 4: 'p_std',
            5: 'p_std', 6: 'p_std', 7: 'p_std', 8: 'p_std'
        };

        if (preview.isDemo) {
            preview.refreshConfiguration().then(() => {
                const config = preview.configuration().config;
                buildGrid(config?.presets || [], config?.assignments || {});
            });
            return;
        }

        fetch('/api/config')
            .then(r => r.json())
            .then(res => {
                if (res.presets) {
                    savedPresets = res.presets;
                    savedAssignments = res.assignments || savedAssignments;
                } else {
                    // use the browser copy before the first device save
                    try {
                        const stored = localStorage.getItem('bmeConfig');
                        if (stored) {
                            const parsed = JSON.parse(stored);
                            savedPresets = parsed.presets || [];
                            savedAssignments = parsed.assignments || savedAssignments;
                        } else {
                            savedPresets = [{ id: 'p_std', color: '#ff6b6b', name: 'Standard Gas Scanner' }];
                        }
                    } catch (e) {
                        savedPresets = [{ id: 'p_std', color: '#ff6b6b', name: 'Standard Gas Scanner' }];
                    }
                }
                buildGrid(savedPresets, savedAssignments);
            }).catch(e => {
                buildGrid(savedPresets, savedAssignments);
            });

        function buildGrid(presetsList, assignmentsList) {
            for (let i = 1; i <= 8; i++) {
                const presetId = assignmentsList[i];
                const preset = presetsList.find(p => p.id === presetId);
                const color = preset ? preset.color : '#8888a0';

                const dot = document.createElement('div');
                dot.className = 'mini-sensor-dot';
                dot.style.setProperty('--dot-color', color);
                dot.title = `Sensor ${i}: ${preset ? preset.name : 'Unknown'}`;
                gridEl.appendChild(dot);
            }
        }
    }
    
    window.addEventListener('classifier-config-change', () => {
        const config = preview.configuration().config;
        const grid = document.getElementById('mini-sensor-grid');
        if (!grid) return;
        [...grid.children].forEach((dot, index) => {
            const preset = config?.presets?.find(item => item.id === config.assignments[index + 1]);
            dot.style.setProperty('--dot-color', preset?.color || '#8888a0');
            dot.title = `Sensor ${index + 1}: ${preset?.name || 'Unknown'}`;
        });
    });
    renderMiniGrid();
});
