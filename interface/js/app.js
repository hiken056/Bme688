document.addEventListener('DOMContentLoaded', () => {
    const btn        = document.getElementById('main-toggle-btn');
    const statusLog  = document.getElementById('status-log');
    const readings   = document.getElementById('live-readings');
    const badge      = document.getElementById('update-badge');
    const footer     = document.getElementById('last-updated');
    const duration   = document.getElementById('measurement-duration');

    let isRunning = false;
    let liveInterval = null;

    // block config changes while measuring
    const navLinks = document.querySelectorAll('nav a, .mobile-nav-links a');
    navLinks.forEach(link => {
        link.addEventListener('click', (e) => {
            if (isRunning && link.getAttribute('href') === 'config.html') {
                e.preventDefault();
                btn.style.boxShadow = '0 0 40px var(--danger)';
                setTimeout(() => btn.style.boxShadow = '', 400);
                
                const p = statusLog.querySelector('.running-text');
                if (p) {
                    const oldText = p.textContent;
                    const oldColor = p.style.color;
                    p.textContent = "Stop measurement before changing config!";
                    p.style.color = "var(--danger)";
                    setTimeout(() => {
                        p.textContent = oldText;
                        p.style.color = oldColor;
                    }, 2000);
                }
            }
        });
    });

    // set the Pi clock from the browser
    function syncRTC() {
        const ts = Date.now();
        console.log('[Auto-Sync] RTC =>', new Date(ts).toISOString());
        
        fetch('/api/sync-time', { 
            method: 'POST', 
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ timestamp_ms: ts }) 
        }).catch(e => console.error("Failed to sync RTC", e));
    }
    syncRTC();

    function saveFileDuration() {
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

    fetch('/api/file-duration')
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


    function renderReadings(res) {
        // keep old single-sensor responses working
        const list = res.sensors ? res.sensors : [res];

        readings.innerHTML = list.map(d => {
            if (!d.valid && d.valid !== undefined) return '';
            return `
                <div class="reading-card">
                    <div class="rc-label">Sensor ${d.sensor || 1} &mdash; ${d.preset || ''}</div>
                    <div class="rc-metrics">
                        <div class="rc-metric">
                            <div class="rc-m-label">TEMP</div>
                            <div class="rc-value">${d.temperature?.toFixed(1) ?? '—'}<span class="rc-unit">°C</span></div>
                        </div>
                        <div class="rc-metric">
                            <div class="rc-m-label">HUM</div>
                            <div class="rc-value">${d.humidity?.toFixed(1) ?? '—'}<span class="rc-unit">%</span></div>
                        </div>
                        <div class="rc-metric">
                            <div class="rc-m-label">PRES</div>
                            <div class="rc-value">${d.pressure?.toFixed(1) ?? '—'}<span class="rc-unit">hPa</span></div>
                        </div>
                        <div class="rc-metric gas-metric">
                            <div class="rc-m-label">GAS</div>
                            <div class="rc-value">${d.gasResistance ? Math.round(d.gasResistance).toLocaleString() : '—'}<span class="rc-unit">Ω</span></div>
                        </div>
                    </div>
                </div>`;
        }).join('');

        const now = new Date();
        footer.textContent = `Last updated: ${now.toLocaleTimeString('en-US', { hour12: false })}`;
        badge.textContent = 'LIVE';
        badge.classList.add('live');
    }

    function resetReadings() {
        readings.innerHTML = `<div class="reading-row idle-msg"><span>Start measuring to see live data</span></div>`;
        footer.textContent = '—';
        badge.textContent = '—';
        badge.classList.remove('live');
    }

    function startUI(fileDurationMinutes = null) {
        if (isRunning) return;
        isRunning = true;
        if (fileDurationMinutes) duration.value = String(fileDurationMinutes);
        duration.disabled = true;
        btn.textContent = 'STOP';
        btn.classList.replace('start', 'stop');
        statusLog.innerHTML = `<p class="running-text" style="color:var(--neon-blue);">Starting measurement...</p>`;

        // refresh live data every two seconds
        liveInterval = setInterval(() => {
            fetch('/api/logs')
                .then(r => r.json())
                .then(res => {
                    if (res.status === 'success' && res.logs) {
                        if (res.logs.includes('[S') || res.logs.includes('Collecting data') || res.logs.includes('[OK] Sensor')) {
                            statusLog.innerHTML = `<p class="running-text" style="color:var(--success);">Recording 8 sensors simultaneously...</p>`;
                        } else {
                            statusLog.innerHTML = `<p class="running-text" style="color:var(--neon-blue);">Initializing sensors...</p><pre class="debug-log" style="color:#ff6b6b">${res.logs}</pre>`;
                        }
                    }
                }).catch(e => {});

            fetch('/api/latest')
                .then(r => r.json())
                .then(res => {
                    if (res.status === 'success' && res.data) {
                        renderReadings(res.data);
                    }
                }).catch(e => {});
        }, 2000);
    }

    function stopUI() {
        if (!isRunning) return;
        isRunning = false;
        duration.disabled = false;
        btn.textContent = 'START';
        btn.classList.replace('stop', 'start');
        statusLog.innerHTML = `<p class="stopped-text">Measurement stopped.</p>`;
        
        clearInterval(liveInterval);
        liveInterval = null;
        resetReadings();
    }

    btn.addEventListener('click', () => {
        if (!isRunning) {
            syncRTC();
            saveFileDuration()
                .then(() => fetch('/api/start-sensor', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ file_duration_minutes: Number(duration.value) })
                }))
                .then(r => r.json())
                .then(res => {
                    if (res.status !== 'success') throw new Error(res.message || 'Start failed');
                    startUI(res.file_duration_minutes);
                })
                .catch(e => {
                    statusLog.innerHTML = `<p class="stopped-text" style="color:var(--danger);">${e.message}</p>`;
                });
        } else {
            fetch('/api/stop-sensor', { method: 'POST' });
            stopUI();
        }
    });

    // keep several open devices in sync
    setInterval(() => {
        fetch('/api/status')
            .then(r => r.json())
            .then(res => {
                if (res.status === 'success') {
                    if (res.is_running && !isRunning) startUI(res.file_duration_minutes);
                    else if (!res.is_running && isRunning) stopUI();
                }
            }).catch(e => {});
    }, 2500);

    function renderMiniGrid() {
        const gridEl = document.getElementById('mini-sensor-grid');
        if (!gridEl) return;
        
        gridEl.innerHTML = '';

        let savedPresets = [];
        let savedAssignments = {
            1: 'p_std', 2: 'p_std', 3: 'p_std', 4: 'p_std',
            5: 'p_std', 6: 'p_std', 7: 'p_std', 8: 'p_std'
        };

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
    
    renderMiniGrid();
});
