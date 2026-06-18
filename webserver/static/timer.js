(function () {
    const LOGE_ID = window.LOGE_ID;

    // ── AudioContext — créé au premier son joué ───────────────────────────────
    let _ctx = null;
    function beep(freq, dur, vol, delay) {
        delay = delay || 0;
        if (!_ctx) _ctx = new (window.AudioContext || window.webkitAudioContext)();
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

    // ── localStorage ─────────────────────────────────────────────────────────
    function lsKey(numero, sujet) {
        return 'timer_' + LOGE_ID + '_' + numero + '_' + sujet;
    }
    function loadState(numero, sujet) {
        try {
            const raw = localStorage.getItem(lsKey(numero, sujet));
            if (!raw) return null;
            const s = JSON.parse(raw);
            if (s.running && s.startedAt) {
                s.elapsed = (s.elapsed || 0) + Math.floor((Date.now() - s.startedAt) / 1000);
                s.startedAt = Date.now();
            }
            return s;
        } catch (e) { return null; }
    }
    function saveState(numero, sujet, state) {
        try { localStorage.setItem(lsKey(numero, sujet), JSON.stringify(state)); } catch (e) {}
    }

    // ── Initialisation de chaque timer ────────────────────────────────────────
    document.querySelectorAll('[data-timer]').forEach(function (cell) {
        const numero    = cell.dataset.numero;
        const sujet     = cell.dataset.sujet;
        const oral      = cell.dataset.oral;
        const totalSecs = hhmm2secs(oral) - hhmm2secs(sujet);

        if (totalSecs <= 0) { cell.textContent = '—'; return; }

        const display  = cell.querySelector('.timer-display');
        const btnPlay  = cell.querySelector('.timer-btn:nth-child(2)');
        const btnReset = cell.querySelector('.timer-btn:nth-child(3)');

        let state    = loadState(numero, sujet) || { elapsed: 0, running: false, startedAt: null };
        let interval = null;
        let warnDone = state.elapsed >= totalSecs - 60;
        let endDone  = state.elapsed >= totalSecs;

        function remaining() { return Math.max(0, totalSecs - state.elapsed); }

        function render() {
            const rem = remaining();
            display.textContent = secs2display(rem);
            display.classList.toggle('warn',  rem > 0 && rem <= 60);
            display.classList.toggle('ended', rem === 0);
            btnPlay.textContent = state.running ? '⏸' : '▶';
            btnPlay.disabled    = (rem === 0);
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

        btnPlay.addEventListener('click',  function () { state.running ? pause() : start(); });
        btnReset.addEventListener('click', reset);

        if (state.running && remaining() > 0) {
            interval = setInterval(tick, 1000);
        } else if (state.running && remaining() === 0) {
            state.running = false;
            endDone = true;
        }

        render();
    });
}());
