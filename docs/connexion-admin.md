# Connexion administrateur — BACKEND #18

## Parcours

Cette branche utilise le compte créé par #17 et les tables de #16, sans nouvelle migration.

1. `GET /admin/csrf` renvoie un jeton CSRF et pose le cookie de liaison HttpOnly
   `cardioscope_csrf`. Le contexte anonyme expire après 10 minutes.
2. `POST /admin/login` reçoit le JSON username/password, les cookies, Origin
   autorisé et X-CSRF-Token. Argon2id vérifie le mot de passe sans le transformer.
   Identifiant inconnu, compte inactif et mauvais mot de passe donnent la même erreur.
3. Le succès crée une session de 8 heures avec un jeton aléatoire de 32 octets.
   Le cookie `cardioscope_admin` est HttpOnly, SameSite=Lax, Path=/, sans Domain.
   Seul son hash SHA-256 est stocké en base ; aucun jeton de session dans le JSON.
4. Le contexte CSRF est renouvelé et lié à la session. Le frontend redemande
   /admin/csrf pour récupérer son nouveau jeton.
5. `GET /admin/me` vérifie compte actif, révocation, expiration absolue et
   inactivité de 30 minutes. Un succès actualise last_seen_at sans prolonger les 8 heures.

Chaque GET /admin/csrf renouvelle le contexte de ce navigateur. Un autre onglet
avec un ancien jeton doit le redemander après un refus 403. Les contextes
authentifiés expirent aussi après 10 minutes, sans dépasser la session. Les hash
de liaison et du jeton CSRF sont stockés ; le jeton CSRF est renvoyé en JSON.
Les réponses admin et erreurs contrôlées portent Cache-Control: no-store.

## Configuration et essai

- Appliquer `python -m alembic upgrade head` et initialiser l’admin (#17).
- CORS_ORIGINS contient les origines exactes, par exemple http://localhost:5174
  pour le frontend Docker local ; aucun wildcard. Le login vérifie aussi Origin.
- AUTH_COOKIE_SECURE=true par défaut ; false uniquement pour HTTP local.
  Sous Compose, transmettre explicitement cette variable au service backend :
  sa présence dans un .env seul ne suffit pas. Conserver true en HTTPS.
- Le frontend utilise `credentials: include`. CORS autorise les cookies et
  X-CSRF-Token pour les origines configurées.

Dans un client HTTP conservant les cookies : appeler /admin/csrf, envoyer le login
avec username/password, Origin et X-CSRF-Token obtenu, puis appeler /admin/me.
Ne pas enregistrer le vrai mot de passe dans une commande ou une capture.
Swagger présente les corps et en-têtes ; le navigateur gère les cookies selon
HTTP/HTTPS et leur origine.

## Limitation des tentatives

Après 5 échecs du même couple identifiant/adresse réseau sur 15 minutes, les essais
suivants reçoivent 429 avec Retry-After jusqu’à la fin de cette fenêtre. Une limite
globale de 30 échecs par adresse sur 15 minutes empêche de changer simplement
d’identifiant. Une réussite remet à zéro seulement le compteur du couple.
Les compteurs et verrous PostgreSQL sont partagés entre workers et sérialisent les
tentatives concurrentes. Un identifiant inconnu effectue aussi une vérification Argon2.

La clé réseau utilise request.client.host, jamais un X-Forwarded-For lu directement.
Configurer les proxies de confiance dans APP #20. Sans ce réglage, plusieurs clients
derrière Nginx peuvent partager la limite réseau. Les lignes expirées sont réutilisées ;
la purge périodique des compteurs, contextes et sessions reste à intégrer à l’exploitation.

## Erreurs

| Statut | Code | Cause |
| --- | --- | --- |
| 401 | invalid_credentials | Login refusé, cause non révélée |
| 401 | unauthenticated | /me sans session valide |
| 403 | csrf_failed | Origine, contexte ou jeton refusé |
| 422 | validation_error | Corps invalide, sans écho du mot de passe |
| 429 | rate_limited | Limite atteinte, Retry-After en secondes |
| 503 | storage_unavailable | Base/configuration indisponible |

/health conserve son contrat et ne constitue pas une vérification de la base.

## Validation et périmètre

`python -m unittest discover -s tests`

Activer CARDIOSCOPE_TEST_POSTGRES=1 avec les variables DB_* pour les tests HTTP.
Ils créent et suppriment une base cardioscope_test_<uuid>, jamais la base applicative.
Ils couvrent login, cookies/hash, CSRF, origines, compte inactif, expirations,
révocation, erreurs, limitation globale et tentatives concurrentes avec des secrets fictifs.

Les routes métier `/predict` et `/model-info`, ainsi que `/admin/me`, exigent une
session administrateur valide. `/health`, `/admin/csrf` et `/admin/login` restent
accessibles sans session. La déconnexion est traitée séparément dans la tâche #19.
L’image Docker doit être reconstruite pour appliquer la protection sur `/predict`.

Référence : [OWASP CSRF](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html).
