"""Configuração central, lida de variáveis de ambiente (.env)."""
from __future__ import annotations

import datetime as dt
import os
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")

DATA_DIR = Path(os.getenv("EMPORIO_DATA_DIR", ROOT / "data"))
SESSIONS_DIR = ROOT / "sessions"
EXAMPLES_DIR = ROOT / "examples"
CACHE_DIR = ROOT / ".cache"

# Marcador que indica que data/ contém dados fictícios de desenvolvimento.
USANDO_DADOS_EXEMPLO = (DATA_DIR / ".dados_de_exemplo").exists()

MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
EMBEDDING_MODEL = os.getenv("GEMINI_EMBEDDING_MODEL", "gemini-embedding-001")

TZ = ZoneInfo("America/Campo_Grande")

_DIAS = ["segunda-feira", "terça-feira", "quarta-feira", "quinta-feira",
         "sexta-feira", "sábado", "domingo"]


def agora() -> dt.datetime:
    """Data/hora atual em Campo Grande.

    EMPORIO_HOJE=AAAA-MM-DD fixa o dia (às 10h), útil para reproduzir
    conversas de exemplo com dados que dependem da data (promoções, prazos).
    """
    fixo = os.getenv("EMPORIO_HOJE")
    if fixo:
        dia = dt.date.fromisoformat(fixo)
        return dt.datetime.combine(dia, dt.time(10, 0), tzinfo=TZ)
    return dt.datetime.now(TZ)


def agora_legivel(momento: dt.datetime | None = None) -> str:
    m = momento or agora()
    return f"{_DIAS[m.weekday()]}, {m:%d/%m/%Y}, {m:%H:%M} (horário de Campo Grande/MS)"
