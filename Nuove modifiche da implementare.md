# ADAPTIQ WMS - MEMORIA TECNICA E RIEPILOGO ARCHITETTURALE
# Data di aggiornamento: 2026-09-14

## 1. Architettura dell'Ambiente & Infrastruttura
* **Ambiente di esecuzione**: Docker e Docker Compose su thin client Dell Wyse 5070.
* **Stack Backend**: Python (FastAPI), Gunicorn + Uvicorn workers, SQLAlchemy (Asyncio), PostgreSQL.
* **Stack Frontend**: HTML5, Bootstrap 5.3.3, Bootstrap Icons, HTML5-QRCode scanner, Vanilla JavaScript SPA.
* **Proxy / Networking**: Nginx come reverse proxy esposto sulla porta 80, inoltra le chiamate al backend containerizzato.

## 2. Componenti Modificati e Stato Corrente

### A. Frontend Operativo (`operator.html`)
* **Autenticazione Operatore**: Overlay di sicurezza basato su token di sessione cookie-based con salvataggio dello stato in `localStorage` (`op_logged_in`).
* **Fase 1 (Spedizione & Picking)**:
  * Gestione dei documenti di trasporto (DDT) e controllo preventivo con endpoint `/validate-ddt`.
  * Generazione automatica di file PDF di stampa con nome file dinamico e univoco basato sul numero d'ordine (es. `DDT_ORDINE_DATA`).
  * Messaggio di feedback visivo (alert verde dinamico in `emailAlertContainer`) che conferma l'avvenuta evasione e l'accodamento dell'email al mandante.
* **Fase 5 (Anagrafica Mandanti)**:
  * Gestione completa dei mandanti con generazione e reset dei PIN d'accesso al portale dedicato.
* **Fase 6 (Configurazioni & Sicurezza)**:
  * Configurazione centralizzata dei parametri SMTP e crittografia TLS.
  * Sezione dedicata al **Reset Diretto della Password Amministratore** senza obbligo di inserimento della vecchia password.

### B. Backend - Rotte Operatore (`app/routers/operator.py`)
* Gestione degli endpoint di inizializzazione (`/init`), login (`/login`), e logout (`/logout`).
* **Nuovo Endpoint di Reset Password**:
  * Rotta POST `/api/operator/admin/reset-password` che riceve `new_password` e `confirm_password`, validando la lunghezza minima e aggiornando direttamente l'hash nel database per l'utente `admin`.

### C. Backend - Rotte Spedizioni / Outbound (`app/routers/outbound.py`)
* **Flusso di Evasione (`/fulfill`)**:
  * Transazione asincrona gestita direttamente dal ciclo di vita della sessione (rimosso il conflitto `async with db.begin()` per prevenire l'eccezione `InvalidRequestError`).
  * Generazione in memoria del PDF ufficiale del DDT tramite `ReportLab` (griglia articoli, intestazione, totali e sezioni firme).
  * Innesco del task asincrono in background (`background_tasks.add_task`) per l'invio della mail al mandante con il PDF allegato.

### D. Utility Email (`app/email_utils.py`)
* Funzione `get_smtp_config` ottimizzata per prelevare la prima configurazione SMTP disponibile in tabella (`limit(1)`), prevenendo errori di ID mancanti.
* Invio asincrono tramite `aiosmtplib` con supporto nativo agli allegati binari (PDF DDT) e gestione dei log di errore critici SMTP.

## 3. Comandi Rapidi per il riavvio e test
* **Riavvio del servizio backend**:
  ```bash
  sudo docker compose restart web
