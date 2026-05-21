# agentic-cloud — startup brief

Brief de démarrage pour l'agent qui implémente ce projet. Repo créé le 2026-05-21 pendant l'audit centaur de asi0.

## Mission en une phrase

Livrer un CLI `cloud` qui unifie le pilotage de **plusieurs Nextcloud accounts** (multi-cloudron) avec des **modes par folder** : full sync, sync sélectif, ou VFS. Une couche d'abstraction propre par-dessus `rclone`, avec une expérience humaine et agent claire.

## Contexte

- asi0 a 3+ instances Nextcloud auto-hébergées sur son serveur Cloudron `crqpt.com` :
  - `cloud.crqpt.com` — perso (vault Obsidian, pictures, videos, downloads, admin)
  - `alysis.crqpt.com` — pro alysis (compta, clients, admin, marketing) — **à déployer**
  - `db-drive.crqpt.com` (alias actuel `drive`) — DB (Découvre Bitcoin)
  - éventuellement d'autres (`wmmw`, etc.)
- Actuellement il utilise **Nextcloud Desktop GUI (AppImage v33)** qui sync tout en full (133 G sur `db-drive` !).
- L'objectif : **virer le client GUI**, tout faire via rclone (WebDAV), avec un CLI propre par-dessus.

## Pourquoi pas Nextcloud Desktop seul ?

Recherche faite le 2026-05-21. Conclusion :
- Le mode VFS sur Linux est en **mode "suffix"** seulement (`.nextcloud` placeholders) — casse `find`, `grep`, IDE, Obsidian.
- Pas d'API CLI riche pour piloter (juste D-Bus pause/resume + edit `nextcloud.cfg` + restart).
- AppImage v33.0.0 a un bug crash sur VFS enable.
- Le maintainer a confirmé en mai 2026 : pas de FUSE prévu dans le client.

Verdict : `rclone` couvre les 3 modes (full / selective / VFS-FUSE-réel), gère multi-account natif, tout en CLI. Plus simple de tout passer dessus que de cohabiter avec NCD.

## Surface CLI cible

```bash
# Account management
cloud account add <name> <webdav-url>          # configure rclone remote
cloud account list                              # liste les accounts
cloud account test <name>                       # ping + cred check
cloud account remove <name>                     # cleanup remote + mounts

# Mount management (par folder, pas par account entier)
cloud mount <account>[:<subpath>] [--vfs|--full|--selective <patterns>...]
cloud unmount <account>[:<subpath>]
cloud status                                    # tous les mounts + cache size + état
cloud doctor                                    # diagnostic complet (creds, mounts, FUSE, systemd)

# Operations
cloud sync <account>[:<subpath>]                # one-shot sync (bisync)
cloud cache evict <account>                     # purge cache local
cloud cache size                                # taille cache par mount

# Convenience
cloud ls <account>[:<subpath>]                  # ls côté remote sans mount
cloud cat <account>:<path>                      # one-shot read
cloud push <local-file> <account>:<remote>      # one-shot upload
cloud push <local-file> <account>:<remote> --share [--expires <days>] [--password <p>]
                                                # upload + retourne URL publique partage
cloud share <account>:<remote-path>             # crée un lien sur un fichier déjà uploadé
cloud share <account>:<path> --revoke           # retire les liens publics sur ce path
cloud share-list <account>                      # liste tous les liens publics actifs

cloud pull <account>:<remote> <local>           # one-shot download
```

### Share URL — parité avec gogcli

Reproduire le UX de `gogcli` pour Google Drive : quand on push un fichier on doit pouvoir récupérer immédiatement le lien public (anyone-with-link), optionnellement avec password / expiry.

Implémentation : `rclone link` couvre déjà Nextcloud WebDAV. Pour les options avancées (password, expiry, scope = read-only vs upload, public vs internal), passer par l'API OCS Nextcloud directement (`/ocs/v2.php/apps/files_sharing/api/v1/shares`) — c'est plus riche que le simple `rclone link`.

