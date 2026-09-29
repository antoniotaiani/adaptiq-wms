from datetime import datetime
from zoneinfo import ZoneInfo

# Il container gira in UTC: tutti i timestamp applicativi vanno generati nel fuso
# del magazzino, altrimenti orari e filtri per data/mese risultano sfasati di 1-2 ore.
APP_TZ = ZoneInfo("Europe/Rome")


def now_local() -> datetime:
    return datetime.now(APP_TZ)


def now_str() -> str:
    """Timestamp nel formato usato in tutte le colonne di data (ordinabile come stringa)."""
    return now_local().strftime("%Y-%m-%d %H:%M:%S")
