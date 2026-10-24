// guided measurement: current phase, time left, and a signal when the person has to act
// the device runs it, this page shows it
(() => {
    const REPEAT_SOUND_MS = 30000;
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

    let state = null;                             // what is shown, from the device
    let timer = null;
    let audio = null;
    let lastAction = null;
    let lastSound = 0;
    let seen = null;                              // 'got it' taps, per step
    let shown = null;                             // the step the bottom bar is about
    let cappedSeen = false;                       // the 20 min warm-up note was read
    let sending = false;
    let generation = 0;                           // grows at every stop, old answers are ignored

    function clock(ms) {
        const seconds = Math.max(0, Math.round(ms / 1000));
        return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
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

    // a collection ends on its own, the device stops it
    function finished() {
        beep();
        window.dispatchEvent(new Event('guide-finished'));
    }

    async function refresh() {
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
            const guided = state?.active;
            hide();
            if (guided) {
                fetch('/api/guide').then(r => r.json()).then(s => { if (s.finished) finished(); }).catch(() => {});
            }
        }
    });

    // a new measurement: sound on, notes not read yet
    function start() {
        unlockSound();
        seen = null;
        cappedSeen = false;
    }

    window.Guide = { start };
})();
