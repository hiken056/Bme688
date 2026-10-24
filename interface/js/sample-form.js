// test or collect data on the main page, and the new measurement dialog
// collect: one card per cycle; one cycle is clean air, then a sample, then remove it
// test: nothing to fill in, predictions on anything until stop
(() => {
    const preview = window.ClassifierPreview;
    const CLASSES = [['clean_air', 'Clean air'], ['spicy', 'Spicy'], ['bad_smell', 'Bad smell']];
    const MAX_CYCLES = 6;
    const MINUTES_PER_CYCLE = 13;   // 3 min clean air + 10 min sample
    const MINUTES_BETWEEN = 3;      // settling after removing a sample

    // objects from the training data, typing a new one works too
    const KNOWN = {
        spicy: ['Amestec mediteranean', 'Anason stelat', 'Boia dulce', 'Busuioc', 'Cardamom', 'Ceai de fructe de padure',
            'Ceai de menta', 'Ceai de sunatoare', 'Ceai rece', 'Ceai verde', 'Chili', 'Chimen', 'Cimbrisor', 'Coriandru',
            'Creion nazal mentolat', 'Cuisoare', 'Curcuma', 'Curry', 'Foi de dafin', 'Garam masala', 'Halls Lime',
            'Ienibahar', 'Matcha', 'Nucsoara', 'Oregano', 'Pasta de dinti', 'Piper negru', 'Scortisoara',
            'Sirop Calmotusin'],
        bad_smell: ['Adidas transpirat', 'Branza telemea', 'Burete de bucatarie uzat', 'Cos de gunoi', 'Cos din baie',
            'Prosop murdar', 'Sosete transpirate', 'Ulei industrial', 'Varza', 'WC'],
        clean_air: ['Aer curat']
    };

    const list = document.getElementById('session-cycles');
    const plan = document.getElementById('session-plan');
    const suggestions = document.getElementById('known-objects');
    const countOut = document.getElementById('cycle-count');
    const minus = document.getElementById('cycle-minus');
    const plus = document.getElementById('cycle-plus');
    const cycleControl = document.getElementById('cycle-control');
    const modeInputs = [...document.querySelectorAll('input[name="measure-mode"]')];
    const modeHint = document.getElementById('mode-hint');
    const startButton = document.getElementById('session-start');
    const controls = cycleControl.closest('.duration-control');
    const escapeHtml = preview.escapeHtml;

    let running = false;
    let cycles = [];
    let count = 2;
    let mode = 'collect';
    let loaded = false;   // the controls stay hidden until the Pi's settings are in

    function load(key) {
        try { return localStorage.getItem(key); } catch (e) { return null; }
    }

    function save(key, value) {
        try { localStorage.setItem(key, value); } catch (e) {}
    }

    // objects typed before on this device keep their class
    function remembered() {
        try { return JSON.parse(load('bmeObjects')) || {}; } catch (e) { return {}; }
    }

    function knownObjects() {
        const out = {};
        Object.entries(KNOWN).forEach(([cls, names]) => names.forEach(name => { out[name] = cls; }));
        return Object.assign(out, remembered());
    }

    function classFor(name) {
        const key = name.trim().toLowerCase();
        const match = Object.entries(knownObjects()).find(([object]) => object.toLowerCase() === key);
        return match ? match[1] : '';
    }

    function blank() {
        return { object: '', cls: '', auto: false, distance: '', notes: '' };
    }

    function show() {
        controls.style.visibility = loaded ? '' : 'hidden';
        countOut.textContent = count;
        minus.disabled = running || count <= 1;
        plus.disabled = running || count >= MAX_CYCLES;
        modeInputs.forEach(input => {
            input.checked = input.value === mode;
            input.disabled = running;
        });
        cycleControl.hidden = mode === 'test';
        modeHint.textContent = mode === 'test'
            ? 'Warm-up, clean air, then bring anything. Predictions run until you press Stop.'
            : 'One cycle: clean air, a sample, then remove it. Use a different sample each time.';
    }

    // the choice is kept on the device, so it is the same on every phone and after a reboot
    async function loadSettings() {
        let saved = null;
        try { saved = await (await fetch('/api/guide-settings')).json(); } catch (e) {}
        if (saved?.mode) mode = saved.mode;
        if (saved?.cycles) count = saved.cycles;
        loaded = true;
        show();
    }

    function saveSettings() {
        fetch('/api/guide-settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ mode, cycles: count })
        }).catch(() => {});
    }

    function setCount(value) {
        count = Math.min(MAX_CYCLES, Math.max(1, value));
        show();
        saveSettings();
    }

    function minutesText() {
        const minutes = count * MINUTES_PER_CYCLE + (count - 1) * MINUTES_BETWEEN;
        return `${count} ${count === 1 ? 'cycle' : 'cycles'} after the warm-up, about ${minutes} min. ` +
            'Each cycle: 3 min clean air, 10 min with the sample, then remove it.';
    }

    function render() {
        while (cycles.length < count) cycles.push(blank());
        cycles.length = count;
        plan.textContent = minutesText();
        suggestions.innerHTML = Object.keys(knownObjects()).sort((a, b) => a.localeCompare(b))
            .map(name => `<option value="${escapeHtml(name)}"></option>`).join('');
        list.innerHTML = cycles.map((cycle, i) => `
            <fieldset class="cycle-card">
                <legend>Cycle ${i + 1}</legend>
                <label for="cycle-object-${i}">Sample</label>
                <input id="cycle-object-${i}" class="text-input" type="text" list="known-objects" maxlength="40"
                    autocomplete="off" placeholder="Type or pick, e.g. Scortisoara" data-index="${i}" data-field="object"
                    value="${escapeHtml(cycle.object)}">
                <div class="cycle-classes" role="radiogroup" aria-label="Class of cycle ${i + 1}">
                    ${CLASSES.map(([value, label]) => `
                        <label class="cycle-class">
                            <input type="radio" name="cycle-class-${i}" value="${value}" data-index="${i}" data-field="cls"
                                ${cycle.cls === value ? 'checked' : ''}>
                            <span>${label}</span>
                        </label>`).join('')}
                </div>
                <div class="cycle-row">
                    <div class="cycle-field cycle-distance">
                        <label for="cycle-distance-${i}">Distance</label>
                        <div class="cycle-unit">
                            <input id="cycle-distance-${i}" class="text-input" type="text" maxlength="4"
                                inputmode="decimal" placeholder="e.g. 5" data-index="${i}" data-field="distance"
                                value="${escapeHtml(cycle.distance)}">
                            <span>cm</span>
                        </div>
                    </div>
                    <div class="cycle-field cycle-notes">
                        <label for="cycle-notes-${i}">Notes <span>optional</span></label>
                        <input id="cycle-notes-${i}" class="text-input" type="text" maxlength="80"
                            placeholder="e.g. open jar" data-index="${i}" data-field="notes"
                            value="${escapeHtml(cycle.notes)}">
                    </div>
                </div>
            </fieldset>`).join('');
    }

    // a known object picks its class, until the class is chosen by hand
    list.addEventListener('input', (e) => {
        const { index, field } = e.target.dataset;
        if (index === undefined) return;
        const cycle = cycles[index];
        if (field === 'cls') {
            cycle.cls = e.target.value;
            cycle.auto = false;
            return;
        }
        cycle[field] = e.target.value;
        if (field === 'object' && (!cycle.cls || cycle.auto)) {
            const cls = classFor(cycle.object);
            cycle.cls = cls;
            cycle.auto = Boolean(cls);
            list.querySelectorAll(`input[name="cycle-class-${index}"]`).forEach(radio => {
                radio.checked = radio.value === cls;
            });
        }
    });

    minus.addEventListener('click', () => setCount(count - 1));
    plus.addEventListener('click', () => setCount(count + 1));
    modeInputs.forEach(input => input.addEventListener('change', () => {
        mode = input.value;
        show();
        saveSettings();
    }));

    window.addEventListener('measurement-ui-change', (e) => {
        running = Boolean(e.detail?.running);
        show();
    });

    function open() {
        cycles = [];
        render();
        (document.getElementById('cycle-object-0') || startButton).focus();
    }

    // checks the cards, returns the title and the cycles, or the first problem
    function read() {
        if (mode === 'test') return { title: 'Tests', mode, cycles: [] };
        for (let i = 0; i < cycles.length; i++) {
            const cycle = cycles[i];
            const name = `Cycle ${i + 1}`;
            const distance = cycle.distance.trim() === '' ? NaN : Number(cycle.distance.replace(',', '.'));
            if (!cycle.object.trim()) return { error: `${name}: write what the sample is`, focus: `cycle-object-${i}` };
            if (!cycle.cls) return { error: `${name}: choose Clean air, Spicy or Bad smell`, focus: `cycle-object-${i}` };
            if (!Number.isFinite(distance) || distance < 0) {
                return { error: `${name}: enter the distance in cm`, focus: `cycle-distance-${i}` };
            }
        }
        const items = cycles.map(cycle => ({
            object: cycle.object.trim(),
            sample_class: cycle.cls,
            distance_cm: Number(cycle.distance.replace(',', '.')),
            notes: cycle.notes.trim()
        }));
        const title = [...new Set(items.map(item => item.object))].join(' ').slice(0, 64);
        return { title, mode, cycles: items };
    }

    function remember(items) {
        const objects = remembered();
        items.forEach(item => { objects[item.object] = item.sample_class; });
        save('bmeObjects', JSON.stringify(objects));
    }

    show();
    loadSettings();
    window.SampleForm = { open, read, remember, mode: () => mode };
})();