Exemple sortie souhaitée :
```
$ cloud push devis-client-x.pdf alysis:clients/x/ --share --expires 30
✓ Uploaded devis-client-x.pdf → alysis:clients/x/devis-client-x.pdf
✓ Public link (expires 2026-06-20):
  https://alysis.crqpt.com/s/aBcDeFgHiJkL
```

Avec `--password` :
```
✓ Public link (password-protected, expires 2026-06-20):
  https://alysis.crqpt.com/s/aBcDeFgHiJkL
  Password: <random-12-char>
```

Le password est généré automatiquement (rand 12 chars) si non fourni, et imprimé pour copy-paste.



## Configuration centralisée

**Un seul fichier source de vérité** : `~/.config/agentic-cloud/config.toml`

```toml
[remotes.crqpt]
url = "https://cloud.crqpt.com/remote.php/dav/files/asi0"
mount = "~/clouds/crqpt"
mode = "vfs"
vfs_cache = "5G"
vfs_max_age = "168h"
auto = true

[remotes.alysis]
url = "https://alysis.crqpt.com/remote.php/dav/files/asi0"
mount = "~/clouds/alysis"
mode = "full"
auto = true

[remotes.db-drive]
url = "https://db-drive.crqpt.com/remote.php/dav/files/asi0"
mount = "~/clouds/db-drive"
mode = "selective"
selective_include = ["compta-2026", "current-projects"]
auto = true
```

Le CLI génère/maintient depuis ce TOML :
- `~/.config/rclone/rclone.conf` (les credentials WebDAV par account)
- `~/.config/systemd/user/cloud-<account>.service` (auto-mount)
- les exclude rules par mount

## Tech stack proposé

- **Langage** : Python + uv (cohérent avec vox, anna, qrcode dans centaur-infra)
- **Backend** : `rclone` (subprocess wrapper)
- **Config** : `tomllib` (stdlib Python 3.11+)
- **Auth** : app passwords Nextcloud, stockés dans rclone config (chiffré)
- **CLI framework** : `click` ou `typer`
- **Tests** : pytest, mock subprocess

## Structure du repo proposée

```
agentic-cloud/
├── README.md
├── pyproject.toml          (uv-managed)
├── src/
│   ├── cloud/
│   │   ├── __init__.py
│   │   ├── __main__.py     (entry)
│   │   ├── cli.py          (click commands)
│   │   ├── config.py       (load/save TOML)
│   │   ├── rclone.py       (subprocess wrapper)
│   │   ├── systemd.py      (unit generation)
│   │   ├── status.py       (parse mount state)
│   │   └── doctor.py       (diagnostics)
├── tests/
│   ├── test_config.py
│   ├── test_rclone.py
│   └── fixtures/
├── docs/
│   ├── design.md
│   ├── migration-from-ncd.md
│   └── vfs-on-linux.md
└── bin/
    └── cloud               (entry, points to src/cloud/__main__.py)
```

## Intégration centaur-infra (pattern existant)

Une fois v1 livrée, **symlink chain** standard :
- `~/centaur-infra/bin/cloud → ../../repos/Asi0Flammeus/agentic-cloud/bin/cloud`
- `~/.local/bin/cloud → /home/asi0/centaur-infra/bin/cloud`

(Pendant le dev, garde le repo séparé. Migration vers `centaur-infra/packages/` à décider une fois mature.)

## Constraints / non-negotiables

1. **Multi-account natif** dès v1 (pas hardcoded à un seul Nextcloud).
2. **Idempotent** : `cloud mount` deux fois ne casse rien. `cloud account add` avec un name existant met à jour.
3. **Pas de daemon propre** : tu réutilises systemd user units pour la persistence (auto-mount au boot).
4. **Pas de credentials en clair** dans le TOML : passe par rclone config crypté (mot de passe optionnel mais recommandé).
5. **Output dual** : human-readable par défaut, `--json` pour pipe agentique.
6. **Stop conditions** : si `rclone` n'est pas installé → message clair + lien install, pas de fallback silencieux.
7. **Codes retour** explicites : `0` ok, `1` config error, `2` mount error, `3` rclone subprocess error.

