document.addEventListener('DOMContentLoaded', () => {
    const preview = window.ClassifierPreview;
    const list = document.getElementById('classifier-list');
    const empty = document.getElementById('classifier-empty');
    const fileInput = document.getElementById('classifier-file');
    const upload = document.getElementById('classifier-upload');
    const notice = document.getElementById('classifier-notice');
    const remove = document.getElementById('classifier-remove');
    const removeDialog = document.getElementById('classifier-remove-dialog');
    const panel = document.getElementById('classifier-panel');
    const settings = document.querySelector('.classifier-settings');
    let selectedId = preview.activeModel()?.id || null;
    let measurementRunning = preview.isDemo && preview.readSession().running;
    let uploading = false;
    let busy = false;
    let removeId = null;
    let offlineNotice = false;

    function notify(message, error = false) {
        notice.textContent = message;
        notice.classList.toggle('is-error', error);
    }

    function minutes(seconds) {
        if (seconds < 60) return `${seconds} seconds`;
        const value = Math.round(seconds / 6) / 10;
        return `${value} ${value === 1 ? 'minute' : 'minutes'}`;
    }

    function shortDate(value) {
        if (!value) return '';
        const date = new Date(value);
        return Number.isNaN(date.getTime()) ? String(value).slice(0, 10)
            : date.toLocaleDateString([], { day: 'numeric', month: 'short' });
    }

    function render() {
        const state = preview.read();
        const loading = preview.modelsStatus();
        if (!state.models.some(model => model.id === selectedId)) {
            selectedId = state.activeId || state.models[0]?.id || null;
        }
        empty.hidden = state.models.length !== 0 || loading.status !== 'ready';
        document.getElementById('classifier-count').textContent = loading.status === 'loading' ? 'Loading…'
            : loading.status === 'unavailable' ? 'Device offline' : `${state.models.length} on this device`;
        if (loading.status === 'unavailable' && !notice.textContent) {
            notify(loading.message, true);
            offlineNotice = true;
        } else if (loading.status === 'ready' && offlineNotice) {
            notify('');
            offlineNotice = false;
        }
        list.replaceChildren();
        state.models.forEach(model => {
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'classifier-model-card';
            button.dataset.modelId = model.id;
            button.classList.toggle('is-selected', model.id === state.activeId);
            button.setAttribute('aria-pressed', String(model.id === state.activeId));
            button.setAttribute('aria-label', `${model.id === state.activeId ? 'Selected model:' : 'Select model:'} ${model.name}`);
            button.innerHTML = `
                <span class="model-symbol" aria-hidden="true"></span>
                <span class="classifier-model-copy">
                    <strong>${preview.escapeHtml(model.name)}</strong>
                    <span>v${preview.escapeHtml(model.version)} · ${model.classes.length} classes${model.accuracy?.unseen_board ? ` · ${Math.round(model.accuracy.unseen_board * 100)}% accuracy` : ''} · ${preview.formatSize(model.size_bytes)}</span>
                </span>
                <span class="model-status ${model.id === state.activeId ? 'selected-status' : ''}">${model.id === state.activeId ? 'Selected' : 'Select'}</span>`;
            button.addEventListener('click', () => {
                selectModel(model.id, button);
            });
            list.appendChild(button);
        });

        const model = state.models.find(item => item.id === selectedId);
        document.getElementById('classifier-details-empty').hidden = Boolean(model);
        document.getElementById('classifier-details-body').hidden = !model;
        document.getElementById('classifier-details-heading').textContent = model?.id === state.activeId
            ? 'Selected model' : 'Model details';
        if (model) {
            document.getElementById('classifier-model-name').textContent = model.name;
            document.getElementById('classifier-model-description').textContent = model.description || model.filename;
            const facts = [
                ['Sensors', `${model.sensors.length} sensors`],
                ['Analysis window', minutes(model.window_seconds)],
                ['Warm-up', minutes(model.warmup_seconds)],
                ['Prediction interval', minutes(model.prediction_interval_seconds)]
            ];
            if (model.accuracy?.unseen_board) {
                facts.push(['Accuracy', `${Math.round(model.accuracy.unseen_board * 100)}%`]);
            }
            document.getElementById('classifier-facts').innerHTML = facts.map(([label, value]) =>
                `<div><dt>${label}</dt><dd>${preview.escapeHtml(value)}</dd></div>`).join('');
            document.getElementById('classifier-file-facts').innerHTML = [
                ['Version', `v${model.version}`],
                ['File size', preview.formatSize(model.size_bytes)],
                ...(model.uploaded_at ? [['Added', shortDate(model.uploaded_at)]] : []),
                ...(model.filename ? [['File', model.filename]] : [])
            ].map(([label, value]) => `<div><dt>${label}</dt><dd>${preview.escapeHtml(value)}</dd></div>`).join('');
            renderTags('classifier-classes', model.class_labels || model.classes.map(preview.classLabel));
            document.getElementById('classifier-profiles').innerHTML = preview.profileGroups(model).map(group =>
                `<span>${group.sensors.length ? `${group.sensors.length} × ` : ''}${preview.escapeHtml(group.name)}</span>`).join('');
        }
        updateControls();
    }

    function renderTags(id, values) {
        document.getElementById(id).innerHTML = values.map(value =>
            `<span>${preview.escapeHtml(value)}</span>`).join('');
    }

    function updateControls() {
        const offline = preview.modelsStatus().status !== 'ready';
        fileInput.disabled = measurementRunning || uploading || busy || offline;
        upload.classList.toggle('is-disabled', fileInput.disabled);
        upload.setAttribute('aria-disabled', String(fileInput.disabled));
        list.querySelectorAll('button').forEach(button => {
            button.disabled = measurementRunning || busy;
        });
        remove.disabled = measurementRunning || busy || !selectedId;
    }

    function parseMetadata(payload, file) {
        if (!payload || typeof payload !== 'object' || Array.isArray(payload)) {
            throw new Error('This file needs model metadata.');
        }
        const metadata = payload.metadata || payload;
        if (!metadata || typeof metadata !== 'object' || Array.isArray(metadata)) {
            throw new Error('This file needs model metadata.');
        }
        function text(value, fallback = '') {
            if (value == null) return fallback;
            if (typeof value !== 'string' || !value.trim() || value.length > 200) {
                throw new Error('Model text must be between 1 and 200 characters.');
            }
            return value.trim();
        }
        function textList(value, name, maximum) {
            if (!Array.isArray(value) || !value.length || value.length > maximum) {
                throw new Error(`Add a valid ${name} list to the model metadata.`);
            }
            if (value.some(item => typeof item !== 'string' || !item.trim())) {
                throw new Error(`Every ${name} entry must have a name.`);
            }
            return [...new Set(value.map(item => text(item)))];
        }
        function seconds(value, fallback) {
            const result = value ?? fallback;
            if (!Number.isFinite(result) || result < 0 || result > 3600) {
                throw new Error('Timing values must be numbers between 0 and 3600 seconds.');
            }
            return result;
        }
        const requirements = metadata.profile_requirements == null ? null
            : preview.expandRequirements(metadata.profile_requirements);
        const sensors = requirements?.sensors || metadata.sensors || [1, 2, 3, 4, 5, 6, 7, 8];
        if (!Array.isArray(sensors) || !sensors.length || sensors.length > 8 ||
            sensors.some(sensor => !Number.isInteger(sensor) || sensor < 1 || sensor > 8)) {
            throw new Error('Sensors must be a list of numbers from 1 to 8.');
        }
        const model = {
            id: `preview-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
            name: text(metadata.name),
            version: text(metadata.version, '1.0'),
            description: text(metadata.description),
            classes: textList(metadata.classes, 'classes', 200),
            profiles: textList(metadata.profiles || metadata.heater_profiles || requirements?.profiles, 'heater profiles', 8),
            sensors: [...new Set(sensors)],
            window_seconds: seconds(metadata.window_seconds, 60),
            warmup_seconds: seconds(metadata.warmup_seconds, 120),
            prediction_interval_seconds: seconds(metadata.prediction_interval_seconds, 5),
            size_bytes: file.size,
            filename: file.name,
            demo: false
        };
        model.sensor_profiles = requirements?.sensor_profiles || metadata.sensor_profiles;
        if (!preview.validRequirements(model)) {
            throw new Error('Include profile_requirements with each profile’s sensor count, mode, duty, sleep, 10 temps and 10 ticks.');
        }
        const requiredNames = [...new Set(Object.values(model.sensor_profiles).map(profile => profile.profile))];
        if (requiredNames.length !== model.profiles.length || requiredNames.some(name => !model.profiles.includes(name))) {
            throw new Error('The heater profiles list must match the per-sensor profile names.');
        }
        // keep only the settings used by the compatibility check
        model.sensor_profiles = Object.fromEntries(model.sensors.map(sensor =>
            [sensor, window.HeaterProfiles.definition({ ...model.sensor_profiles[sensor], name: model.sensor_profiles[sensor].profile })]));
        if (!model.name) throw new Error('Add a model name to the metadata.');
        if (!model.window_seconds || !model.prediction_interval_seconds) {
            throw new Error('Analysis window and prediction interval must be greater than zero.');
        }
        return model;
    }

    async function importFile(file) {
        if (!file || measurementRunning || uploading) return;
        if (!file.name.toLowerCase().endsWith('.json')) {
            notify('Choose a JSON model file.', true);
            return;
        }
        if (file.size > 5 * 1024 * 1024) {
            notify('The model file must be 5 MiB or smaller.', true);
            return;
        }
        uploading = true;
        updateControls();
        notify('Checking the model…');
        try {
            const text = await file.text();
            const model = parseMetadata(JSON.parse(text), file);
            if (measurementRunning) throw new Error('Stop measurement before adding a model.');
            const added = await preview.addModel(text, file, model);
            selectedId = added?.id || model.id;
            notify(preview.read().activeId === selectedId ? 'Model added and selected.' : 'Model added. Select it to use it.');
        } catch (error) {
            notify(error instanceof SyntaxError ? 'This file is not valid JSON.' : error.message, true);
        } finally {
            uploading = false;
            fileInput.value = '';
            render();
        }
    }

    fileInput.addEventListener('change', () => importFile(fileInput.files[0]));
    ['dragenter', 'dragover'].forEach(event => upload.addEventListener(event, e => {
        e.preventDefault();
        if (!fileInput.disabled) upload.classList.add('is-dragging');
    }));
    ['dragleave', 'drop'].forEach(event => upload.addEventListener(event, e => {
        e.preventDefault();
        upload.classList.remove('is-dragging');
    }));
    upload.addEventListener('drop', e => importFile(e.dataTransfer.files[0]));

    async function selectModel(id, button) {
        if (measurementRunning || busy) return;
        const state = preview.read();
        const model = state.models.find(item => item.id === id);
        if (!model) return;
        const top = button.getBoundingClientRect().top;
        const changed = state.activeId !== id;
        busy = true;
        updateControls();
        try {
            if (changed) await preview.selectModel(id);
            if (selectedId !== id) settings.open = false;
            selectedId = id;
            if (changed) notify('Model selected.');
        } catch (error) {
            notify(error.message || 'Could not select the model.', true);
        } finally {
            busy = false;
            render();
            const selectedButton = [...list.children].find(item => item.dataset.modelId === id);
            if (selectedButton) {
                selectedButton.focus({ preventScroll: true });
                window.scrollBy({ top: selectedButton.getBoundingClientRect().top - top, behavior: 'instant' });
            }
        }
    }

    remove.addEventListener('click', () => {
        if (measurementRunning) return;
        const model = preview.read().models.find(item => item.id === selectedId);
        if (!model) return;
        removeId = model.id;
        document.getElementById('classifier-remove-message').textContent = `Remove ${model.name} from this device? Measurement files are kept.`;
        removeDialog.returnValue = 'cancel';
        removeDialog.showModal();
    });
    removeDialog.addEventListener('close', async () => {
        if (removeDialog.returnValue !== 'remove' || measurementRunning) return;
        busy = true;
        updateControls();
        try {
            await preview.removeModel(removeId);
            notify('Model removed.');
        } catch (error) {
            notify(error.message || 'Could not remove the model.', true);
        } finally {
            busy = false;
            render();
        }
    });

    async function checkMeasurement() {
        if (preview.isDemo) {
            measurementRunning = Boolean(preview.readSession().running);
        } else {
            const controller = new AbortController();
            const timeout = setTimeout(() => controller.abort(), 3000);
            try {
                const response = await fetch('/api/status', { signal: controller.signal });
                if (response.ok) {
                    const payload = await response.json();
                    if (payload.status === 'success') measurementRunning = Boolean(payload.is_running);
                }
            } catch (error) {} finally {
                clearTimeout(timeout);
            }
        }
        if (measurementRunning) {
            panel.hidden = true;
            window.location.replace(preview.isDemo
                ? `index.html?demo=1${preview.showControls ? '&controls=1' : ''}`
                : 'index.html');
            return;
        }
        panel.hidden = false;
        updateControls();
    }

    window.addEventListener('storage', event => {
        if (event.key === 'bmeClassifierPreviewV1' || event.key === null) render();
    });
    window.addEventListener('classifier-preview-change', render);
    window.addEventListener('focus', () => preview.refreshModels());
    render();
    checkMeasurement();
    if (!preview.isDemo) {
        setInterval(checkMeasurement, 2500);
    }
});
