cat << 'EOF' > PROJECT_ARCHITECTURE.md
# AdaptiQ WMS - Architettura di Sistema, Mappatura Flussi & Specifiche Tecniche

**Versione:** 2.1.0-hardened  
**Aggiornato al:** Settembre 2026  
**Stack Principale:** Python 3.11, FastAPI, SQLAlchemy 2.0 (Asyncio / asyncpg), PostgreSQL 15+, Docker Compose, Jinja2, Html5-QRCode.

---

## 1. Albero del Progetto (Project Tree)

```text
adaptiq-wms/
├── docker-compose.yml              # Definizione dei servizi: db (PostgreSQL) e web/api (FastAPI)
├── Dockerfile                      # Build dell'immagine applicativa basata su Python 3.11
├── requirements.txt                # Dipendenze Python (FastAPI, uvicorn, gunicorn, sqlalchemy, asyncpg, jose, ecc.)
├── CHANGELOG_OVERPICKING_FIX.md    # Storico delle correzioni vincoli DB e overpicking
├── PROJECT_ARCHITECTURE.md         # [Questo documento] Mappa architetturale e flussi
│
├── app/                            # Core modulare del Backend
│   ├── __init__.py
│   ├── database.py                 # Connessione DB, engine asyncpg e sessionmaker
│   ├── models.py                   # Modelli SQLAlchemy (Merchant, Item, DispatchOrder, DispatchOrderLine, InventoryTransaction)
│   ├── schemas.py                  # Modelli Pydantic (Request/Response DTO per le API)
│   ├── security.py                 # Gestione hash PIN (PBKDF2-HMAC-SHA256) e firma token JWT
│   └── routers/                    # Router modulari registrati in FastAPI
│       ├── __init__.py
│       ├── auth.py                 # Autenticazione Portale Mandanti (login/logout via HttpOnly cookie)
│       ├── inbound.py              # Ricezione merci e registrazione carichi da DDT fornitore
│       ├── inventory.py            # Catalogo, giacenze e rettifiche inventariali
│       ├── merchants.py            # Gestione anagrafica e creazione committenti
│       └── outbound.py             # Flusso Spedizioni: validazione preventiva DDT e prelievo/fulfill
│
├── static/                         # Risorse statiche servite dall'applicazione
│   ├── logo.png                    # Logo primario AdaptiQ
│   └── logo_logistics.jpeg         # Logo secondario/fallback logistica
│
└── templates/                      # Pagine HTML renderizzate via Jinja2
    ├── operator.html               # Terminale Postazione Operativa Magazzino (Fasi 1-5, Scanner Barcode)
    └── portal.html                 # Portale Mandanti dedicato (consultazione ordini e giacenze riservate)
