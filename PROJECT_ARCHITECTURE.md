# AdaptiQ WMS - Architettura di Sistema, Mappatura Flussi & Specifiche Tecniche

**Versione:** 2.2.0
**Aggiornato al:** Settembre 2026
**Stack Principale:** Python 3.11, FastAPI, SQLAlchemy 2.0 (Asyncio / asyncpg), PostgreSQL 16, Docker Compose, Nginx, Jinja2, Bootstrap 5.3.3, Html5-QRCode, ReportLab, aiosmtplib.

---

## 1. Albero del Progetto (Project Tree)

```text
adaptiq-wms/
├── docker-compose.yml               # Servizi: db (Postgres), web (FastAPI/Gunicorn), nginx (reverse proxy)
├── docker-compose.override.yml      # Solo locale: espone debugpy (5678) e avvia con --reload per il debug in VS Code
├── Dockerfile                       # Build immagine applicativa Python 3.11-slim
├── requirements.txt                 # Dipendenze installate nell'immagine Docker
├── requirements-dev.txt             # Dipendenze del venv locale (solo per Pylance/autocompletamento IDE)
├── PROJECT_ARCHITECTURE.md          # [Questo documento] Mappa architetturale e flussi
├── manual.html                      # Manuale utente standalone
├── Nuove modifiche da implementare.md
│
├── app/                             # Core modulare del Backend
│   ├── __init__.py
│   ├── main.py                      # Istanza FastAPI, lifespan (init schema DB + seed dati demo), mount static, registrazione router
│   ├── database.py                  # Engine asyncpg, sessionmaker, dependency get_db()
│   ├── config.py                    # Variabili d'ambiente: DATABASE_URL, JWT_SECRET/ALGORITHM/EXPIRATION, path static/templates
│   ├── models.py                    # Modelli SQLAlchemy: Merchant, Item, InventoryTransaction, DispatchOrder,
│   │                                 #   DispatchOrderLine, SmtpSettings, OperatorUser, AuditLog
│   ├── schemas.py                   # Modelli Pydantic (Request/Response DTO per le API)
│   ├── auth.py                      # Hash/verify PIN (PBKDF2-HMAC-SHA256), firma/verifica JWT, dependency per
│   │                                 #   sessione operatore, mandante, o entrambe (token via cookie HttpOnly)
│   ├── audit.py                     # Helper per scrivere voci di log in AuditLog (audit trail)
│   ├── email_utils.py               # Config SMTP da DB, invio email asincrono (aiosmtplib) con allegato PDF
│   └── routers/                     # Router modulari registrati in FastAPI
│       ├── __init__.py
│       ├── views.py                 # Rotte HTML: "/" (operator.html) e "/portal" (portal.html)
│       ├── operator.py              # Init/login/logout operatore, cambio e reset password amministratore
│       ├── merchants.py             # CRUD anagrafica mandanti, login/logout portale mandante, reset/self-service PIN,
│       │                            #   giacenze e ordini "live" del mandante autenticato
│       ├── items.py                 # Risoluzione articolo per barcode/SKU (scanner), multi-tenant aware
│       ├── inventory.py             # Giacenze aggregate, rettifiche manuali, storno referenza da un mandante
│       ├── inbound.py                # Ricezione merci: registrazione DDT fornitore, generazione SKU automatica, storico carichi
│       ├── outbound.py               # Validazione preventiva DDT, evasione ordine (fulfill), generazione PDF DDT,
│       │                            #   invio email al mandante, storico spedizioni, dettaglio ordine
│       └── config.py                 # Configurazione SMTP, upload loghi personalizzati, consultazione audit log
│
├── nginx/
│   └── nginx.conf                   # Reverse proxy porta 80, rate limiting su /api/merchant/login e globale, security headers
│
├── static/                          # Risorse statiche servite dall'applicazione (loghi personalizzabili da UI)
│   ├── logo.png                     # Logo primario AdaptiQ
│   └── logo_logistics.png           # Logo secondario/fallback logistica
│
├── templates/                       # Pagine HTML renderizzate via Jinja2
│   ├── operator.html                # Terminale Postazione Operativa Magazzino (Fasi 1-6, Scanner Barcode)
│   └── portal.html                  # Portale Mandanti dedicato (consultazione ordini e giacenze riservate)
│
└── .vscode/                         # Configurazione debug (launch.json collegato a docker-compose.override.yml)
```

---

## 2. Modello Dati (`app/models.py`)

