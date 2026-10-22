// error cards: one at a time, in the middle of the page, ok shows the next one
// demo: ?demo=1&errors=1 shows every card the device can send
(() => {
    const dialog = document.getElementById('error-card');
    const kindText = document.getElementById('error-kind');
    const countText = document.getElementById('error-count');
    const titleText = document.getElementById('error-title');
    const detailText = document.getElementById('error-detail');
    const fixText = document.getElementById('error-fix');
    const okButton = document.getElementById('error-ok');

    const queue = [];
    let shownCount = 0;

    // the card for each way a measurement can stop, the codes come from the backend
    function cardFor(code, message = '') {
        const stopped = 'Measurement stopped';
        switch (code) {
            case 'board': return { kind: stopped, title: 'No connection to the sensor board',
                detail: 'The sensors did not answer, so nothing was recorded.',
                fix: 'Check the cable between the Pi and the sensor board, then press Start again.' };
            case 'bus': return { kind: stopped, title: 'Sensor bus is off',
                detail: `The Pi cannot read the sensors (${message.includes('I2C') ? 'I2C' : 'SPI'}).`,
                fix: 'Turn on I2C and SPI in raspi-config, then reboot the Pi.' };
            case 'files': return { kind: stopped, title: 'Cannot save the recording',
                detail: 'The Pi could not write the measurement files.',
                fix: 'The SD card may be full. Delete old recordings in Files.' };
            default: return { kind: stopped, title: 'The sensor program stopped',
                detail: 'Everything recorded so far is saved.',
                fix: 'Press Start to try again. If it happens again, restart the Pi.' };
        }
    }

    // every card, for the demo
    const DEMO = [cardFor('board'), cardFor('bus', 'SPI'), cardFor('files'), cardFor('crashed')];

    function render(card) {
        kindText.textContent = card.kind;
        const total = shownCount + queue.length;
        countText.textContent = total > 1 ? `${shownCount} of ${total}` : '';
        titleText.textContent = card.title;
        detailText.textContent = card.detail;
        fixText.textContent = card.fix;
        dialog.dataset.kind = card.kind === 'Warning' ? 'warning' : 'error';
    }

    function next() {
        if (!queue.length) {
            dialog.close();
            return;
        }
        shownCount += 1;
        render(queue.shift());
        if (!dialog.open) dialog.showModal();
        okButton.focus();
    }

    // cards wait their turn behind the one on screen
    function show(cards) {
        queue.push(...(Array.isArray(cards) ? cards : [cards]));
        if (!dialog.open) {
            shownCount = 0;
            next();
        } else {
            countText.textContent = `${shownCount} of ${shownCount + queue.length}`;
        }
    }

    okButton.addEventListener('click', next);
    dialog.addEventListener('cancel', (e) => {
        // escape works like ok
        e.preventDefault();
        next();
    });

    const params = new URLSearchParams(window.location.search);
    if (window.ClassifierPreview?.isDemo && params.get('errors') === '1') {
        window.addEventListener('load', () => show(DEMO));
    }

    window.ErrorCard = { show, cardFor };
})();
