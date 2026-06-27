(function () {
    const LOGE_ID    = window.LOGE_ID;
    const CSRF_TOKEN = window.CSRF_TOKEN;
    const API_URL    = '/loge/timer-state';

    // ── AudioContext — créé au premier geste sur la page ─────────────────────
    let _ctx = null;
    function initAudio() {
        if (_ctx) return;
        _ctx = new (window.AudioContext || window.webkitAudioContext)();
    }
    ['click', 'keydown', 'touchstart'].forEach(function (evt) {
        document.addEventListener(evt, initAudio, { once: true, passive: true });
    });

    function beep(freq, dur, vol, delay) {
        if (!_ctx) return;
        delay = delay || 0;
        const ctx = _ctx;
        const schedule = function () {
            const t = ctx.currentTime + delay;
            const osc = ctx.createOscillator();
            const g   = ctx.createGain();
            osc.connect(g); g.connect(ctx.destination);
            osc.type = 'sine';
            osc.frequency.value = freq;
            g.gain.setValueAtTime(vol || 0.3, t);
            g.gain.exponentialRampToValueAtTime(0.001, t + dur);
            osc.start(t); osc.stop(t + dur);
        };
        if (ctx.state === 'suspended') { ctx.resume().then(schedule); } else { schedule(); }
    }
    function playWarning() { beep(880, 0.12, 0.25); }
    function playEnd() {
        beep(880,  0.15, 0.35, 0.00);
        beep(880,  0.15, 0.35, 0.30);
        beep(1100, 0.45, 0.40, 0.60);
    }

    // ── Utilitaires temps ─────────────────────────────────────────────────────
    function hhmm2secs(hhmm) {
        const p = hhmm.split(':').map(Number);
        return p[0] * 3600 + p[1] * 60;
    }
    function secs2display(s) {
        if (s <= 0) return '00:00';
        const m = Math.floor(s / 60), sec = s % 60;
        return String(m).padStart(2, '0') + ':' + String(sec).padStart(2, '0');
    }

    // ── Redis via API ─────────────────────────────────────────────────────────
    function saveState(numero, sujet, state) {
        fetch(API_URL, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CSRF_TOKEN },
            body: JSON.stringify({ loge: LOGE_ID, numero: numero, sujet: sujet,
                                   elapsed: state.elapsed, running: state.running,
                                   startedAt: state.startedAt }),
        }).catch(function () {});
    }

    // ── Initialisation de chaque timer à partir des états reçus ──────────────
    function initTimer(cell, serverState) {
        const numero    = cell.dataset.numero;
        const sujet     = cell.dataset.sujet;
        const oral      = cell.dataset.oral;
        const totalSecs = hhmm2secs(oral) - hhmm2secs(sujet);

        if (totalSecs <= 0) { cell.textContent = '—'; return; }

        const display  = cell.querySelector('.timer-display');
        const btnPlay  = cell.querySelector('.timer-btn[data-action="play"]');
        const btnReset = cell.querySelector('.timer-btn[data-action="reset"]');

        let state = serverState || { elapsed: 0, running: false, startedAt: null };

        // Rattraper le temps écoulé si le timer tournait pendant le rechargement
        if (state.running && state.startedAt) {
            state.elapsed = (state.elapsed || 0) + Math.floor((Date.now() - state.startedAt) / 1000);
            state.startedAt = Date.now();
        }

        let interval = null;
        let warnDone = state.elapsed >= totalSecs - 60;
        let endDone  = state.elapsed >= totalSecs;

        function remaining() { return Math.max(0, totalSecs - state.elapsed); }

        function render() {
            const rem = remaining();
            display.textContent = secs2display(rem);
            display.classList.toggle('running', state.running && rem > 60);
            display.classList.toggle('warn',    rem > 0 && rem <= 60);
            display.classList.toggle('ended',   rem === 0);
            if (btnPlay)  { btnPlay.textContent = state.running ? '⏸' : '▶'; btnPlay.disabled = (rem === 0); }
        }

        function tick() {
            state.elapsed++;
            const rem = remaining();
            if (!warnDone && rem <= 60 && rem > 0) { warnDone = true; playWarning(); }
            if (rem === 0 && !endDone) {
                endDone = true;
                state.running = false;
                clearInterval(interval); interval = null;
                playEnd();
            }
            state.startedAt = Date.now();
            saveState(numero, sujet, state);
            render();
        }

        function start() {
            if (state.running || remaining() === 0) return;
            state.running   = true;
            state.startedAt = Date.now();
            saveState(numero, sujet, state);
            render();
            interval = setInterval(tick, 1000);
        }

        function pause() {
            state.running   = false;
            state.startedAt = null;
            clearInterval(interval); interval = null;
            saveState(numero, sujet, state);
            render();
        }

        function reset() {
            clearInterval(interval); interval = null;
            state    = { elapsed: 0, running: false, startedAt: null };
            warnDone = false;
            endDone  = false;
            saveState(numero, sujet, state);
            render();
        }

        if (btnPlay)  btnPlay.addEventListener('click',  function () { initAudio(); state.running ? pause() : start(); });
        if (btnReset) btnReset.addEventListener('click', reset);

        if (state.running && remaining() > 0) {
            interval = setInterval(tick, 1000);
        } else if (state.running && remaining() === 0) {
            state.running = false;
            endDone = true;
        }

        render();
    }

    // ── Chargement batch des états depuis Redis ───────────────────────────────
    fetch(`${API_URL}?loge=${encodeURIComponent(LOGE_ID)}`)
        .then(function (r) { return r.ok ? r.json() : {}; })
        .catch(function () { return {}; })
        .then(function (states) {
            document.querySelectorAll('[data-timer]').forEach(function (cell) {
                const slot = cell.dataset.numero + '_' + cell.dataset.sujet;
                initTimer(cell, states[slot] || null);
            });
        });
}());
