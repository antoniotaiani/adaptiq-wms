# AdaptiQ WMS — Checklist Deploy in Produzione

Documento di lavoro creato il 2026-09-17. Traccia le decisioni prese e i passi da eseguire
per portare AdaptiQ WMS da ambiente locale (Docker Compose) a un VPS pubblico con Coolify.

## Decisioni prese

- **Hosting**: Netcup VPS 500 G12 — 2 vCPU, 4 GB RAM DDR5 ECC, 128 GB NVMe, ~€4,96/mese (escl. IVA)
  - Alternativa se servisse più margine: VPS 1000 G12 (4 vCPU, 8 GB RAM, ~€8,72/mese) — upgrade
    possibile in seguito dal pannello clienti Netcup, stessa generazione G12, senza ricreare il server.
    **Verificato**: con abbonamento prepagato, l'upgrade a metà contratto NON fa perdere soldi —
    il periodo residuo non consumato viene accreditato e compensato sul nuovo piano.
  - Location: **Norimberga (NUE)**, Germania
  - Confronto fatto anche con Hetzner (più caro, Trustpilot 3.0/5) e Contabo (Trustpilot 4.6/5 ma
    reputazione storica di CPU condivise sovrapposte) — Netcup scelto per rapporto specifiche/prezzo
    e reputazione (Trustpilot 3.9/5)
- **Sistema operativo**: Debian 13 (deciso automaticamente da Netcup, non Ubuntu 24.04 come da
  piano iniziale — nessun problema, Coolify lo supporta ufficialmente)
- **Piattaforma di deploy**: Coolify (self-hosted, open source, gratuito) installato sul VPS
- **Worker Gunicorn**: ridotti da 4 a 2 in `docker-compose.yml` per adattarsi alla RAM del VPS 500 G12
- **Dominio**: `adaptiqwms.com`, acquistato su **Netcup** in bundle col VPS (~€14/anno, prezzo
  dichiarato costante per tutta la durata del contratto). Valutate e scartate `adaptiq-wms.com`,
  `atalogistics-wms.com` (disponibili, preferenza stilistica) e `adaptiq.com` (non disponibile).
  Registrar iniziale Porkbun scartato: differenza di prezzo trascurabile (~€4/anno) a fronte della
  comodità di un unico account/fattura con Netcup.
- **Backup**: Cloudflare R2 (piano gratuito, 10 GB/mese, download sempre gratuito) — vedi Fase G.

## FASE A — Acquisto del VPS ✅ COMPLETATA

- [x] Ordine completato (2026-09-18): VPS 500 G12 + dominio `adaptiqwms.com`, verifica anti-frode
  superata, credenziali ricevute.

## FASE B — Configurazione iniziale del server ✅ COMPLETATA

- [x] Connessione: `ssh root@152.53.239.38` (hostname: v2202609421992523561.supersrv...)
- [x] Sistema aggiornato (`apt update && apt upgrade -y`) — Debian 13/trixie
- [x] `curl` già preinstallato su questa immagine
- [ ] (Facoltativo, rimandato) Creare un utente non-root con sudo per l'uso quotidiano

## FASE C — Installazione di Coolify ✅ COMPLETATA

- [x] Installato con successo (2026-09-18) — Coolify v4.3.21, pannello su http://152.53.239.38:8000
- [x] Account amministratore Coolify creato
- [x] Server "This machine" aggiunto (setup single-server, Coolify e app sullo stesso VPS)
- [ ] **Da fare appena possibile**: backup del file `/data/coolify/source/.env` in un posto sicuro
  fuori dal server (es. password manager) — contiene le chiavi/segreti dell'istanza Coolify

## FASE D — Dominio e DNS ✅ COMPLETATA

- [x] Registrato `adaptiqwms.com` su Netcup (bundle col VPS)
- [x] Record A creato per `adaptiqwms.com` → `152.53.239.38` (Netcup CloudDNS, TTL 3600)
- [x] Record A creato per `www.adaptiqwms.com` → `152.53.239.38` (Netcup CloudDNS, TTL 3600)

