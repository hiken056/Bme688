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
    // live data from the device
    let live = null;
    let liveError = '';
    let uiChanges = 0;      // grows at every start and stop, older answers are ignored
    let liveTimer = null;
    // countdown to the next prediction
    let nextAt = null;
    let nextKind = 'next';

    document.getElementById('prediction-demo-controls').hidden = !preview.showControls;

    const states = {
        no_model: ['No model selected', 'Choose a classifier to prepare live predictions.', 'Select a model', 'classifier.html'],
        inactive: ['Classifier inactive', 'Select a model on the Classifier page.', 'Select a model', 'classifier.html'],
        idle: ['Ready when you are', 'Start a measurement to collect a smell pattern.'],
        starting: ['Starting sensors', 'Waiting for the first sensor readings.'],
        collecting: ['Collecting data', 'Building a complete pattern across sensors and heater steps.'],
        analysing: ['Analysing', 'Comparing the latest pattern with the model.'],
        no_reference: ['No clean-air reference', 'The model has no training clean air for these heater profiles.'],
        waiting_clean_air: ['Waiting for clean air', 'Predictions start after the warm-up and the clean air.'],
        waiting_sample: ['Waiting for the sample', 'Predictions start 2 min after you tap Sample placed.'],
        incompatible: ['Profile mismatch', 'The selected model needs different heater profiles.', 'Review model', 'classifier.html'],
        checking: ['Checking profiles', 'Reading the sensor configuration before making predictions.'],
        loading: ['Loading models', 'Reading the models stored on this device.'],
        unverified: ['Profile settings missing', 'This model needs its training profile settings and sensor counts.', 'Review model', 'classifier.html'],
        unavailable: ['Configuration unavailable', 'Cannot verify the sensor profiles. Predictions are paused; recording can continue.'],
        no_data: ['No sensor data', 'No new readings are arriving from the sensors. Check the measurement log.'],
        error: ['Classifier unavailable', 'Predictions are unavailable. Measurement recording continues.'],
        disconnected: ['Device not reachable', 'Predictions will appear when the device responds again. Recording continues on the device.']
    };
    const busyStates = ['starting', 'collecting', 'analysing', 'loading'];

    function duration(seconds) {
        seconds = Math.max(0, Math.round(seconds || 0));
        if (seconds < 60) return `${seconds} s`;
        if (seconds >= 5400) return `${Math.round(seconds / 3600)} h`;
        const minutes = Math.floor(seconds / 60);
        const rest = seconds % 60;
        return rest && minutes < 10 ? `${minutes} min ${rest} s` : `${Math.round(seconds / 60)} min`;
    }

    function clock(value) {
        const date = value ? new Date(value) : new Date();
        return Number.isNaN(date.getTime()) ? '' : date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });
    }

    function day(value) {
        const date = new Date(value);
        return Number.isNaN(date.getTime()) ? '' : date.toLocaleDateString([], { day: 'numeric', month: 'short' });
    }

    function pageLink(page) {
        return `${page}${preview.isDemo ? `?demo=1${preview.showControls ? '&controls=1' : ''}` : ''}`;
    }

    // options: progress, note, actions
    function renderState(state, model, message, options = {}) {
        if (state === 'incompatible') return renderMismatch(model, preview.compatibility(model));
        const [title, description, action, href] = states[state] || states.error;
        const busy = busyStates.includes(state);
        const progress = options.progress ?? 68;
        result.dataset.state = state;
        result.innerHTML = `
            <div class="prediction-placeholder">
                <span class="prediction-symbol ${busy ? 'is-busy' : ''}" aria-hidden="true">
                    <svg viewBox="0 0 32 32" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M4 16h4l3-8 5 16 4-12 3 4h5" stroke-linecap="round" stroke-linejoin="round"/></svg>
                </span>
                <h2>${title}</h2>
                <p>${preview.escapeHtml(message || description)}</p>
                ${busy && state !== 'loading' && state !== 'starting' ? `<div class="prediction-progress" aria-hidden="true"><span style="width:${Math.min(100, Math.max(3, progress))}%"></span></div>
                    <span class="prediction-progress-note">${preview.escapeHtml(options.note ||
                        `${model?.window_seconds || 120} s analysis window`)}</span>` : ''}
                ${options.actions || ''}
                ${action ? `<a class="btn btn-outline" href="${pageLink(href)}">${action}</a>` : ''}
            </div>`;
        updated.textContent = 'No predictions yet';
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
                ${check.issues?.length ? `<details class="prediction-profile-details">
                    <summary>See what differs <span class="classifier-chevron" aria-hidden="true"></span></summary>
                    <ul>${check.issues.map(issue => `<li>${preview.escapeHtml(issue)}</li>`).join('')}</ul>
                </details>` : ''}
            </div>`;
        updated.textContent = 'Predictions unavailable';
    }

    // prediction from the device or the demo
    function renderResult(prediction, model) {
        const kind = prediction.state;
        const confident = kind === 'ready';
        const titles = {
            ready: prediction.label_text, unknown: 'Unknown', unusual: 'Unusual pattern', insufficient: 'Not enough data'
        };
        const labels = {
            ready: 'Predicted smell', unknown: 'No confident match', unusual: 'Not like the training data', insufficient: 'Prediction skipped'
        };
        const explanations = {
            ready: `The best match over the last ${prediction.window_seconds} seconds.`,
            unknown: `Closest match: ${prediction.top_text}, but below the ${Math.round((model?.unknown_below ?? 0.6) * 100)}% needed for a confident answer.`,
            unusual: 'The sensor pattern is far from anything the model learned, so its answer would not be reliable.',
            insufficient: 'Too few complete heater cycles arrived in the last window. Predictions continue when data arrives.'
        };
        const percent = Math.round(prediction.confidence * 100);
        result.dataset.state = confident ? 'ready' : 'unknown';
        result.innerHTML = `
            <div class="prediction-summary ${confident ? '' : 'is-unknown'}">
                <div>
                    <span class="classifier-label">${labels[kind] || 'Prediction'}</span>
                    <h2>${preview.escapeHtml(titles[kind] || prediction.top_text)}</h2>
                </div>
                ${kind === 'insufficient' ? '' : `<div class="prediction-confidence">
                    <strong>${percent}<span>%</span></strong>
                    <span>${confident ? 'Model confidence' : 'Top score'}</span>
                </div>`}
            </div>
            <p class="prediction-explanation">${preview.escapeHtml(explanations[kind] || '')}</p>
            ${prediction.warnings?.length ? `<ul class="prediction-warnings">${prediction.warnings.map(text =>
                `<li>${preview.escapeHtml(text)}</li>`).join('')}</ul>` : ''}
            ${kind === 'insufficient' ? '' : `<div class="prediction-scores">
                <h4>Class probabilities</h4>
                ${prediction.probabilities.map((item, i) => {
                    const value = Math.round(item.p * 100);
                    return `<div class="prediction-score ${i === 0 && confident ? 'is-leading' : ''}">
                        <div><span>${preview.escapeHtml(item.label)}</span><span>${value}%</span></div>
                        <div class="prediction-score-track"><span style="width:${value}%"></span></div>
                    </div>`;
                }).join('')}
            </div>`}`;
        updated.textContent = `Updated ${clock(prediction.time)}`;
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

    // device polling
    async function poll() {
        if (preview.isDemo) return;
        const asked = uiChanges;
        try {
            const answer = await preview.api('/api/prediction', {}, 5000);
            // started or stopped while the answer was on its way: wait for the next one
            if (asked !== uiChanges) return;
            live = answer;
            liveError = '';
            const seconds = live.next_prediction_s ?? live.collecting_remaining_s;
            nextKind = live.next_prediction_s != null ? 'next' : 'first';
            nextAt = live.running && seconds != null ? Date.now() + seconds * 1000 : null;
        } catch (error) {
            liveError = error.message;
        }
        render();
    }

    function schedulePolling() {
        clearTimeout(liveTimer);
        if (preview.isDemo) return;
        const delay = document.hidden ? 30000 : measuring ? 3000 : 15000;
        liveTimer = setTimeout(async () => {
            if (!document.hidden) await poll();
            schedulePolling();
        }, delay);
    }

    function renderLive(model) {
        if (!live) {
            renderState(liveError ? 'disconnected' : 'loading', model, liveError || 'Reading the classifier status.');
            return;
        }
        const s = live;
        switch (s.state) {
            case 'no_model':
                renderState('inactive', model);
                break;
            case 'incompatible':
                renderMismatch(model, { issues: s.issues || [] });
                break;
            case 'idle':
                // the device needs a moment to notice a new measurement
                renderState(measuring ? 'starting' : 'idle', model);
                break;
            case 'starting':
                renderState('starting', model);
                break;
            case 'no_reference':
                renderState('no_reference', model, s.message);
                break;
            case 'waiting_clean_air':
            case 'waiting_sample':
                renderState(s.state, model);
                break;
            case 'collecting': {
                const total = model.window_seconds || 120;
                const left = s.collecting_remaining_s ?? total;
                renderState('collecting', model, null, {
                    progress: (1 - left / total) * 100, note: `First prediction in about ${duration(left)}`
                });
                break;
            }
            case 'no_data':
                renderState('no_data', model, s.message);
                break;
            case 'error':
                renderState('error', model, s.message);
                break;
            default:
                if (s.result) renderResult(s.result, model);
                else renderState('analysing', model);
        }
    }

    function demoPrediction(model, unknown) {
        const labels = model.classes.slice(0, 4);
        // the demo follows what is in front of the sensors
        const wanted = { clean_air: 'clean air', spicy: 'spicy', bad_smell: 'bad smell' }[window.Guide?.demoStage()?.sample];
        const preferred = labels.find(label => label.toLowerCase() === wanted) || labels[0];
        const ranked = [preferred, ...labels.filter(label => label !== preferred)];
        const scores = unknown ? [38, 29, 20, 13] : [86, 8, 4, 2];
        // keep demo probabilities consistent when a model has fewer classes
        if (ranked.length === 1) scores[0] = unknown ? 38 : 100;
        else scores[ranked.length - 1] += scores.slice(ranked.length).reduce((sum, value) => sum + value, 0);
        return {
            state: unknown ? 'unknown' : 'ready', label_text: preferred, top_text: preferred,
            confidence: scores[0] / 100, window_seconds: model.window_seconds, time: new Date().toISOString(),
            probabilities: ranked.map((label, i) => ({ label, p: scores[i] / 100 })), warnings: []
        };
    }

    function renderCountdown() {
        if (preview.isDemo || !measuring || !nextAt || !preview.activeModel()) return;
        const seconds = Math.ceil((nextAt - Date.now()) / 1000);
        updated.textContent = seconds > 0
            ? `${nextKind === 'first' ? 'First' : 'Next'} prediction in ${duration(seconds)}`
            : 'Next prediction any moment';
    }

    function render() {
        const model = preview.activeModel();
        const loading = preview.modelsStatus();
        document.getElementById('prediction-model-name').textContent = model ? `${model.name} v${model.version}` : 'No model selected';
        // which clean air the predictions are compared with
        const stage = preview.isDemo ? window.Guide?.demoStage() : null;
        const guided = stage || (live?.reference?.source === 'cycle' ? live.reference : null);
        const testing = stage ? stage.mode === 'test' : live?.reference?.log?.startsWith('clean_air');
        windowLabel.textContent = !model ? ''
            : guided?.cycle ? (testing ? 'Uses the latest clean air' : `Uses clean air of cycle ${guided.cycle}`)
            : live?.reference?.averaged ? 'Uses average reference air' : 'Uses reference air';
        if (!model) {
            if (loading.status === 'loading') renderState('loading', null);
            else if (loading.status === 'unavailable') renderState('disconnected', null, loading.message);
            else renderState(preview.read().models.length ? 'inactive' : 'no_model');
        } else if (!preview.compatibility(model).compatible) {
            const check = preview.compatibility(model);
            renderState(check.status === 'mismatch' ? 'incompatible' : check.status, model, check.message);
        } else if (!measuring && !previewOverride) {
            renderState('idle', model);
        } else if (!preview.isDemo) {
            renderLive(model);
        } else if (stage) {
            // the demo follows the cycles: nothing before the clean air, then one window of data
            if (stage.key.startsWith('waiting')) renderState(stage.key, model);
            else if (stage.key === 'collecting') {
                renderState('collecting', model, null, {
                    progress: (1 - stage.left_s / 120) * 100, note: `First prediction in about ${duration(stage.left_s)}`
                });
            } else renderResult(demoPrediction(model, false), model);
        } else if (demoState === 'ready' || demoState === 'unknown') {
            renderResult(demoPrediction(model, demoState === 'unknown'), model);
        } else {
            renderState(demoState, model);
        }
        renderCountdown();
    }

    stateSelect.addEventListener('change', () => {
        demoState = stateSelect.value;
        previewOverride = true;
        demoStartedAt = null;
        render();
    });
    window.addEventListener('measurement-ui-change', event => {
        uiChanges += 1;
        const wasMeasuring = measuring;
        measuring = event.detail.running;
        if (!measuring) nextAt = null;
        previewOverride = false;
        preview.refreshConfiguration();
        if (preview.isDemo) {
            if (measuring && !wasMeasuring) {
                demoStartedAt = Date.now();
                demoState = 'collecting';
            } else if (!measuring) {
                demoStartedAt = null;
                demoState = 'idle';
            }
            stateSelect.value = demoState;
        } else {
            poll();
            schedulePolling();
        }
        render();
    });
    if (preview.isDemo) {
        setInterval(() => {
            if (!measuring || !demoStartedAt) return;
            const elapsed = Date.now() - demoStartedAt;
            const nextState = elapsed < 4000 ? 'collecting' : 'ready';
            if (window.Guide?.demoStage()) {
                render();
                return;
            }
            if (nextState === demoState) return;
            demoState = nextState;
            stateSelect.value = demoState;
            render();
        }, 1000);
    }
    window.addEventListener('classifier-preview-change', render);
    window.addEventListener('classifier-config-change', render);
    window.addEventListener('storage', event => {
        if (event.key === 'bmeClassifierPreviewV2' || event.key === null) render();
    });
    render();
    preview.refreshConfiguration();
    poll();
    schedulePolling();
    window.addEventListener('focus', () => { preview.refreshConfiguration(); preview.refreshModels(); });
    document.addEventListener('visibilitychange', () => {
        if (!document.hidden) {
            preview.refreshConfiguration();
            poll();
            schedulePolling();
        }
    });
    if (!preview.isDemo) setInterval(() => {
        if (!document.hidden) preview.refreshConfiguration();
    }, 10000);
    setInterval(renderCountdown, 1000);
});
