// models live on the device; the demo keeps them in the browser
window.ClassifierPreview = (() => {
    const storageKey = 'bmeClassifierPreviewV2';
    const sessionKey = 'bmeAcquisitionPreviewV1';
    const configKey = 'bmeClassifierConfigPreviewV1';
    const params = new URLSearchParams(window.location.search);
    const isDemo = params.get('demo') === '1';
    const showControls = isDemo && params.get('controls') === '1';
    const demoProfileNames = ['hp_301_basic', 'hp_354_standard', 'hp_411_levels', 'hp_501_sweep'];
    const demoRequirements = Object.fromEntries(Array.from({ length: 8 }, (_, index) => {
        const preset = window.HeaterProfiles.presets().find(item => item.name === demoProfileNames[Math.floor(index / 2)]);
        return [index + 1, window.HeaterProfiles.definition(preset)];
    }));
    const demoModels = [{
        id: 'demo-smell-classifier',
        name: 'Clean air / spicy / bad smell',
        version: '0.2',
        description: 'Tells clean air, spicy and bad smell apart.',
        classes: ['Clean air', 'Spicy', 'Bad smell'],
        profiles: ['hp_301_basic', 'hp_354_standard', 'hp_411_levels', 'hp_501_sweep'],
        sensors: [1, 2, 3, 4, 5, 6, 7, 8],
        sensor_profiles: demoRequirements,
        window_seconds: 120,
        warmup_seconds: 0,
        prediction_interval_seconds: 30,
        size_bytes: 11129,
        filename: 'smell_classifier_v0.2.json',
        demo: true
    }];

    // models read from the device
    let deviceModels = { status: 'loading', models: [], activeId: null, message: '' };
    let modelsRequest = null;

    function read() {
        if (!isDemo) return { models: deviceModels.models, activeId: deviceModels.activeId };
        try {
            const value = JSON.parse(localStorage.getItem(storageKey));
            if (Array.isArray(value?.models)) return value;
        } catch (error) {}
        return { models: [], activeId: null };
    }

    function save(state) {
        localStorage.setItem(storageKey, JSON.stringify(state));
        window.dispatchEvent(new Event('classifier-preview-change'));
    }

    // device errors return status and message
    async function api(url, options = {}, timeoutMs = 8000) {
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), timeoutMs);
        try {
            const response = await fetch(url, { ...options, signal: controller.signal, cache: 'no-store' });
            let payload = null;
            try { payload = await response.json(); } catch (error) {}
            if (!response.ok || payload?.status === 'error') {
                throw new Error(payload?.message || payload?.detail || 'The device could not complete the request.');
            }
            return payload;
        } catch (error) {
            if (error.name === 'AbortError') throw new Error('The device did not respond. Check the Wi-Fi connection and try again.');
            if (error instanceof TypeError) throw new Error('Cannot reach the device. Check the Wi-Fi connection and try again.');
            throw error;
        } finally { clearTimeout(timeout); }
    }

    function publishModels(next) {
        deviceModels = { ...deviceModels, ...next };
        window.dispatchEvent(new Event('classifier-preview-change'));
    }

    async function loadModels() {
        try {
            const payload = await api('/api/models');
            if (!Array.isArray(payload?.models)) throw new Error('The device sent an unexpected model list.');
            publishModels({ status: 'ready', models: payload.models, activeId: payload.active_id || null, message: '' });
        } catch (error) {
            publishModels({ status: 'unavailable', message: error.message });
        }
    }

    function refreshModels() {
        if (isDemo) return Promise.resolve();
        if (!modelsRequest) modelsRequest = loadModels().finally(() => { modelsRequest = null; });
        return modelsRequest;
    }

    function modelsStatus() {
        return isDemo ? { status: 'ready', message: '' } : { status: deviceModels.status, message: deviceModels.message };
    }

    // the demo keeps using browser storage
    async function addModel(text, file, parsed) {
        if (isDemo) {
            const state = read();
            state.models.push(parsed);
            if (!state.activeId) state.activeId = parsed.id;
            save(state);
            return parsed;
        }
        const payload = await api(`/api/models?filename=${encodeURIComponent(file.name)}`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, body: text
        }, 20000);
        await refreshModels();
        return payload.model;
    }

    async function selectModel(id) {
        if (isDemo) {
            const state = read();
            state.activeId = id;
            save(state);
            return;
        }
        await api('/api/models/active', {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ id })
        });
        await refreshModels();
    }

    async function removeModel(id) {
        if (isDemo) {
            const state = read();
            state.models = state.models.filter(model => model.id !== id);
            if (state.activeId === id) state.activeId = null;
            save(state);
            return;
        }
        await api(`/api/models/${encodeURIComponent(id)}`, { method: 'DELETE' });
        await refreshModels();
    }

    function classLabel(name) {
        const text = String(name).replace(/_/g, ' ').trim();
        return text.charAt(0).toUpperCase() + text.slice(1);
    }

    function seedDemoModels() {
        const state = read();
        if (state.demoCatalogVersion === 4) return;
        demoModels.forEach(model => {
            const index = state.models.findIndex(item => item.id === model.id);
            if (index < 0) state.models.push({ ...model });
            else state.models[index] = { ...model };
        });
        if (!state.activeId) state.activeId = demoModels[0].id;
        state.demoCatalogVersion = 4;
        save(state);
    }

    function activeModel() {
        const state = read();
        return state.models.find(model => model.id === state.activeId) || null;
    }

    function readSession() {
        try {
            return JSON.parse(sessionStorage.getItem(sessionKey)) || { running: false };
        } catch (error) {
            return { running: false };
        }
    }

    function saveSession(session) {
        sessionStorage.setItem(sessionKey, JSON.stringify(session));
    }

    let configState = { status: 'checking', config: null };
    let configRequest = null;

    function defaultDemoConfiguration() {
        const presets = window.HeaterProfiles.presets();
        return {
            presets,
            assignments: Object.fromEntries(Array.from({ length: 8 }, (_, index) =>
                [index + 1, presets.find(item => item.name === demoRequirements[index + 1].profile).id])),
            file_duration_minutes: 30
        };
    }

    function publishConfiguration(status, config = null) {
        configState = { status, config };
        window.dispatchEvent(new Event('classifier-config-change'));
    }

    async function loadConfiguration() {
        if (isDemo) {
            try {
                const saved = localStorage.getItem(configKey);
                const config = saved ? JSON.parse(saved) : defaultDemoConfiguration();
                publishConfiguration(Array.isArray(config?.presets) && config.assignments ? 'ready' : 'unavailable', config);
            } catch (error) { publishConfiguration('unavailable'); }
            return;
        }
        // browser copies cannot confirm what the device is actually running
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), 3000);
        try {
            const response = await fetch('/api/config', { signal: controller.signal, cache: 'no-store' });
            if (!response.ok) throw new Error('Configuration unavailable');
            const config = await response.json();
            if (!Array.isArray(config?.presets) || !config.assignments) throw new Error('Configuration unavailable');
            publishConfiguration('ready', config);
        } catch (error) { publishConfiguration('unavailable'); }
        finally { clearTimeout(timeout); }
    }

    function refreshConfiguration() {
        if (!configRequest) configRequest = loadConfiguration().finally(() => { configRequest = null; });
        return configRequest;
    }

    function saveDemoConfiguration(config) {
        if (!isDemo) return;
        localStorage.setItem(configKey, JSON.stringify(config));
        publishConfiguration('ready', config);
    }

    function validProfile(profile) {
        const steps = (values, min, max) => Array.isArray(values) && values.length === 10 &&
            values.every(value => Number.isInteger(value) && value >= min && value <= max);
        return Boolean(profile && typeof profile.profile === 'string' && profile.profile.trim() &&
            profile.profile.length <= 200 && ['parallel', 'sequential'].includes(profile.mode) &&
            Number.isInteger(profile.duty) && profile.duty >= 0 &&
            Number.isInteger(profile.sleep) && profile.sleep >= 0 &&
            steps(profile.temps, 0, 400) && steps(profile.ticks, 1, profile.mode === 'sequential' ? 28 : 429));
    }

    function validRequirements(model) {
        if (!Array.isArray(model?.sensors) || !model.sensors.length || model.sensors.length > 8 ||
            model.sensors.some(sensor => !Number.isInteger(sensor) || sensor < 1 || sensor > 8) ||
            new Set(model.sensors).size !== model.sensors.length) return false;
        const requirements = model.sensor_profiles;
        return Boolean(requirements && typeof requirements === 'object' && !Array.isArray(requirements) &&
            Object.keys(requirements).length === model.sensors.length &&
            model.sensors.every(sensor => validProfile(requirements[sensor])));
    }

    function expandRequirements(requirements) {
        if (!Array.isArray(requirements) || !requirements.length || requirements.length > 8) {
            throw new Error('Add a valid profile_requirements list.');
        }
        const sensors = [];
        const sensor_profiles = {};
        const profiles = [];
        requirements.forEach(requirement => {
            if (!validProfile(requirement) || !Number.isInteger(requirement.count) ||
                requirement.count < 1 || sensors.length + requirement.count > 8) {
                throw new Error('Each profile requirement needs a sensor count and valid mode, duty, sleep, 10 temps and 10 ticks. Use 1 to 8 inputs in total.');
            }
            profiles.push(requirement.profile);
            for (let i = 0; i < requirement.count; i++) {
                const input = sensors.length + 1;
                sensors.push(input);
                sensor_profiles[input] = window.HeaterProfiles.definition({ ...requirement, name: requirement.profile });
            }
        });
        return { sensors, sensor_profiles, profiles: [...new Set(profiles)] };
    }

    function compatibility(model, state = configState) {
        if (!validRequirements(model)) return {
            status: 'unverified', compatible: false,
            message: 'This model has no complete profile requirements. Add a model with its training configuration.', issues: []
        };
        if (state.status !== 'ready') return {
            status: state.status, compatible: false,
            message: state.status === 'checking' ? 'Reading the sensor configuration.' :
                'Cannot verify the sensor configuration. Predictions are paused until it can be checked.', issues: []
        };
        const config = state.config;
        const issues = [];
        const available = [];
        for (let sensor = 1; sensor <= 8; sensor++) {
            const assigned = config.assignments[sensor];
            const matches = config.presets.filter(preset => preset?.id === assigned);
            const actual = matches.length === 1 ? matches[0] : null;
            if (actual) available.push({ sensor, profile: {
                ...actual, profile: actual.name || 'Assigned profile', sleep: actual.sleep ?? actual.sleep_sec
            } });
        }
        const used = new Set();
        const inputMapping = {};
        profileGroups(model).forEach(group => {
            const candidates = available.filter(item => validProfile(item.profile) &&
                profileSignature(item.profile) === profileSignature(group.settings));
            if (candidates.length < group.sensors.length) {
                issues.push(`${group.name}: needs ${group.sensors.length} sensors, found ${candidates.length} with matching settings.`);
            }
            group.sensors.forEach(input => {
                const candidate = candidates.find(item => !used.has(item.sensor));
                if (!candidate) return;
                used.add(candidate.sensor);
                inputMapping[input] = candidate.sensor;
            });
        });
        return {
            status: issues.length ? 'mismatch' : 'compatible', compatible: !issues.length, issues,
            input_mapping: issues.length ? null : inputMapping,
            message: issues.length ? 'Match the required profile settings and sensor counts. Recording can continue without predictions.' :
                'The required profiles and sensor counts match. Sensor positions can be changed.'
        };
    }

    function profileSignature(profile) {
        return JSON.stringify([profile.mode, profile.duty, profile.sleep, profile.temps, profile.ticks]);
    }

    function profileGroups(model) {
        if (!validRequirements(model)) return (model.profiles || []).map(name => ({ name, sensors: [] }));
        const groups = new Map();
        model.sensors.forEach(sensor => {
            const profile = model.sensor_profiles[sensor];
            const key = profileSignature(profile);
            if (!groups.has(key)) groups.set(key, { name: profile.profile, sensors: [], settings: profile });
            groups.get(key).sensors.push(sensor);
        });
        return [...groups.values()];
    }

    function configurationForModel(model, config) {
        if (!validRequirements(model) || !Array.isArray(config?.presets) || !config.assignments) {
            throw new Error('The model requirements and current configuration must be available.');
        }
        const next = JSON.parse(JSON.stringify(config));
        const groups = profileGroups(model).map(group => ({ ...group, assigned: [] }));
        const used = new Set();
        const changes = [];
        const matches = (preset, settings) => preset && validProfile({
            ...preset, profile: preset.name || 'Assigned profile', sleep: preset.sleep ?? preset.sleep_sec
        }) && profileSignature({ ...preset, sleep: preset.sleep ?? preset.sleep_sec }) === profileSignature(settings);

        // keep matching assignments before filling any missing groups
        groups.forEach(group => {
            for (let sensor = 1; sensor <= 8 && group.assigned.length < group.sensors.length; sensor++) {
                const presets = next.presets.filter(preset => preset?.id === next.assignments[sensor]);
                if (!used.has(sensor) && presets.length === 1 && matches(presets[0], group.settings)) {
                    used.add(sensor);
                    group.assigned.push(sensor);
                }
            }
        });
        groups.forEach(group => {
            if (group.assigned.length === group.sensors.length) return;
            let preset = next.presets.find(item => matches(item, group.settings) &&
                next.presets.filter(other => other?.id === item.id).length === 1);
            if (!preset) {
                let suffix = 1;
                while (next.presets.some(item => item?.id === `classifier_profile_${suffix}`)) suffix++;
                preset = {
                    id: `classifier_profile_${suffix}`, name: `${group.name}_model`,
                    builtin: false, color: '#8174eb', mode: group.settings.mode,
                    duty: group.settings.duty, sleep: group.settings.sleep,
                    temps: [...group.settings.temps], ticks: [...group.settings.ticks]
                };
                next.presets.push(preset);
            }
            for (let sensor = 1; sensor <= 8 && group.assigned.length < group.sensors.length; sensor++) {
                if (used.has(sensor)) continue;
                const previous = next.presets.find(item => item?.id === next.assignments[sensor]);
                changes.push({ sensor, from: previous?.name || 'Not configured', to: preset.name });
                next.assignments[sensor] = preset.id;
                used.add(sensor);
                group.assigned.push(sensor);
            }
        });
        if (!compatibility(model, { status: 'ready', config: next }).compatible) {
            throw new Error('Could not build a matching sensor configuration.');
        }
        return { config: next, changes };
    }

    function escapeHtml(value) {
        return String(value).replace(/[&<>"']/g, char => ({
            '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;'
        })[char]);
    }

    function formatSize(bytes) {
        if (bytes < 1024) return `${bytes} B`;
        if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KiB`;
        return `${(bytes / 1024 / 1024).toFixed(1)} MiB`;
    }

    document.addEventListener('DOMContentLoaded', () => {
        if (!isDemo) return;
        document.querySelectorAll('a[href="index.html"], a[href="classifier.html"], a[href="config.html"]').forEach(link => {
            link.href += showControls ? '?demo=1&controls=1' : '?demo=1';
        });
    });

    if (isDemo) {
        try { seedDemoModels(); } catch (error) {}
    } else {
        refreshModels();
    }

    window.addEventListener('storage', event => {
        if (event.key === configKey || event.key === null) refreshConfiguration();
    });

    return {
        isDemo, showControls, read, save, activeModel, readSession, saveSession, escapeHtml, formatSize,
        refreshConfiguration, saveDemoConfiguration, configuration: () => configState,
        validRequirements, expandRequirements, compatibility, profileGroups, configurationForModel,
        api, refreshModels, modelsStatus, addModel, selectModel, removeModel, classLabel
    };
})();
