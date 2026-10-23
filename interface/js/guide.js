// guided measurement: current phase, time left, and a signal when the person has to act
// the device runs it and this page shows it, the demo runs the same steps in the browser
(() => {
    const preview = window.ClassifierPreview;
    const params = new URLSearchParams(window.location.search);
    const SPEED = Math.max(1, Number(params.get('speed')) || 1);   // demo only: ?speed=60 makes a minute last a second
    const STUCK = params.get('stuck') === '1';                      // demo only: sensors that never settle
    // same as backend/guide.py
    const CLEAN_S = 180, SAMPLE_S = 600, SETTLE_S = 180;
    const WARM_LIMIT = 0.05, WARM_CHECKS = 3, WARM_EVERY_S = 30, WARM_HALF_S = 90, WARM_MAX_S = 1200;
    const WINDOW_MS = 120000;     // demo: one analysis window
    const REPEAT_SOUND_MS = 30000;
    const STORE = 'bmeGuide';
    const PAGE_TITLE = document.title;

    const card = document.getElementById('phase-card');
    const cycleText = document.getElementById('phase-cycle');
    const totalText = document.getElementById('phase-total');
    const titleText = document.getElementById('phase-title');
    const detailText = document.getElementById('phase-detail');
    const bar = document.getElementById('phase-bar');
    const barFill = document.getElementById('phase-bar-fill');
    const nextText = document.getElementById('phase-next');
    const nextTime = document.getElementById('phase-next-time');
    const phaseButton = document.getElementById('phase-button');
    const actionBar = document.getElementById('action-bar');
    const actionTitle = document.getElementById('action-title');
    const actionDetail = document.getElementById('action-detail');
    const actionButton = document.getElementById('action-button');

    let demo = preview.isDemo ? load() : null;   // demo plan kept in the browser
    let state = null;                             // what is shown, from the device or the demo
    let timer = null;
    let audio = null;
    let lastAction = null;
    let lastSound = 0;
    let seen = null;                              // 'got it' taps, per step
    let shown = null;                             // the step the bottom bar is about
    let cappedSeen = false;                       // the 20 min warm-up note was read
    let sending = false;
    let generation = 0;                           // grows at every stop, old answers are ignored

    function load() {
        try { return JSON.parse(localStorage.getItem(STORE)); } catch (e) { return null; }
    }

    function save() {
        try {
            if (demo) localStorage.setItem(STORE, JSON.stringify(demo));
            else localStorage.removeItem(STORE);
        } catch (e) {}
    }

    function clock(ms) {
        const seconds = Math.max(0, Math.round(ms / 1000));
        return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
    }

    // demo: the same steps as backend/guide.py, with a made-up warm-up
    function demoNow() {
        return demo.startedAt + (Date.now() - demo.startedAt) * SPEED;
    }

    function demoWarm(now) {
        const g = demo.guide;
        while (g.warm_at == null) {
            const t = g.warm.last_check == null ? demo.startedAt + 2 * WARM_HALF_S * 1000 : g.warm.last_check + WARM_EVERY_S * 1000;
            if (t > now) return;
            if (t - demo.startedAt >= WARM_MAX_S * 1000) {
                g.warm_at = t;
                g.warm.capped = true;
                return;
            }
            const drift = STUCK ? 0.2 : 0.6 * Math.exp(-(t - demo.startedAt) / 150000);   // a cold start settling
            g.warm.last_check = t;
            g.warm.drift = Math.round(drift * 1000) / 1000;
            g.warm.checks = drift < WARM_LIMIT ? g.warm.checks + 1 : 0;
            if (g.warm.checks >= WARM_CHECKS) g.warm_at = t;
        }
    }

    function cleanStarts(g) {
        if (g.warm_at == null) return [];
        if (g.mode === 'test') return [g.warm_at, ...g.recleans];
        return [g.warm_at, ...g.removed.map(t => t + SETTLE_S * 1000)].slice(0, g.cycles.length);
    }

    function planPhase(g, start, now) {
        if (g.warm_at == null) return { key: 'warmup', index: 0, start_ms: start };
        const starts = cleanStarts(g);
        if (g.mode === 'test') {
            const index = starts.length - 1, low = starts[index], high = low + CLEAN_S * 1000;
            return now < high ? { key: 'clean', index, start_ms: low, end_ms: high } : { key: 'testing', index, since_ms: high };
        }
        const n = g.cycles.length;
        for (let i = 0; i < n; i++) {
            if (i > 0 && now < g.removed[i - 1] + SETTLE_S * 1000) {
                return { key: 'settle', index: i, start_ms: g.removed[i - 1], end_ms: g.removed[i - 1] + SETTLE_S * 1000 };
            }
            const low = starts[i], high = low + CLEAN_S * 1000;
            if (now < high) return { key: 'clean', index: i, start_ms: low, end_ms: high };
            const placed = g.placed[i];
            if (placed == null) return { key: 'add', index: i, since_ms: high };
            if (now < placed + SAMPLE_S * 1000) return { key: 'sample', index: i, start_ms: placed, end_ms: placed + SAMPLE_S * 1000 };
            if (i < n - 1 && g.removed[i] == null) return { key: 'remove', index: i, since_ms: placed + SAMPLE_S * 1000 };
        }
        return { key: 'done', index: n - 1 };
    }

    function planLeft(g, phase, now) {
        if (g.mode === 'test') return null;
        const n = g.cycles.length;
        if (phase.key === 'done') return 0;
        if (phase.key === 'warmup') return (n * (CLEAN_S + SAMPLE_S) + (n - 1) * SETTLE_S) * 1000;
        const after = { settle: CLEAN_S + SAMPLE_S, clean: SAMPLE_S, add: SAMPLE_S, sample: 0, remove: 0 };
        return (phase.end_ms ? Math.max(0, phase.end_ms - now) : 0) + after[phase.key] * 1000
            + (n - 1 - phase.index) * (SETTLE_S + CLEAN_S + SAMPLE_S) * 1000;
    }

    function demoState() {
        const now = demoNow();
        demoWarm(now);
        const g = demo.guide;
        const phase = planPhase(g, demo.startedAt, now);
        return { active: true, mode: g.mode, cycles: g.cycles, phase, now_ms: now, left_ms: planLeft(g, phase, now),
            warm: g.warm, warm_limit: WARM_LIMIT, warm_checks: WARM_CHECKS };
    }

    // text and buttons for each step
    function describe(s) {
        const { phase, cycles, mode } = s;
        const cycle = cycles[phase.index] || {};
        const last = phase.index === cycles.length - 1;
        const distance = Number.isFinite(cycle.distance_cm) ? ` at ${cycle.distance_cm} cm` : '';
        switch (phase.key) {
            case 'warmup': {
                // ready below 5% change per minute, the bar fills as the checks pass
                const w = s.warm || {};
                const percent = value => Math.round((Math.exp(value) - 1) * 100);
                return { title: 'Warm-up', detail: `Settling. Ready below ${percent(s.warm_limit)}% change per minute.`,
                    next: 'Clean air', progress: (w.checks || 0) / s.warm_checks,
                    right: w.drift == null ? 'usually 4 to 10 min' : `now ${percent(w.drift)}% per min` };
            }
            case 'settle': return { title: 'Airing out', detail: 'Waiting for the last sample to clear.', next: 'Clean air' };
            case 'clean':
                if (mode === 'test') {
                    return { title: 'Clean air', detail: phase.index ? 'Keep the sample away. Measuring the new reference.'
                        : 'Keep the sample away. Measuring the reference.', next: 'Bring what you want to test' };
                }
                return { title: 'Clean air', detail: 'Measuring the air without the sample. This is the reference.',
                    next: `Add ${cycle.object}` };
            case 'add': return { title: 'Add the sample', detail: `Place ${cycle.object}${distance}.`, action: 'Sample placed' };
            case 'sample': return { title: cycle.object, detail: 'Keep the sample in place.',
                next: last ? 'Measurement ends' : `Remove ${cycle.object}` };
            case 'remove': return { title: 'Remove the sample', detail: `Take ${cycle.object} away.`, action: 'Sample removed' };
            case 'testing': return { title: 'Testing', detail: 'Bring anything close to the sensors. Moved to another room? Tap New clean air.',
                action: 'Got it', notice: 'Bring what you want to test', noticeDetail: 'Hold it close to the sensors.',
                button: 'New clean air' };
            default: return { title: 'All cycles done', detail: 'You can remove the sample.' };
        }
    }

    function beep() {
        if (!audio) return;
        try {
            [0, 0.25, 0.5].forEach((delay, i) => {
                const osc = audio.createOscillator();
                const gain = audio.createGain();
                const t = audio.currentTime + delay;
                osc.frequency.value = i === 1 ? 1175 : 880;
                osc.connect(gain);
                gain.connect(audio.destination);
                gain.gain.setValueAtTime(0.0001, t);
                gain.gain.exponentialRampToValueAtTime(0.35, t + 0.02);
                gain.gain.exponentialRampToValueAtTime(0.0001, t + 0.2);
                osc.start(t);
                osc.stop(t + 0.22);
            });
        } catch (e) {}
    }

    // browsers allow sound only after a tap on the page
    function unlockSound() {
        try {
            audio = audio || new (window.AudioContext || window.webkitAudioContext)();
            audio.resume();
        } catch (e) {}
    }
    document.addEventListener('pointerdown', unlockSound, { once: true });

    function render() {
        if (!state?.active) {
            card.hidden = true;
            actionBar.hidden = true;
            document.title = PAGE_TITLE;
            return;
        }
        const { phase, cycles, now_ms: now } = state;
        let text = describe(state);
        const n = cycles.length;
        let key = `${phase.key}-${phase.index}`;
        // warm-up stopped at the 20 min limit, tell once
        if (state.warm?.capped && phase.key !== 'warmup' && !text.action && !cappedSeen) {
            text = { ...text, action: 'Got it', notice: 'Sensors still settling',
                noticeDetail: 'They kept changing for 20 min. Predictions may be less reliable.' };
            key = 'capped';
        }
        shown = key;

        card.hidden = false;
        card.dataset.phase = phase.key;
        cycleText.textContent = state.mode === 'test' ? (phase.index ? `Test, clean air ${phase.index + 1}` : 'Test')
            : phase.key === 'warmup' ? `${n} ${n === 1 ? 'cycle' : 'cycles'} planned` : `Cycle ${phase.index + 1} of ${n}`;
        totalText.textContent = state.left_ms == null || phase.key === 'done' ? ''
            : `about ${Math.max(1, Math.round(state.left_ms / 60000))} min ${phase.key === 'warmup' ? 'after the warm-up' : 'left'}`;
        titleText.textContent = text.title;
        detailText.textContent = text.detail;

        // progress to the next step: time, or warm-up checks passed
        const timed = Boolean(phase.end_ms) || phase.key === 'warmup';
        bar.hidden = !timed;
        nextText.parentElement.hidden = !timed;
        if (timed) {
            const progress = phase.key === 'warmup' ? text.progress
                : Math.min(1, Math.max(0, (now - phase.start_ms) / (phase.end_ms - phase.start_ms)));
            barFill.style.width = `${(progress * 100).toFixed(1)}%`;
            nextText.textContent = `Next: ${text.next}`;
            nextTime.textContent = text.right || `in ${clock(phase.end_ms - now)}`;
        }
        phaseButton.hidden = !text.button;
        if (text.button) {
            phaseButton.textContent = text.button;
            phaseButton.disabled = sending;
        }

        // bar at the bottom, sound and tab title when the person has to act
        const needed = text.action && !(text.notice && seen === key);
        if (needed) {
            actionBar.hidden = false;
            actionTitle.textContent = text.notice || text.title;
            actionDetail.textContent = text.noticeDetail || text.detail;
            actionButton.textContent = text.action;
            actionButton.disabled = sending;
            if (key !== lastAction || (!text.notice && Date.now() - lastSound > REPEAT_SOUND_MS)) {
                beep();
                lastSound = Date.now();
            }
            lastAction = key;
            document.title = Math.floor(Date.now() / 1000) % 2 ? `${text.notice || text.title}!` : PAGE_TITLE;
        } else {
            actionBar.hidden = true;
            lastAction = needed ? lastAction : null;
            document.title = PAGE_TITLE;
        }
    }

    // a collection ends on its own, the device stops it and the demo stops here
    function finished() {
        beep();
        window.dispatchEvent(new Event('guide-finished'));
    }

    async function refresh() {
        if (demo) {
            state = demoState();
            if (state.phase.key === 'done' && !demo.finished) {
                demo.finished = true;
                save();
                finished();
            }
            save();
            render();
            return;
        }
        if (preview.isDemo) return;
        const asked = generation;
        try {
            const next = await (await fetch('/api/guide')).json();
            // stopped while the answer was on its way: keep the card hidden
            if (asked !== generation) return;
            if (state?.active && !next.active && next.finished) finished();
            state = next;
            render();
        } catch (e) {}
    }

    function show() {
        clearInterval(timer);
        refresh();
        timer = setInterval(refresh, 1000);
    }

    function hide() {
        generation += 1;
        clearInterval(timer);
        timer = null;
        state = null;
        render();
    }

    // taps go to the device, which accepts them only at the right step
    async function send(action) {
        const phase = state?.phase;
        if (!phase || sending) return;
        if (demo) {
            const g = demo.guide;
            if (action === 'reclean') g.recleans.push(demoNow());
            else g[action][phase.index] = demoNow();
            save();
            refresh();
            return;
        }
        sending = true;
        render();
        try {
            const response = await fetch(`/api/guide/${action}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ index: phase.index })
            });
            state = await response.json();
        } catch (e) {}
        sending = false;
        refresh();
    }

    actionButton.addEventListener('click', () => {
        unlockSound();
        const key = state?.phase?.key;
        if (shown !== 'capped' && key === 'add') send('placed');
        else if (shown !== 'capped' && key === 'remove') send('removed');
        else {
            if (shown === 'capped') cappedSeen = true;
            else seen = shown;
            render();
        }
    });
    phaseButton.addEventListener('click', () => {
        unlockSound();
        if (state?.phase?.key === 'testing') send('reclean');
    });

    window.addEventListener('measurement-ui-change', (e) => {
        if (e.detail?.running) {
            show();
        } else {
            // one last look: did the cycles end the measurement
            const guided = !preview.isDemo && state?.active;
            demo = null;
            save();
            hide();
            if (guided) {
                fetch('/api/guide').then(r => r.json()).then(s => { if (s.finished) finished(); }).catch(() => {});
            }
        }
    });

    // demo: what the prediction panel should show, following the steps
    function demoStage() {
        if (!demo) return null;
        const s = demoState();
        const g = demo.guide, now = s.now_ms, phase = s.phase;
        const ended = cleanStarts(g).map((low, i) => [i, low + CLEAN_S * 1000]).filter(w => w[1] <= now);
        let from = null;
        if (g.mode === 'test' && phase.key === 'testing') from = phase.since_ms;
        if (g.mode === 'collect' && ['sample', 'remove'].includes(phase.key)) from = g.placed[phase.index];
        const cycle = ended.length ? ended[ended.length - 1][0] + 1 : null;
        if (from == null) return { key: cycle && g.mode === 'collect' ? 'waiting_sample' : 'waiting_clean_air', cycle, mode: g.mode };
        const left = from + WINDOW_MS - now;
        const sample = g.mode === 'collect' ? g.cycles[phase.index].sample_class : 'clean_air';
        return left > 0 ? { key: 'collecting', left_s: left / 1000, cycle, mode: g.mode }
            : { key: 'ready', cycle, sample, mode: g.mode };
    }

    function start(mode, cycles) {
        unlockSound();
        seen = null;
        cappedSeen = false;
        if (!preview.isDemo) return;
        demo = { startedAt: Date.now(), finished: false, guide: {
            mode, cycles, placed: [], removed: [], recleans: [], warm_at: null,
            warm: { checks: 0, last_check: null, drift: null, capped: false } } };
        save();
    }

    window.Guide = { start, demoStage };
})();