| Tabella | Scopo | Campi/vincoli chiave |
|---|---|---|
| `merchants` | Anagrafica committenti | `account_code` (univoco), `pin_hash`, `email`/`phone` opzionali |
| `items` | Catalogo/giacenze per mandante | `sku` PK, `barcode`, `on_hand_qty`; vincolo univoco `(merchant_id, barcode)` — lo stesso barcode può coesistere su mandanti diversi |
| `inventory_transactions` | Storico movimenti (carico, scarico, rettifica) | `sku` FK, `transaction_type`, `quantity` (può essere negativo) |
| `dispatch_orders` | Testata ordine/spedizione evaso | `order_number`, `merchant_id`, `status`, `total_units` |
| `dispatch_order_lines` | Righe di dettaglio della spedizione | `expected_qty` vs `picked_qty` |
| `smtp_settings` | Configurazione server SMTP (riga singola, id=1) | host/porta/credenziali, `use_tls`, `portal_base_url` |
| `operator_users` | Utenze operatore del terminale (es. `admin`) | `username` univoco, `password_hash` |
| `audit_log` | Traccia delle azioni sensibili (Fase 6) | `actor`, `action`, `target`, `details` |

---

## 3. Autenticazione e Ruoli (`app/auth.py`)

Due ambiti di sessione, entrambi con **JWT firmato con la stessa chiave** (`JWT_SECRET`) ma veicolato su **cookie HttpOnly separati**:

- **Operatore** — cookie `operator_token`. Il claim `code` del JWT ha sempre prefisso `OP_<username>`, usato per distinguerlo da un token mandante. Login via `POST /api/operator/login`.
- **Mandante** — cookie `adaptiq_token`. Login via `POST /api/merchant/login` (account_code + PIN).
- `get_operator_or_merchant_payload` accetta l'uno o l'altro (usato per endpoint condivisi come il dettaglio ordine), restituendo `{"role": ..., "payload": ...}`; l'autorizzazione fine (es. il mandante vede solo i propri ordini) va poi verificata dall'endpoint.
- Hash PIN/password: PBKDF2-HMAC-SHA256, salt casuale per record, confronto costante (`hash_pin`/`verify_pin` in `auth.py`, riutilizzati sia per PIN mandante che per password operatore).

---

## 4. Mappa Endpoint API

| Router | Prefix | Endpoint principali |
|---|---|---|
| `views.py` | `/` | `GET /` (operator.html), `GET /portal` (portal.html) |
| `operator.py` | `/api/operator` | `POST /init`, `POST /login`, `POST /logout`, `POST /admin/change-password`, `POST /admin/reset-password` |
| `merchants.py` | `/api` | `GET|POST /merchants`, `GET|PUT|DELETE /merchants/{id}`, `POST /merchants/{id}/reset-pin`, `PUT /merchants/{id}/pin`, `POST /merchant/login`, `POST /merchant/logout`, `GET /merchant/me/inventory`, `GET /merchant/me/orders` |
| `items.py` | `/api/items` | `POST /resolve` (lookup barcode/SKU per scanner, multi-tenant aware) |
| `inventory.py` | `/api/inventory` | `GET ""` (giacenze), `POST /adjust` (rettifica), `DELETE /merchant-item/{sku}` (storno referenza) |
| `inbound.py` | `/api/inbound` | `POST /ddt` (registrazione carico + generazione SKU automatica), `GET /history` |
| `outbound.py` | `/api/orders` | `POST /validate-ddt`, `GET /check/{order_number}`, `POST /fulfill` (evasione + PDF + email), `GET /history`, `GET /{order_id}` |
| `config.py` | `/api/config` | `GET|POST /smtp`, `POST /logo/{target}` (upload loghi), `GET /audit-log` |

Tutti gli endpoint operativi (tranne login/init) richiedono la sessione operatore via dependency; gli endpoint `/merchant/me/*` e `/merchants/{id}/pin` richiedono la sessione mandante.

---

## 5. Flussi Principali

### A. Ricezione Merce (Inbound, `inbound.py`)
DDT fornitore → per ogni riga: se il barcode/SKU esiste già per quel mandante aggiorna la giacenza (`on_hand_qty += quantity`), altrimenti genera un nuovo SKU progressivo nel formato `SKU-<CODICE_MANDANTE>-<ANNO>-NNNN`. Blocca barcode duplicati appartenenti ad altri mandanti (a meno di `allow_shared_barcode=true`), impedisce righe duplicate nello stesso DDT, registra una `InventoryTransaction` di tipo `INBOUND_RECEIVE` per riga.

