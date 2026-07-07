// ── Sélecteur d'heure façon Android (roues H / MM défilantes) ────────────────
// Remplace le petit widget natif <input type="time"> (peu ergonomique sur
// desktop) par une saisie tactile façon Android : deux roues défilantes
// (heures, minutes) qui s'ouvrent au clic sur le champ.
//
// Usage : marquer le champ avec class="time-picker-field" (type="text"
// readonly, valeur "HH:MM"), ajouter data-effacable="1" si le champ peut être
// vidé (bouton "Vider" en plus du bouton "OK"), puis appeler
// initTimePickers() une fois le DOM prêt.
"use strict";

const TIME_PICKER_ITEM_H = 36;

function _creerRoueHeure(container, min, max, valeurInitiale) {
    const inner = document.createElement('div');
    inner.className = 'time-picker-col-inner';
    for (let i = min; i <= max; i++) {
        const item = document.createElement('div');
        item.className = 'time-picker-item';
        item.textContent = String(i).padStart(2, '0');
        item.dataset.value = String(i);
        inner.appendChild(item);
    }
    container.appendChild(inner);

    function valeurCourante() {
        const idx = Math.round(container.scrollTop / TIME_PICKER_ITEM_H);
        return Math.min(max, Math.max(min, min + idx));
    }
    function marquerSelection() {
        const v = valeurCourante();
        for (const el of inner.children) {
            el.classList.toggle('selected', Number(el.dataset.value) === v);
        }
    }
    function allerA(v, smooth) {
        container.scrollTo({ top: (v - min) * TIME_PICKER_ITEM_H, behavior: smooth ? 'smooth' : 'auto' });
    }
    allerA(valeurInitiale, false);
    marquerSelection();

    let timeoutId = null;
    container.addEventListener('scroll', () => {
        clearTimeout(timeoutId);
        timeoutId = setTimeout(() => { allerA(valeurCourante(), true); marquerSelection(); }, 90);
    });
    inner.addEventListener('click', (e) => {
        if (e.target.classList.contains('time-picker-item')) {
            allerA(Number(e.target.dataset.value), true);
            setTimeout(marquerSelection, 200);
        }
    });
    return { get: valeurCourante };
}

function _fermerTimePicker() {
    const popup = document.getElementById('active-time-picker');
    if (popup) {
        if (popup._onOutsideClick) document.removeEventListener('mousedown', popup._onOutsideClick);
        popup.remove();
    }
    const backdrop = document.getElementById('active-time-picker-backdrop');
    if (backdrop) backdrop.remove();
}

function _ouvrirTimePicker(inputEl) {
    _fermerTimePicker();
    const brut = (inputEl.value || '').trim();
    const [hBrut, mBrut] = brut.includes(':') ? brut.split(':') : ['8', '0'];
    const heureInit = Math.min(23, Math.max(0, parseInt(hBrut, 10) || 0));
    const minuteInit = Math.min(59, Math.max(0, parseInt(mBrut, 10) || 0));

    const backdrop = document.createElement('div');
    backdrop.className = 'time-picker-backdrop';
    backdrop.id = 'active-time-picker-backdrop';
    document.body.appendChild(backdrop);

    const popup = document.createElement('div');
    popup.className = 'time-picker-popup';
    popup.id = 'active-time-picker';

    const wheels = document.createElement('div');
    wheels.className = 'time-picker-wheels';

    const colH = document.createElement('div');
    colH.className = 'time-picker-col';
    const highlightH = document.createElement('div');
    highlightH.className = 'time-picker-highlight';
    colH.appendChild(highlightH);

    const sep = document.createElement('div');
    sep.className = 'time-picker-sep';
    sep.textContent = ':';

    const colM = document.createElement('div');
    colM.className = 'time-picker-col';
    const highlightM = document.createElement('div');
    highlightM.className = 'time-picker-highlight';
    colM.appendChild(highlightM);

    wheels.appendChild(colH);
    wheels.appendChild(sep);
    wheels.appendChild(colM);
    popup.appendChild(wheels);

    const actions = document.createElement('div');
    actions.className = 'time-picker-actions';
    if (inputEl.dataset.effacable === '1') {
        const btnVider = document.createElement('button');
        btnVider.type = 'button';
        btnVider.className = 'outline';
        btnVider.textContent = 'Vider';
        btnVider.onclick = () => {
            inputEl.value = '';
            inputEl.dispatchEvent(new Event('change'));
            _fermerTimePicker();
        };
        actions.appendChild(btnVider);
    }
    const btnOk = document.createElement('button');
    btnOk.type = 'button';
    btnOk.textContent = 'OK';
    actions.appendChild(btnOk);
    popup.appendChild(actions);

    document.body.appendChild(popup);

    // Les roues ont besoin d'être dans le DOM (dimensions connues) avant le
    // positionnement scroll initial — d'où l'ajout de `popup` juste au-dessus.
    const roueH = _creerRoueHeure(colH, 0, 23, heureInit);
    const roueM = _creerRoueHeure(colM, 0, 59, minuteInit);

    btnOk.onclick = () => {
        inputEl.value = `${String(roueH.get()).padStart(2, '0')}:${String(roueM.get()).padStart(2, '0')}`;
        inputEl.dispatchEvent(new Event('change'));
        _fermerTimePicker();
    };

    const rect = inputEl.getBoundingClientRect();
    popup.style.top  = `${rect.bottom + window.scrollY + 4}px`;
    popup.style.left = `${rect.left + window.scrollX}px`;

    const onOutsideClick = (e) => {
        if (!popup.contains(e.target) && e.target !== inputEl) _fermerTimePicker();
    };
    popup._onOutsideClick = onOutsideClick;
    setTimeout(() => document.addEventListener('mousedown', onOutsideClick), 0);
}

function initTimePickers() {
    document.querySelectorAll('.time-picker-field').forEach((el) => {
        el.addEventListener('click', () => _ouvrirTimePicker(el));
    });
}