## FASE E — Deploy dell'app in Coolify ✅ PRIMO DEPLOY RIUSCITO (2026-09-18)

- [x] Progetto Coolify creato: "adaptiq-wms-prod"
- [x] Repository GitHub collegato come **Public Git Repository** (`antoniotaiani/adaptiq-wms`,
  branch `main`) — repo pubblico, quindi nessuna autenticazione richiesta. Nota: con questa
  modalità i deploy NON sono automatici al push, vanno rilanciati manualmente dal pannello
  ("Redeploy"); si può passare a "GitHub App" in futuro per i webhook automatici.
- [x] Build pack impostato su **Docker Compose** (non Railpack, che è per app singole)
- [x] Corretto il nome del file rilevato da Coolify: `docker-compose.yml` (non `.yaml`)
- [x] 4 variabili d'ambiente impostate in Coolify (vedi Fase F)
- [x] Attivata l'opzione **"Preserve repository during deployment"** — necessaria perché il
  servizio `nginx` non ha una build propria e ha bisogno che i file del repository (in
  particolare `nginx/nginx.conf`) restino disponibili sul server anche dopo la fase di build,
  per il bind-mount. Senza questa opzione il container nginx falliva all'avvio.
- [x] **Bug risolto**: `docker-compose.yml` aveva `nginx` con `ports: "80:80"`, in conflitto
  con il proxy interno di Coolify che occupa già la porta 80 dell'host. Cambiato in
  `expose: "80"` (il servizio resta raggiungibile solo sulla rete Docker interna; sarà Coolify
  a instradare il traffico esterno verso il container tramite il proprio proxy).
- [x] **Deploy riuscito**: tutti e 3 i container attivi — `db` (Healthy), `web` (Started),
  `nginx` (Started).
- [x] Dominio `adaptiqwms.com` (e `www.adaptiqwms.com`) assegnati al servizio `nginx`, porta 80
  interna, HTTPS attivo — certificato SSL Let's Encrypt generato automaticamente da Coolify
- [x] **Sito raggiungibile e funzionante**: `https://adaptiqwms.com` operativo, login terminale
  operatore e portale mandanti verificati, evasione ordine testata con successo

## FASE F — Sicurezza prima del go-live

- [x] **Codice modificato** (commit `baa3793`): `docker-compose.yml` parametrizzato con
  variabili d'ambiente (`${POSTGRES_USER}`, `${POSTGRES_PASSWORD}`, `${POSTGRES_DB}`,
  `${JWT_SECRET}`, con fallback ai vecchi valori solo per l'uso locale) — necessario perché il
  repository è pubblico e i valori precedenti erano scritti in chiaro nel codice
- [x] 4 variabili d'ambiente impostate in Coolify (Build time + Runtime attivi, Interpolation
  disattivata):

  | Variabile | Valore |
  |---|---|
  | `POSTGRES_USER` | `adaptiq_user` |
  | `POSTGRES_PASSWORD` | *(segreto — vedi password manager / pannello Coolify)* |
  | `POSTGRES_DB` | `adaptiq_wms` |
  | `JWT_SECRET` | *(segreto — vedi password manager / pannello Coolify)* |

  ⚠️ **Non scrivere mai i valori reali in questo file**: il repository è pubblico. I segreti
  vivono solo nel pannello Coolify e in un password manager. I valori usati al primo deploy
  sono transitati in chiaro in una chat: vanno **ruotati** (vedi "Prossimi passi aperti").

- [ ] Cambiare la password amministratore operatore (password di default definita nel codice
  in `app/routers/operator.py`, creata al primo avvio via `/api/operator/init`) — da fare subito dopo il primo login, da Fase 6 > Sicurezza
  del terminale operatore

### Invio email (SMTP) — problema risolto (2026-09-18)

Le email (creazione mandante, evasione ordine) non partivano nonostante SMTP configurato
correttamente in app (Fase 6 > Email & Notifiche, credenziali Aruba). Causa individuata dopo
diagnosi approfondita:

- **Sintomo**: log del container `web` mostrava `Errore SMTP critico: Timed out connecting to
  smtps.aruba.it on port 465/587`
- **Diagnosi**: testate le porte 25/465/587 in uscita anche verso Gmail (per escludere un
  problema specifico di Aruba) — timeout su tutte, verso qualunque destinazione. Esclusi
  firewall locali (`iptables`/`nftables`/`ufw`/`firewalld` — nessuna regola). `mtr` da e verso
  il server → percorso di rete perfettamente sano (0% perdita pacchetti), quindi non un
  problema di routing generale
- **Causa reale**: **Netcup applica di default una policy firewall "netcup Mail block"** a
  livello di server (SCP → Firewall, sezione "regole di default", separata dalle "Firewall
  Policies" personalizzate create dall'utente — per questo non era visibile subito) che droppa
  tutto il traffico OUTGOING sulle porte 25/465/587
- **Soluzione**: eliminata la policy di default "netcup Mail block" da SCP → Firewall (icona
  cestino). Non è bastato creare regole ACCEPT personalizzate sulle stesse porte: le regole
  vengono valutate dall'alto in basso e quella di default (DROP) veniva evidentemente valutata
  per prima
- **Verificato**: email inviate correttamente sia in fase di creazione mandante sia di
  finalizzazione ordine

⚠️ **Da ricordare per eventuali VPS futuri su Netcup**: questo blocco è attivo di default su
ogni nuovo server G12 e va rimosso esplicitamente da SCP → Firewall prima di poter usare SMTP
in uscita, indipendentemente dal provider email utilizzato.

## FASE G — Backup

**Verificato**: Netcup NON offre backup automatici programmati per i VPS (solo snapshot manuali
copy-on-write, in numero limitato, utili solo per rollback di configurazione). Il backup dei dati
applicativi è quindi interamente a nostro carico.

**Destinazione scelta: Cloudflare R2** (10 GB/mese gratis, download sempre gratuito).

- [ ] Creare un account Cloudflare e un bucket R2 dedicato ai backup
- [ ] Script di cron sul VPS: `pg_dump` del database + upload su R2 (via `rclone`/`aws-cli`) +
  rotazione dei backup vecchi — **da scrivere insieme**, non ancora fatto

## FASE H — Verifica finale ✅ COMPLETATA

- [x] `https://adaptiqwms.com` raggiungibile e funzionante
- [x] Login operatore verificato
- [x] Portale mandanti verificato
- [x] Certificato SSL attivo (Let's Encrypt via Coolify)
- [x] Invio email funzionante (creazione mandante + evasione ordine) — vedi Fase F per i dettagli
  del problema risolto
- [ ] Monitorare l'uso RAM/CPU dal pannello Coolify nei primi giorni: se stabilmente sopra
  l'80-90% di RAM, valutare l'upgrade a VPS 1000 G12 (vedi Decisioni prese)

## Prossimi passi aperti

1. Cambiare la password admin operatore (Fase F)
1b. Ruotare `POSTGRES_PASSWORD` (richiede anche `ALTER USER` su Postgres) e `JWT_SECRET` in
   Coolify, poi Redeploy (Fase F)
2. Script di backup automatico su Cloudflare R2 (Fase G)
3. (Facoltativo) Creare utente non-root sul VPS per l'uso quotidiano (Fase B)
4. (Facoltativo) Passare da "Public Git Repository" a "GitHub App" per i deploy automatici al push
5. (Facoltativo) Backup del file `/data/coolify/source/.env` in un password manager (Fase C)
6. Monitoraggio RAM/CPU nei primi giorni d'uso (Fase H)

## Note verificate

- **Firewall**: confermato, Netcup offre un firewall gestito dal pannello (SCP).
- **Backup Netcup**: nessun backup automatico incluso per i VPS (solo per il Web Hosting).
- **Push a GitHub**: le credenziali git non sono disponibili nella sessione Claude Code usata per
  la configurazione — i push a `origin main` vanno fatti dall'utente da un terminale autenticato
  (`git push origin main`, o `! git push origin main` dentro la chat).