### B. Evasione Spedizione (Outbound, `outbound.py`)
1. `POST /validate-ddt` — controllo preventivo (giacenza sufficiente, articolo censito, ordine non già evaso) senza scrivere nulla.
2. `POST /fulfill` — crea `DispatchOrder` + `DispatchOrderLine`, decrementa `on_hand_qty` con lock (`with_for_update`) per prevenire overpicking concorrente, verifica `picked_qty <= expected_qty` e `picked_qty <= on_hand_qty`, registra `InventoryTransaction` di tipo `OUTBOUND_PICK`.
3. Genera in memoria il PDF ufficiale del DDT (ReportLab: intestazione, tabella articoli, sezione firme).
4. Se il mandante ha un'email e l'SMTP è configurato, accoda in background (`BackgroundTasks`) l'invio email con il PDF allegato; altrimenti l'ordine viene comunque evaso e la UI riceve un messaggio esplicativo (`email_info`).

### C. Rettifiche e Gestione Inventario (`inventory.py`)
Rettifica manuale della giacenza con traccia del delta in `InventoryTransaction` (tipo `ADJUSTMENT (motivo)`); storno di una referenza da un mandante, bloccato se esistono spedizioni storicizzate che la referenziano.

### D. Anagrafica Mandanti e Accesso Portale (`merchants.py`)
CRUD mandanti (creazione/modifica/cancellazione con controlli di dipendenza su SKU e ordini esistenti), generazione PIN iniziale con invio email di benvenuto, reset PIN da operatore o self-service dal mandante autenticato (verifica del PIN attuale), consultazione "live" di giacenze e ordini filtrata per il proprio `merchant_id`.

### E. Configurazione, Personalizzazione e Audit (`config.py`, `audit.py`)
Parametri SMTP centralizzati (riga singola in DB), upload dei loghi (`logo.png` / `logo_logistics.png`, max 3MB, PNG/JPEG/WEBP), consultazione dell'audit log con filtro per intervallo di date. Le azioni sensibili (modifica anagrafica, cancellazioni, cambio config SMTP, upload logo, reset/cambio password admin) vengono tracciate in `AuditLog` tramite `log_action()`, sulla stessa transazione dell'operazione principale.

### F. Autenticazione Operatore (`operator.py`)
`POST /init` crea l'utente `admin` di bootstrap (idempotente) con password di default; login/logout via cookie `operator_token`; cambio password con verifica della vecchia, e reset diretto (senza vecchia password) per recovery amministrativa — entrambi tracciati in audit log.

---

## 6. Infrastruttura (Docker / Nginx)

- **`db`**: `postgres:16-alpine`, healthcheck `pg_isready`, volume persistente `postgres_data`.
- **`web`**: build da `Dockerfile` (Python 3.11-slim), monta a caldo `app/`, `templates/`, `static/`; all'avvio installa alcune dipendenze extra runtime (`aiosmtplib`, `reportlab`, `email-validator`, `pydantic[email]`, `python-multipart`) e lancia `gunicorn` con worker `uvicorn.workers.UvicornWorker` (`-w 4`). Attende che `db` sia healthy.
- **`nginx`**: reverse proxy sulla porta 80, security header (`X-Frame-Options`, `X-Content-Type-Options`, ecc.), rate limiting globale (30r/s) e dedicato più stringente su `/api/merchant/login` (5r/m, burst 3) per mitigare il brute-force sul PIN.
- **`docker-compose.override.yml`** (solo ambiente locale, non committato in produzione): sostituisce l'avvio con `debugpy` in `--wait-for-client` e `uvicorn --reload`, espone la porta 5678 per il debugger di VS Code (vedi `.vscode/launch.json`).
- **Ciclo di vita app (`lifespan` in `main.py`)**: all'avvio acquisisce un `pg_advisory_lock` per serializzare la creazione schema tra repliche multiple, esegue `Base.metadata.create_all`, e — se il DB è vuoto — inserisce due mandanti e quattro articoli demo.

---

## 7. Comandi Rapidi

```bash
# Avvio stack completo (produzione-like)
docker compose up --build -d

# Avvio con debugger VS Code attivo (usa automaticamente l'override)
docker compose up --build

# Riavvio del solo backend dopo modifiche
docker compose restart web

# Log applicativi
docker compose logs -f web
```