## Migration plan (pour asi0, post-v1)

1. **Phase A** : `cloud account add crqpt https://cloud.crqpt.com/remote.php/dav/files/asi0` + test mount VFS sur un sous-folder (e.g., `~/clouds/crqpt-test/`).
2. **Phase B** : valider 24h, pas de conflit avec le NCD GUI qui tourne en parallèle.
3. **Phase C** : kill le NCD GUI sync sur `vaultsync/` (mais garder le data local en backup). Remount `~/clouds/crqpt/` en full mode sur ce path, repointer `~/vault` symlink.
4. **Phase D** : add les 2 autres accounts (`alysis` une fois déployé, `db-drive`).
5. **Phase E** : `db-drive` en `selective` (cible : 133 G → 10 G actifs). Gain ~120 G disque.
6. **Phase F** : décommission NCD AppImage (garde-le installé mais désactive autostart).

## Documents de référence

- État laptop asi0 au 2026-05-21 : voir HTML `/tmp/audit-laptop-2026-05-21.html` (audit complet, 14 dimensions).
- Spec sibling `audit-agent` : `~/vaultsync/nondual-mind/_inbox/2026-05-21 - audit-agent — observability for Claude + Pi.md`.
- Convention package centaur : voir `~/centaur-infra/packages/qrcode/` (référence layout).
- rclone WebDAV docs : https://rclone.org/webdav/
- Nextcloud Desktop config schéma : https://docs.nextcloud.com/server/latest/user_manual/en/desktop/configfile.html (utile pour debug le client existant pendant migration).

## Stop condition de la session de dev

Considère v1 livrée quand :
- [ ] `cloud account add` + `list` + `test` + `remove` fonctionnent
- [ ] `cloud mount --vfs` et `cloud mount --full` fonctionnent sur un compte test
- [ ] `cloud status` montre un résumé lisible
- [ ] `cloud doctor` détecte au moins : rclone absent, FUSE absent, mount stale, cache plein
- [ ] systemd unit auto-mount fonctionne au reboot
- [ ] `cloud push <file> <account>:<path> --share` upload + retourne URL publique (parité gogcli)
- [ ] `cloud share <account>:<path>` sur fichier existant + `--revoke` + `share-list`
- [ ] README.md de base avec usage examples
- [ ] Au moins 1 test unitaire pour `config.py`
- [ ] Smoke test manuel passé sur `crqpt` account de asi0

Tout le reste (selective sync sophistiqué, `cloud pull`, `--json` output, intégration centaur-infra définitive, password/expiry sur shares) va en v1.1.

## Décision : Google Drive vs Nextcloud (contexte asi0)

`cloud` gère **uniquement Nextcloud** (via rclone WebDAV). Google Drive reste géré par `gog` (CLI existant `gogcli` pour les 3 comptes Google de asi0). C'est volontaire — deux écosystèmes, deux outils.

**Convention recommandée par asi0** (à graver dans `centaur-infra/AGENTS.md`) :
- **Nextcloud** (`cloud`) → vault, perso, admin sensible, comptabilité, intra-équipe alysis
- **Google Drive** (`gog`) → collab externe, livrables clients, co-édition Docs/Sheets, partages ponctuels grand public

Le test de routage : *"Si quelqu'un hors constellation centaur doit lire/co-éditer → Google. Sinon → Nextcloud."*

---

**À toi de jouer.** Lis ce brief, ouvre le repo, structure-toi, code de manière incrémentale. Demande à asi0 ce qui est ambigu avant de présupposer. Bonne route.
