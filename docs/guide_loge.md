# Mode d'emploi — Loge

*À distribuer aux surveillants de loge le jour des épreuves.*

## 1. Connexion

Rendez-vous sur :

```
https://[URL_PUBLIQUE_DU_SITE]/login-loge
```

Renseignez le **nom de la loge** et le **mot de passe** indiqués sur votre papillon papier.

Un QR code figure également sur le papillon : il pointe vers votre fiche de loge et **pré-remplit le nom de la loge** sur l'écran de connexion, mais le mot de passe reste à saisir la première fois. Une fois connecté, la session reste active plusieurs heures : reflasher le QR (ou recharger la page) vous ramène directement sur la fiche sans redemander le mot de passe, tant que vous ne vous êtes pas déconnecté.

## 2. La liste des oraux

Une fois connecté, la page affiche le tableau de tous les oraux de votre loge : candidat, salle, matière, examinateur, heure de sujet, heure d'oral et heure de fin.

- **Ligne en rouge** : cet oral a été modifié depuis l'impression du papillon (changement d'horaire ou d'examinateur) — vérifiez l'information affichée à l'écran plutôt que le papier.
- **Pastille 🔴** à côté d'un candidat : il bénéficie d'un aménagement d'épreuve (temps de préparation majoré).
- La page se **met à jour automatiquement** dès qu'un changement survient (pas besoin de recharger ni de rafraîchir).

## 3. Le minuteur

Chaque ligne dispose d'un minuteur qui décompte le **temps de préparation** du candidat (entre l'heure de sujet et l'heure d'oral) :

| Bouton | Action |
|---|---|
| ▶ / ⏸ | Démarrer ou mettre en pause le décompte |
| ↺ | Réinitialiser le minuteur à zéro |

- Un **bip** retentit lorsqu'il reste 1 minute, puis une **triple sonnerie** à la fin du temps de préparation.
- Le son ne s'active qu'après un premier clic sur la page (contrainte des navigateurs) — cliquez n'importe où si vous n'entendez rien.
- Démarrez le minuteur au moment où vous remettez le sujet au candidat, afin qu'il sache quand se rendre en salle.

## 4. Affichage et filtres

Au-dessus du tableau, deux cases à cocher permettent d'adapter l'affichage :

- **Tout montrer** (décochée par défaut) : la colonne Examinateur est masquée tant que cette case n'est pas cochée.
- **Masquer les passés** : ne coche cette case que si vous voulez ne voir que les candidats qui restent à passer — les oraux marqués « Passé » (voir ci-dessous) disparaissent alors de la liste.

Le tableau réordonne aussi automatiquement les lignes : dès qu'un minuteur est démarré, la ligne correspondante remonte en haut de la liste, pour repérer d'un coup d'œil qui est en train de passer.

### Marquer un candidat comme passé

Chaque ligne dispose d'un bouton **Marquer passé** : cliquez dessus une fois le candidat envoyé en salle. Le bouton devient **✓ Passé** (cliquez à nouveau pour annuler). Cette information est enregistrée en base — elle ne disparaît donc pas au bout de 24h comme l'état des minuteurs, et reste disponible même après un rechargement de la page ou une reconnexion.

## 5. Autres liens utiles

En bas de page :
- **Liste des loges** — annuaire de toutes les loges du centre
- **Liste des salles** — annuaire de toutes les salles d'examen

En cas de problème (page bloquée, mot de passe refusé), contactez l'administration de l'épreuve.
