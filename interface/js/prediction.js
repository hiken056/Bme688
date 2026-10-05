document.addEventListener('DOMContentLoaded', () => {
    const preview = window.ClassifierPreview;
    const result = document.getElementById('prediction-result');
    const updated = document.getElementById('prediction-updated');
    const windowLabel = document.getElementById('prediction-window');
    const stateSelect = document.getElementById('prediction-demo-state');
    const matchDialog = document.getElementById('profile-match-dialog');
    const matchForm = document.getElementById('profile-match-form');
    const matchError = document.getElementById('profile-match-error');
    const matchApply = document.getElementById('profile-match-apply');
    const matchCancel = document.getElementById('profile-match-cancel');
    let measuring = false;
    let demoState = 'idle';
    let demoStartedAt = null;
    let previewOverride = false;
    let matchingProfiles = false;
    let matchPlan = null;

    document.getElementById('prediction-demo-controls').hidden = !preview.showControls;

    const states = {
        no_model: ['No model selected', 'Choose a classifier to prepare live predictions.', 'Select a model', 'classifier.html'],
        inactive: ['Classifier inactive', 'Select a model on the Classifier page.', 'Select a model', 'classifier.html'],
        idle: ['Ready when you are', 'Start a measurement to collect a smell pattern.'],
        warming: ['Warming up', 'Waiting for the sensors to settle before analysing.'],
        collecting: ['Collecting data', 'Building a complete pattern across sensors and heater steps.'],
        analysing: ['Analysing', 'Comparing the latest pattern with the model.'],
        incompatible: ['Profile mismatch', 'The selected model needs different heater profiles.', 'Review model', 'classifier.html'],
        checking: ['Checking profiles', 'Reading the sensor configuration before making predictions.'],
        unverified: ['Profile settings missing', 'This model needs its training profile settings and sensor counts.', 'Review model', 'classifier.html'],
        unavailable: ['Configuration unavailable', 'Cannot verify the sensor profiles. Predictions are paused; recording can continue.'],
        error: ['Classifier unavailable', 'Predictions are unavailable. Measurement recording continues.'],
        disconnected: ['Waiting for classifier', 'Predictions will appear when the classifier is connected.']
    };

    function renderState(state, model, message) {
        if (state === 'incompatible') return renderMismatch(model, preview.compatibility(model));
        const [title, description, action, href] = states[state];
        const progress = state === 'warming' ? 43 : 68;
        const busy = ['warming', 'collecting', 'analysing'].includes(state);
        result.dataset.state = state;
        result.innerHTML = `
            <div class="prediction-placeholder">
                <span class="prediction-symbol ${busy ? 'is-busy' : ''}" aria-hidden="true">
                    <svg viewBox="0 0 32 32" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M4 16h4l3-8 5 16 4-12 3 4h5" stroke-linecap="round" stroke-linejoin="round"/></svg>
                </span>
                <h2>${title}</h2>
                <p>${preview.escapeHtml(message || description)}</p>
                ${busy ? `<div class="prediction-progress" aria-hidden="true"><span style="width:${progress}%"></span></div>
                    <span class="prediction-progress-note">${state === 'warming' ? `${model?.warmup_seconds || 120}s warm-up` : `${model?.window_seconds || 60}s analysis window`}</span>` : ''}
                ${action ? `<a class="btn btn-outline" href="${href}${preview.isDemo && href === 'classifier.html' ? (preview.showControls ? '?demo=1&controls=1' : '?demo=1') : ''}">${action}</a>` : ''}
            </div>`;
        updated.textContent = 'No predictions yet';
    }

    function pageLink(page) {
        return `${page}${preview.isDemo ? `?demo=1${preview.showControls ? '&controls=1' : ''}` : ''}`;
    }

    function renderMismatch(model, check) {
        result.dataset.state = 'incompatible';
        result.innerHTML = `
            <div class="prediction-placeholder prediction-mismatch">
                <span class="prediction-symbol" aria-hidden="true">
                    <svg viewBox="0 0 32 32" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M16 6 28 26H4L16 6Z" stroke-linejoin="round"/><path d="M16 13v6m0 3v1" stroke-linecap="round"/></svg>
                </span>
                <h2>Profiles don’t match</h2>
                <p>${measuring ? 'Recording continues without predictions. Stop the measurement to update the sensor profiles.' :
                    'This model needs a different sensor setup. Use its profiles to enable predictions, or record without predictions.'}</p>
                ${!measuring ? `<div class="prediction-mismatch-actions">
                    <button id="prediction-match-profiles" class="btn btn-primary" type="button" ${matchingProfiles ? 'disabled' : ''}>Use model profiles</button>
                    <a class="classifier-text-link" href="${pageLink('config.html')}">Configure manually</a>
                </div>` : ''}
                ${check.issues.length ? `<details class="prediction-profile-details">
                    <summary>See what differs <span class="classifier-chevron" aria-hidden="true"></span></summary>
                    <ul>${check.issues.map(issue => `<li>${preview.escapeHtml(issue)}</li>`).join('')}</ul>
                </details>` : ''}
            </div>`;
        updated.textContent = 'Predictions unavailable';
    }

    async function request(url, options = {}) {
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), 5000);
        try {
            const response = await fetch(url, { ...options, signal: controller.signal, cache: 'no-store' });
            if (!response.ok) throw new Error('Could not reach the device. Try again.');
            return await response.json();
        } finally { clearTimeout(timeout); }
    }

    async function ensureStopped() {
        if (measuring || (preview.isDemo && preview.readSession().running)) {
            throw new Error('Stop the measurement before changing sensor profiles.');
        }
        if (!preview.isDemo) {
            const status = await request('/api/status');
            if (status.status !== 'success') throw new Error('Could not verify that the measurement is stopped.');
            if (status.is_running) throw new Error('Stop the measurement before changing sensor profiles.');
        }
    }

    function setMatchPlan(model) {
        const state = preview.configuration();
        if (state.status !== 'ready') throw new Error('The sensor configuration is unavailable. Try again when the device is connected.');
        const plan = preview.configurationForModel(model, state.config);
        matchPlan = { ...plan, modelId: model.id, requirements: JSON.stringify(model.sensor_profiles),
            original: JSON.stringify(state.config) };
        document.getElementById('profile-match-message').textContent = `Update the sensor setup for ${model.name}?`;
        document.getElementById('profile-match-changes').innerHTML = plan.changes.map(change =>
            `<li><strong>Sensor ${change.sensor}</strong><span>${preview.escapeHtml(change.from)} → ${preview.escapeHtml(change.to)}</span></li>`).join('');
    }

    async function openMatchDialog() {
        if (matchingProfiles || measuring) return;
        matchingProfiles = true;
        matchPlan = null;
        matchApply.disabled = false;
        matchCancel.disabled = false;
        document.getElementById('profile-match-message').textContent = 'Checking the selected model and sensor settings.';
        document.getElementById('profile-match-changes').replaceChildren();
        try {
            await ensureStopped();
            await preview.refreshConfiguration();
            const model = preview.activeModel();
            if (!model) throw new Error('Select a model first.');
            setMatchPlan(model);
            previewOverride = false;
            if (!matchPlan.changes.length) { matchPlan = null; return; }
            matchError.textContent = '';
            matchDialog.showModal();
            matchCancel.focus();
        } catch (error) {
            matchError.textContent = error.name === 'AbortError' ? 'The device did not respond. Try again.' : error.message;
            if (!matchDialog.open) matchDialog.showModal();
            matchApply.disabled = true;
        } finally {
            matchingProfiles = false;
            render();
        }
    }

    result.addEventListener('click', event => {
        if (event.target.closest('#prediction-match-profiles')) openMatchDialog();
    });
    window.addEventListener('classifier-match-request', openMatchDialog);
    matchCancel.addEventListener('click', () => { if (!matchingProfiles) matchDialog.close(); });
    matchDialog.addEventListener('cancel', event => { if (matchingProfiles) event.preventDefault(); });
    matchDialog.addEventListener('close', () => { matchPlan = null; matchError.textContent = ''; });
    matchForm.addEventListener('submit', async event => {
        event.preventDefault();
        if (matchingProfiles || !matchPlan) return;
        matchingProfiles = true;
        matchApply.disabled = true;
        matchCancel.disabled = true;
        matchApply.textContent = 'Applying…';
        matchError.textContent = '';
        try {
            await ensureStopped();
            await preview.refreshConfiguration();
            const model = preview.activeModel();
            if (!model || model.id !== matchPlan.modelId || JSON.stringify(model.sensor_profiles) !== matchPlan.requirements) {
                throw new Error('The selected model changed. Close this dialog and review it again.');
            }
            if (preview.configuration().status !== 'ready') throw new Error('Could not read the sensor configuration. Try again.');
            if (JSON.stringify(preview.configuration().config) !== matchPlan.original) {
                setMatchPlan(model);
                if (!matchPlan.changes.length) { matchDialog.close(); return; }
                throw new Error('The sensor settings changed. Review the updated assignments before applying.');
            }
            await ensureStopped();
            if (preview.isDemo) preview.saveDemoConfiguration(matchPlan.config);
            else {
                const saved = await request('/api/config', {
                    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(matchPlan.config)
                });
                if (saved.status !== 'success') throw new Error(saved.message || 'Could not save the sensor profiles.');
                try { localStorage.setItem('bmeConfig', JSON.stringify(matchPlan.config)); } catch (error) {}
                await preview.refreshConfiguration();
                if (!preview.compatibility(model).compatible) throw new Error('Profiles saved, but the matching configuration could not be verified. Check the device before starting.');
            }
            matchDialog.close();
        } catch (error) {
            matchError.textContent = error.name === 'AbortError' ? 'The device did not respond. Check its settings before trying again.' : error.message;
        } finally {
            matchingProfiles = false;
            matchApply.disabled = false;
            matchCancel.disabled = false;
            matchApply.textContent = 'Apply profiles';
            render();
        }
    });

    function renderPrediction(model, unknown) {
        const labels = model.classes.slice(0, 4);
        const preferred = labels.find(label => label.toLowerCase() === 'food') || labels[0];
        const ranked = [preferred, ...labels.filter(label => label !== preferred)];
        const scores = unknown ? [38, 29, 20, 13] : [86, 8, 4, 2];
        // keep demo probabilities consistent when a model has fewer classes
        if (ranked.length === 1) scores[0] = unknown ? 38 : 100;
        else scores[ranked.length - 1] += scores.slice(ranked.length).reduce((sum, value) => sum + value, 0);
        result.dataset.state = unknown ? 'unknown' : 'ready';
        result.innerHTML = `
            <div class="prediction-summary ${unknown ? 'is-unknown' : ''}">
                <div>
                    <span class="classifier-label">${unknown ? 'No confident match' : 'Predicted smell'}</span>
                    <h2>${unknown ? 'Unknown' : preview.escapeHtml(preferred)}</h2>
                </div>
                <div class="prediction-confidence">
                    <strong>${scores[0]}<span>%</span></strong>
                    <span>${unknown ? 'Top score' : 'Model confidence'}</span>
                </div>
            </div>
            <p class="prediction-explanation">${unknown ? 'The pattern does not have a confident match. Recording continues.' : 'A stable match across the latest analysis window.'}</p>
            <div class="prediction-scores">
                <h4>Class probabilities</h4>
                ${ranked.map((label, i) => `
                    <div class="prediction-score ${i === 0 && !unknown ? 'is-leading' : ''}">
                        <div><span>${preview.escapeHtml(label)}</span><span>${scores[i]}%</span></div>
                        <div class="prediction-score-track"><span style="width:${scores[i]}%"></span></div>
                    </div>`).join('')}
            </div>`;
        updated.textContent = `Updated ${new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false })}`;
    }

    function render() {
        const model = preview.activeModel();
        document.getElementById('prediction-model-name').textContent = model ? `${model.name} · v${model.version}` : 'No model selected';
        windowLabel.textContent = model ? `${model.window_seconds}s window · every ${model.prediction_interval_seconds}s` : '';
        if (!model) {
            renderState(preview.read().models.length ? 'inactive' : 'no_model');
        } else if (!preview.compatibility(model).compatible) {
            const check = preview.compatibility(model);
            renderState(check.status === 'mismatch' ? 'incompatible' : check.status, model, check.message);
        } else if (!measuring && !previewOverride) {
            renderState('idle', model);
        } else if (!preview.isDemo) {
            renderState('disconnected', model);
        } else if (demoState === 'ready' || demoState === 'unknown') {
            renderPrediction(model, demoState === 'unknown');
        } else {
            renderState(demoState, model);
        }
    }

    stateSelect.addEventListener('change', () => {
        demoState = stateSelect.value;
        previewOverride = true;
        demoStartedAt = null;
        render();
    });
    window.addEventListener('measurement-ui-change', event => {
        const wasMeasuring = measuring;
        measuring = event.detail.running;
        previewOverride = false;
        preview.refreshConfiguration();
        if (preview.isDemo) {
            if (measuring && !wasMeasuring) {
                demoStartedAt = Date.now();
                demoState = 'warming';
            } else if (!measuring) {
                demoStartedAt = null;
                demoState = 'idle';
            }
            stateSelect.value = demoState;
        }
        render();
    });
    if (preview.isDemo) {
        setInterval(() => {
            if (!measuring || !demoStartedAt) return;
            const elapsed = Date.now() - demoStartedAt;
            const nextState = elapsed < 3000 ? 'warming' : elapsed < 6000 ? 'collecting' : 'ready';
            if (nextState === demoState) return;
            demoState = nextState;
            stateSelect.value = demoState;
            render();
        }, 1000);
    }
    window.addEventListener('classifier-preview-change', render);
    window.addEventListener('classifier-config-change', render);
    window.addEventListener('storage', event => {
        if (event.key === 'bmeClassifierPreviewV1' || event.key === null) render();
    });
    render();
    preview.refreshConfiguration();
    window.addEventListener('focus', () => preview.refreshConfiguration());
    document.addEventListener('visibilitychange', () => {
        if (!document.hidden) preview.refreshConfiguration();
    });
    if (!preview.isDemo) setInterval(() => {
        if (!document.hidden) preview.refreshConfiguration();
    }, 10000);
});
