document.addEventListener('DOMContentLoaded', () => {

    const colors = ['#ff6b6b','#48dbfb','#1dd1a1','#feca57','#ff9ff3','#00d2d3',
                     '#54a0ff','#5f27cd','#ff9f43','#10ac84','#ee5a24','#0652DD',
                     '#9980FA','#C4E538','#FDA7DF','#D980FA','#40407a','#33d9b2'];
    const MAX_PARALLEL_TICKS = 100;
    const MAX_SEQUENTIAL_TICKS = 28;

    // built-in Bosch heater profiles
    const BUILTIN_PRESETS = [
        {
            id: 'bosch_std', name: 'Bosch Standard (HP-354)', builtin: true, color: colors[0],
            mode: 'parallel', duty: 0, sleep: 0,
            temps: [320, 100, 100, 100, 200, 200, 200, 320, 320, 320],
            ticks: [  5,   2,  10,  30,   5,   5,   5,   5,   5,   5]
        },
        {
            id: 'bosch_lp', name: 'Bosch Low Power (HP-418)', builtin: true, color: colors[1],
            mode: 'parallel', duty: 1, sleep: 10,
            temps: [200, 200, 200, 200, 200, 200, 200, 200, 200, 200],
            ticks: [ 10,  10,  10,  10,  10,  10,  10,  10,  10,  10]
        },
        {
            id: 'bosch_ulp', name: 'Bosch Ultra Low Power (HP-419)', builtin: true, color: colors[2],
            mode: 'parallel', duty: 1, sleep: 30,
            temps: [150, 150, 150, 150, 150, 150, 150, 150, 150, 150],
            ticks: [  5,   5,   5,   5,   5,   5,   5,   5,   5,   5]
        },
        {
            id: 'bosch_voc_hi', name: 'Bosch VOC High (HP-411)', builtin: true, color: colors[3],
            mode: 'parallel', duty: 0, sleep: 0,
            temps: [400, 400, 350, 300, 250, 200, 150, 100, 50, 400],
            ticks: [ 22,  11,  11,  11,  11,  11,  11,  11, 11,  22]
        },
        {
            id: 'bosch_voc_ramp', name: 'Bosch VOC Ramp (HP-412)', builtin: true, color: colors[4],
            mode: 'parallel', duty: 0, sleep: 0,
            temps: [100, 150, 200, 250, 300, 350, 400, 350, 200, 100],
            ticks: [ 10,  10,  10,  10,  10,  10,  22,  10,  10,  10]
        },
        {
            id: 'bosch_iaq_3s', name: 'Bosch IAQ 3s (HP-501)', builtin: true, color: colors[5],
            mode: 'parallel', duty: 0, sleep: 0,
            temps: [320, 100, 100, 200, 200, 320, 320, 100, 200, 320],
            ticks: [  5,   2,   5,   5,   5,   5,   5,   2,   5,   5]
        },
        {
            id: 'bosch_iaq_300s', name: 'Bosch IAQ 300s (HP-502)', builtin: true, color: colors[6],
            mode: 'sequential', duty: 1, sleep: 270,
            temps: [320, 100, 100, 200, 200, 320, 320, 100, 200, 320],
            ticks: [ 10,  10,  10,  10,  10,  10,  10,  10,  10,  10]
        },
        {
            id: 'bosch_co2', name: 'Bosch CO2 Proxy (HP-601)', builtin: true, color: colors[7],
            mode: 'parallel', duty: 0, sleep: 0,
            temps: [400, 400, 400, 400, 400, 400, 400, 400, 400, 400],
            ticks: [ 22,  22,  22,  22,  22,  22,  22,  22,  22,  22]
        },
        {
            id: 'bosch_smoke', name: 'Bosch Smoke / Combustion (HP-602)', builtin: true, color: colors[8],
            mode: 'parallel', duty: 0, sleep: 0,
            temps: [350, 350, 350, 350, 400, 400, 400, 300, 250, 200],
            ticks: [ 11,  11,  11,  11,  22,  22,  22,  11,  11,  11]
        },
        {
            id: 'bosch_ethanol', name: 'Bosch Ethanol / Alcohol (HP-603)', builtin: true, color: colors[9],
            mode: 'parallel', duty: 0, sleep: 0,
            temps: [250, 300, 350, 400, 400, 350, 300, 250, 200, 150],
            ticks: [ 11,  11,  11,  22,  22,  11,  11,  11,  11,  11]
        },
        {
            id: 'bosch_h2', name: 'Bosch H2 / Reducing Gas (HP-701)', builtin: true, color: colors[10],
            mode: 'parallel', duty: 0, sleep: 0,
            temps: [150, 200, 250, 300, 350, 300, 250, 200, 150, 100],
            ticks: [ 11,  11,  11,  11,  22,  11,  11,  11,  11,  11]
        },
        {
            id: 'bosch_nh3', name: 'Bosch NH3 / Amines (HP-702)', builtin: true, color: colors[11],
            mode: 'parallel', duty: 0, sleep: 0,
            temps: [200, 250, 300, 350, 400, 400, 350, 300, 200, 150],
            ticks: [ 11,  11,  11,  11,  22,  22,  11,  11,  11,  11]
        },
        {
            id: 'bosch_toluene', name: 'Bosch Toluene / Aromatics (HP-703)', builtin: true, color: colors[12],
            mode: 'parallel', duty: 0, sleep: 0,
            temps: [300, 350, 400, 400, 350, 300, 250, 200, 150, 100],
            ticks: [ 11,  11,  22,  22,  11,  11,  11,  11,  11,  11]
        },
        {
            id: 'bosch_humidity', name: 'Bosch Humidity Sweep (HP-801)', builtin: true, color: colors[13],
            mode: 'sequential', duty: 0, sleep: 0,
            temps: [100, 100, 150, 150, 200, 200, 250, 250, 300, 300],
            ticks: [ 20,  20,  20,  20,  20,  20,  20,  20,  20,  20]
        },
        {
            id: 'bosch_wide', name: 'Bosch Wide Temp Scan (HP-901)', builtin: true, color: colors[14],
            mode: 'parallel', duty: 0, sleep: 0,
            temps: [ 50, 100, 150, 200, 250, 300, 350, 400, 350, 200],
            ticks: [ 11,  11,  11,  11,  11,  11,  11,  22,  11,  11]
        },
        {
            id: 'bosch_urban', name: 'Bosch Urban Air Quality (HP-3011)', builtin: true, color: colors[15],
            mode: 'parallel', duty: 0, sleep: 0,
            temps: [320, 200, 100, 200, 320, 200, 100, 200, 320, 100],
            ticks: [  5,   5,   2,   5,   5,   5,   2,   5,   5,   2]
        },
        {
            id: 'bosch_outdoor', name: 'Bosch Outdoor AQI (HP-3012)', builtin: true, color: colors[16],
            mode: 'sequential', duty: 1, sleep: 60,
            temps: [320, 320, 200, 200, 100, 100, 200, 200, 320, 320],
            ticks: [ 10,  10,  10,  10,  10,  10,  10,  10,  10,  10]
        }
    ];

    let presets = [...BUILTIN_PRESETS];
    let sensorAssignments = {
        1: 'bosch_std', 2: 'bosch_std', 3: 'bosch_std', 4: 'bosch_std',
        5: 'bosch_std', 6: 'bosch_std', 7: 'bosch_std', 8: 'bosch_std'
    };

    let selectedPresetId = 'bosch_std';

    function normalizeUserPreset(preset) {
        // support old browser config
        if (preset.sleep === undefined && preset.sleep_sec !== undefined) {
            preset.sleep = preset.sleep_sec;
        }
        delete preset.sleep_sec;
        if (preset.sleep === undefined) preset.sleep = 0;
        if (preset.mode === 'sequential' && Array.isArray(preset.ticks)) {
            preset.ticks = preset.ticks.map(tick =>
                Math.max(1, Math.min(MAX_SEQUENTIAL_TICKS, Number(tick) || 1))
            );
        }
        return preset;
    }

    // load the device config first
    fetch('/api/config')
        .then(r => r.json())
        .then(res => {
            if (res.presets) {
                const userPresets = res.presets.filter(p => !p.builtin).map(normalizeUserPreset);
                presets = [...BUILTIN_PRESETS, ...userPresets];
                if (res.assignments) sensorAssignments = res.assignments;
                
                renderPresets();
                renderSensors();
                loadEditor();
            } else {
                loadFromLocal();
            }
        })
        .catch(e => loadFromLocal());

    function loadFromLocal() {
        try {
            const stored = localStorage.getItem('bmeConfig');
            if (stored) {
                const parsed = JSON.parse(stored);
                if (parsed.presets) {
                    const userPresets = parsed.presets.filter(p => !p.builtin).map(normalizeUserPreset);
                    presets = [...BUILTIN_PRESETS, ...userPresets];
                }
                if (parsed.assignments) sensorAssignments = parsed.assignments;
            }
        } catch (e) {
            console.error('Failed to parse stored config', e);
        }
        
        renderPresets();
        renderSensors();
        loadEditor();
    }

    const presetListEl = document.getElementById('preset-list');
    const addPresetBtn = document.getElementById('add-preset-btn');
    const sensorGridEl = document.getElementById('sensor-grid');
    const curveStepsEl = document.getElementById('curve-steps');
    const applyAllBtn  = document.getElementById('apply-all-btn');

    const editName  = document.getElementById('edit-name');
    const editMode  = document.getElementById('edit-mode');
    const editDuty  = document.getElementById('edit-duty');
    const editSleep = document.getElementById('edit-sleep');

    function renderPresets() {
        presetListEl.innerHTML = '';
        presets.forEach(p => {
            const pill = document.createElement('div');
            pill.className = `preset-pill ${p.id === selectedPresetId ? 'active' : ''}`;

            const dot = document.createElement('div');
            dot.className = 'color-dot';
            dot.style.backgroundColor = p.color;

            const txt = document.createElement('span');
            txt.textContent = p.name;

            pill.appendChild(dot);
            pill.appendChild(txt);

            if (!p.builtin) {
                const del = document.createElement('button');
                del.className = 'preset-delete-btn';
                del.textContent = '\u00d7';
                del.title = 'Delete preset';
                del.addEventListener('click', (e) => {
                    e.stopPropagation();
                    presets = presets.filter(x => x.id !== p.id);
                    const fallback = presets[0].id;
                    Object.keys(sensorAssignments).forEach(k => {
                        if (sensorAssignments[k] === p.id) sensorAssignments[k] = fallback;
                    });
                    if (selectedPresetId === p.id) selectedPresetId = presets[0].id;
                    renderPresets();
                    renderSensors();
                    loadEditor();
                });
                pill.appendChild(del);
            }

            if (p.id === selectedPresetId) pill.style.borderColor = p.color;

            pill.addEventListener('click', () => {
                selectedPresetId = p.id;
                renderPresets();
                loadEditor();
            });

            presetListEl.appendChild(pill);
        });
    }

    function renderSensors() {
        sensorGridEl.innerHTML = '';
        for (let i = 1; i <= 8; i++) {
            const presetId = sensorAssignments[i];
            const preset   = presets.find(p => p.id === presetId);

            const sItem = document.createElement('div');
            sItem.className = 'sensor-item';

            if (preset) {
                sItem.style.color       = preset.color;
                sItem.style.borderColor = preset.color;
                sItem.innerHTML = `<h4>Sensor ${i}</h4><span>${preset.name}</span>`;
            }

            sItem.addEventListener('click', () => {
                sensorAssignments[i] = selectedPresetId;
                renderSensors();
            });

            sensorGridEl.appendChild(sItem);
        }
    }

    function generateStepInputs() {
        curveStepsEl.innerHTML = '';
        for (let i = 0; i < 10; i++) {
            const wrap = document.createElement('div');
            wrap.className = 'step-input-group';

            const label = document.createElement('span');
            label.textContent = `S${i + 1}`;

            const tempInput = document.createElement('input');
            tempInput.type = 'number'; tempInput.min = '0'; tempInput.max = '400';
            tempInput.dataset.type = 'temp'; tempInput.dataset.index = i;

            const tickInput = document.createElement('input');
            tickInput.type = 'number'; tickInput.min = '1'; tickInput.max = '100';
            tickInput.dataset.type = 'tick'; tickInput.dataset.index = i;

            wrap.appendChild(label);
            wrap.appendChild(tempInput);
            wrap.appendChild(tickInput);
            curveStepsEl.appendChild(wrap);

            tempInput.addEventListener('input', saveEditorToPreset);
            tickInput.addEventListener('input', saveEditorToPreset);
        }
    }

    function setEditorReadonly(readonly) {
        [editName, editMode, editDuty, editSleep].forEach(el => {
            el.disabled = readonly;
            el.style.opacity = readonly ? '0.5' : '1';
        });
        curveStepsEl.querySelectorAll('input').forEach(inp => {
            inp.disabled = readonly;
            inp.style.opacity = readonly ? '0.5' : '1';
        });
        if (readonly) {
            editName.title = 'Built-in Bosch profiles cannot be edited';
        } else {
            editName.title = '';
        }
    }

    function loadEditor() {
        const p = presets.find(x => x.id === selectedPresetId);
        if (!p) return;

        editName.value  = p.name;
        editMode.value  = p.mode;
        editDuty.value  = p.duty;
        editSleep.value = p.sleep;

        const inputs = curveStepsEl.querySelectorAll('input');
        inputs.forEach(inp => {
            const idx = parseInt(inp.dataset.index);
            inp.value = (inp.dataset.type === 'temp') ? p.temps[idx] : p.ticks[idx];
            if (inp.dataset.type === 'tick') {
                inp.max = p.mode === 'sequential'
                    ? MAX_SEQUENTIAL_TICKS
                    : MAX_PARALLEL_TICKS;
            }
        });

        setEditorReadonly(!!p.builtin);
    }

    function saveEditorToPreset() {
        const p = presets.find(x => x.id === selectedPresetId);
        if (!p || p.builtin) return;

        p.name = editName.value || 'Unnamed Preset';
        p.mode = editMode.value;
        p.duty = parseInt(editDuty.value) || 0;
        p.sleep = parseInt(editSleep.value) || 0;

        const maxTicks = p.mode === 'sequential'
            ? MAX_SEQUENTIAL_TICKS
            : MAX_PARALLEL_TICKS;

        curveStepsEl.querySelectorAll('input').forEach(inp => {
            const idx = parseInt(inp.dataset.index);
            let val = parseInt(inp.value) || 0;
            if (inp.dataset.type === 'temp') p.temps[idx] = val;
            else {
                val = Math.max(1, Math.min(maxTicks, val));
                p.ticks[idx] = val;
                inp.max = maxTicks;
                inp.value = val;
            }
        });

        renderPresets();
        renderSensors();
    }

    editName.addEventListener('input',  saveEditorToPreset);
    editMode.addEventListener('change', saveEditorToPreset);
    editDuty.addEventListener('input',  saveEditorToPreset);
    editSleep.addEventListener('input', saveEditorToPreset);

    addPresetBtn.addEventListener('click', () => {
        const newId    = 'p_' + Date.now();
        const userPresets = presets.filter(p => !p.builtin);
        const randColor   = colors[(BUILTIN_PRESETS.length + userPresets.length) % colors.length];

        presets.push({
            id: newId,
            name: `New Profile ${userPresets.length + 1}`,
            builtin: false,
            color: randColor,
            mode: 'parallel', duty: 0, sleep: 0,
            temps: [200,200,200,200,200,200,200,200,200,200],
            ticks: [ 10, 10, 10, 10, 10, 10, 10, 10, 10, 10]
        });

        selectedPresetId = newId;
        renderPresets();
        loadEditor();
    });

    applyAllBtn.addEventListener('click', () => {
        const payload = { presets, assignments: sensorAssignments };

        fetch('/api/config', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        })
            .then(r => r.json())
            .then(res => {
                if (res.status !== 'success') throw new Error(res.message || 'Save failed');
                localStorage.setItem('bmeConfig', JSON.stringify(payload));
                const orig = applyAllBtn.textContent;
                applyAllBtn.textContent = '\u2713 Configuration Saved';
                setTimeout(() => applyAllBtn.textContent = orig, 2000);
            })
            .catch(e => {
                console.error('Failed to push config to backend', e);
                const orig = applyAllBtn.textContent;
                applyAllBtn.textContent = 'Save Failed — Try Again';
                setTimeout(() => applyAllBtn.textContent = orig, 2500);
            });
    });

    generateStepInputs();
    renderPresets();
    renderSensors();
    loadEditor();
});
