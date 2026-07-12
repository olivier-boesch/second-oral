/* Tri au clic sur les en-têtes marqués data-sortable, pour toute
 * <table class="sortable">. Réutilisable sur n'importe quelle page —
 * cf. liste_examinateurs.html. */
(function () {
    function cellValue(cell) {
        return (cell.dataset.sort !== undefined ? cell.dataset.sort : cell.textContent).trim();
    }

    function compare(a, b, numeric) {
        if (numeric) {
            return (parseFloat(a) || 0) - (parseFloat(b) || 0);
        }
        return a.localeCompare(b, 'fr', { sensitivity: 'base' });
    }

    function makeSortable(table) {
        const headerRow = table.querySelector('tr');
        const headers = Array.from(headerRow.querySelectorAll('th[data-sortable]'));

        headers.forEach(function (th) {
            th.addEventListener('click', function () {
                const asc = th.dataset.sortDir !== 'asc';
                headers.forEach(function (h) {
                    delete h.dataset.sortDir;
                    h.classList.remove('sort-asc', 'sort-desc');
                });
                th.dataset.sortDir = asc ? 'asc' : 'desc';
                th.classList.add(asc ? 'sort-asc' : 'sort-desc');

                const colIndex = Array.from(headerRow.children).indexOf(th);
                const numeric = th.dataset.sortType === 'numeric';
                const tbody = table.tBodies[0] || table;
                const rows = Array.from(tbody.querySelectorAll('tr')).filter(function (r) {
                    return r !== headerRow;
                });

                rows.sort(function (r1, r2) {
                    const cmp = compare(
                        cellValue(r1.children[colIndex]), cellValue(r2.children[colIndex]), numeric,
                    );
                    return asc ? cmp : -cmp;
                });

                rows.forEach(function (row, i) {
                    tbody.appendChild(row);
                    row.classList.remove('tr_light', 'tr_dark');
                    row.classList.add(i % 2 === 0 ? 'tr_light' : 'tr_dark');
                });
            });
        });
    }

    document.querySelectorAll('table.sortable').forEach(makeSortable);
})();
