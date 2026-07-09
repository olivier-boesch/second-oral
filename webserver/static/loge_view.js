(function () {
    "use strict";
    const table   = document.getElementById('oraux-table');
    const tbody   = document.getElementById('oraux-tbody');
    const cbExam  = document.getElementById('toggle-examinateurs');
    const cbPasse = document.getElementById('toggle-passes');
    if (!table || !tbody) return;

    // ── Tri dynamique : les oraux dont le minuteur est déclenché montent en haut ──
    function reorderRows() {
        const rows = Array.from(tbody.rows);
        rows.forEach(function (row, i) { row.dataset.origIndex = i; });
        rows.sort(function (a, b) {
            const ra = a.querySelector('.timer-display.running') ? 0 : 1;
            const rb = b.querySelector('.timer-display.running') ? 0 : 1;
            if (ra !== rb) return ra - rb;
            return Number(a.dataset.origIndex) - Number(b.dataset.origIndex);
        });
        rows.forEach(function (row) { tbody.appendChild(row); });
    }

    // Se déclenche à chaque mise à jour d'un minuteur (running/warn/ended
    // togglés par timer.js toutes les secondes) : le tri reste à jour sans
    // rechargement de page.
    new MutationObserver(reorderRows).observe(tbody, {
        attributes: true, attributeFilter: ['class'], subtree: true,
    });
    reorderRows();

    // ── Case "Tout montrer" : les examinateurs sont cachés par défaut (CSS) ──
    if (cbExam) {
        cbExam.addEventListener('change', function () {
            table.classList.toggle('show-examinateurs', cbExam.checked);
        });
    }

    // ── Filtre "Masquer les passés" ──────────────────────────────────────────
    function applyPasseFilter() {
        const hide = !!(cbPasse && cbPasse.checked);
        Array.from(tbody.rows).forEach(function (row) {
            row.style.display = (hide && row.dataset.passage === 'true') ? 'none' : '';
        });
    }
    if (cbPasse) {
        cbPasse.addEventListener('change', applyPasseFilter);
    }

    // ── Bouton "Marquer passé" : bascule persistée en base (pas seulement Redis) ──
    function togglePassage(btn) {
        const row    = btn.closest('tr');
        const idOral = btn.dataset.idOral;
        const next   = row.dataset.passage !== 'true';
        fetch(`/loge/${encodeURIComponent(window.LOGE_ID)}/passage/${idOral}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': window.CSRF_TOKEN },
            body: JSON.stringify({ passage: next }),
        })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (res) {
                if (!res) return;
                row.dataset.passage = res.passage ? 'true' : 'false';
                btn.textContent = res.passage ? '✓ Passé' : 'Marquer passé';
                btn.classList.toggle('is-passed', res.passage);
                applyPasseFilter();
            })
            .catch(function () {});
    }
    tbody.querySelectorAll('.passage-btn').forEach(function (btn) {
        btn.addEventListener('click', function () { togglePassage(btn); });
    });
}());
