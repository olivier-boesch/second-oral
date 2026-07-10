(function () {
    function flashRow(tr, ok) {
        tr.style.transition = 'background-color .3s';
        tr.style.backgroundColor = ok ? '#d1fae5' : '#fecaca';
        setTimeout(() => { tr.style.backgroundColor = ''; }, 700);
    }

    window.reassignerLoge = function (idExaminateur, idLoge, radio) {
        const tr = radio.closest('tr');
        fetch('/gestion/reassignation-loges/assign', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': window.CSRF_TOKEN },
            body: JSON.stringify({ id_examinateur: idExaminateur, id_loge: idLoge }),
        })
            .then((r) => { if (!r.ok) throw new Error(); return r.json(); })
            .then(() => flashRow(tr, true))
            .catch(() => {
                flashRow(tr, false);
                alert("Impossible de réaffecter cette salle. Réessayez.");
            });
    };

    const formNouvelle = document.getElementById('form-nouvelle-loge');
    if (formNouvelle) {
        formNouvelle.addEventListener('submit', function (e) {
            e.preventDefault();
            const input = document.getElementById('nouvelle-loge-nom');
            const nom = input.value.trim();
            if (!nom) return;
            fetch('/gestion/reassignation-loges/nouvelle-loge', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRFToken': window.CSRF_TOKEN },
                body: JSON.stringify({ nom }),
            })
                .then((r) => {
                    if (!r.ok) return r.text().then((t) => { throw new Error(t); });
                    return r.json();
                })
                .then(() => { window.location.reload(); })
                .catch(() => {
                    alert("Impossible de créer cette loge (nom déjà utilisé ?).");
                });
        });
    }
})();
