document.addEventListener('DOMContentLoaded', () => {

    const colors = ['#ff6b6b','#48dbfb','#1dd1a1','#feca57','#ff9ff3','#00d2d3',
                     '#54a0ff','#5f27cd','#ff9f43','#10ac84','#ee5a24','#0652DD',
                     '#9980FA','#C4E538','#FDA7DF','#D980FA','#40407a','#33d9b2'];
    const MAX_PARALLEL_TICKS = 429;
    const MAX_SEQUENTIAL_TICKS = 28;
    const DEFAULT_PRESET_ID = 'heater_354';

    // Profiles included with Bosch BME AI-Studio 2.3.2.
    const BOSCH_PROFILES = [
        ['heater_1',   'hp_001_stabilize', [320,320,320,320,320,320,320,320,320,320], [429,429,429,429,429,429,429,429,429,429]],
        ['heater_301', 'hp_301_basic', [100,100,200,200,200,200,320,320,320,320], [2,41,2,14,14,14,2,14,14,14]],
        ['heater_321', 'hp_321_stepped', [100,320,320,200,200,200,320,320,320,320], [43,2,2,2,21,21,2,14,14,14]],
        ['heater_322', 'hp_322_stepped', [100,320,320,200,200,200,320,320,320,320], [64,2,2,2,31,31,2,20,21,21]],
        ['heater_323', 'hp_323_wide', [70,350,350,210,210,210,350,350,350,350], [43,2,2,2,21,21,2,14,14,14]],
        ['heater_324', 'hp_324_wide', [70,350,350,210,210,210,350,350,350,350], [64,2,2,2,31,31,2,20,21,21]],
        ['heater_331', 'hp_331_deep', [50,50,350,350,350,140,140,350,350,350], [70,70,1,1,138,70,70,1,1,138]],
        ['heater_332', 'hp_332_deep', [50,50,350,350,350,140,140,350,350,350], [100,100,1,1,198,100,100,1,1,198]],
        ['heater_354', 'hp_354_standard', [320,100,100,100,200,200,200,320,320,320], [5,2,10,30,5,5,5,5,5,5]],
        ['heater_411', 'hp_411_levels', [100,320,170,320,240,240,240,320,320,320], [43,2,43,2,2,20,21,2,20,21]],
        ['heater_412', 'hp_412_levels', [100,320,170,320,240,240,240,320,320,320], [64,2,64,2,2,31,32,2,31,32]],
        ['heater_413', 'hp_413_wide_levels', [70,350,163,350,256,256,256,350,350,350], [43,2,43,2,2,20,21,2,20,21]],
        ['heater_414', 'hp_414_wide_levels', [70,350,163,350,256,256,256,350,350,350], [64,2,64,2,2,31,32,2,31,32]],
        ['heater_501', 'hp_501_sweep', [210,265,265,320,320,265,210,155,100,155], [24,2,22,2,22,24,24,24,24,24]],
        ['heater_502', 'hp_502_sweep', [210,265,265,320,320,265,210,155,100,155], [32,2,30,2,30,32,32,32,32,32]],
        ['heater_503', 'hp_503_wide_sweep', [210,280,280,350,350,280,210,140,70,140], [24,2,22,2,22,24,24,24,24,24]],
        ['heater_504', 'hp_504_wide_sweep', [210,280,280,350,350,280,210,140,70,140], [32,2,30,2,30,32,32,32,32,32]]
    ];

    const BUILTIN_PRESETS = BOSCH_PROFILES.map(([id, name, temps, ticks], index) => ({
        id,
        name,
        builtin: true,
        color: colors[index],
        mode: 'parallel',
        duty: 0,
        sleep: 0,
        temps,
        ticks
    }));

    const LEGACY_PRESET_IDS = {
        bosch_std: 'heater_354',
        bosch_voc_hi: 'heater_411',
        bosch_voc_ramp: 'heater_412',
        bosch_iaq_3s: 'heater_501',
        bosch_iaq_300s: 'heater_502'
    };

    let presets = [...BUILTIN_PRESETS];
    let sensorAssignments = {
        1: DEFAULT_PRESET_ID, 2: DEFAULT_PRESET_ID,
        3: DEFAULT_PRESET_ID, 4: DEFAULT_PRESET_ID,
        5: DEFAULT_PRESET_ID, 6: DEFAULT_PRESET_ID,
        7: DEFAULT_PRESET_ID, 8: DEFAULT_PRESET_ID
    };

    let selectedPresetId = DEFAULT_PRESET_ID;
    let fileDurationMinutes = 30;

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

    function normalizeAssignments(assignments = {}) {
        const availableIds = new Set(presets.map(preset => preset.id));
        const normalized = {};

        for (let sensor = 1; sensor <= 8; sensor++) {
            const savedId = assignments[sensor];
            const presetId = LEGACY_PRESET_IDS[savedId] || savedId;
            normalized[sensor] = availableIds.has(presetId)
                ? presetId
                : DEFAULT_PRESET_ID;
        }
        return normalized;
    }

    // load the device config first
    fetch('/api/config')
        .then(r => r.json())
        .then(res => {
            if (res.presets) {
                const userPresets = res.presets.filter(p => !p.builtin).map(normalizeUserPreset);
                presets = [...BUILTIN_PRESETS, ...userPresets];
                sensorAssignments = normalizeAssignments(res.assignments);
                if ([15, 20, 25, 30].includes(res.file_duration_minutes)) {
                    fileDurationMinutes = res.file_duration_minutes;
                }
                
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
                sensorAssignments = normalizeAssignments(parsed.assignments);
                if ([15, 20, 25, 30].includes(parsed.file_duration_minutes)) {
                    fileDurationMinutes = parsed.file_duration_minutes;
                }
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
    const presetHelp = document.getElementById('preset-help');
    const presetHelpButton = presetHelp.querySelector('.preset-help-button');

    const editName  = document.getElementById('edit-name');
    const editMode  = document.getElementById('edit-mode');
    const editDuty  = document.getElementById('edit-duty');
    const editSleep = document.getElementById('edit-sleep');

    function setPresetHelpOpen(open) {
        presetHelp.classList.toggle('is-open', open);
        presetHelpButton.setAttribute('aria-expanded', String(open));
    }

    presetHelpButton.addEventListener('click', event => {
        event.stopPropagation();
        setPresetHelpOpen(!presetHelp.classList.contains('is-open'));
    });

    document.addEventListener('click', event => {
        if (!presetHelp.contains(event.target)) {
            setPresetHelpOpen(false);
            presetHelpButton.blur();
        }
    });

    document.addEventListener('keydown', event => {
        if (event.key === 'Escape') {
            setPresetHelpOpen(false);
            presetHelpButton.blur();
        }
    });

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
                    Object.keys(sensorAssignments).forEach(k => {
                        if (sensorAssignments[k] === p.id) sensorAssignments[k] = DEFAULT_PRESET_ID;
                    });
                    if (selectedPresetId === p.id) selectedPresetId = DEFAULT_PRESET_ID;
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
        const payload = {
            presets,
            assignments: sensorAssignments,
            file_duration_minutes: fileDurationMinutes
        };

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
