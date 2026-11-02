"""Extração e limpeza do texto do manual de políticas (PDF)."""
from __future__ import annotations

import re
from pathlib import Path

from pypdf import PdfReader

_CABECALHO = re.compile(r"^\s*Empório da Música\s+Manual de Políticas e Procedimentos\s*$")
_PAGINA = re.compile(r"^\s*Página\s+\d+\s*$")


def extrair_texto(pdf: Path) -> str:
    """Texto do PDF, sem cabeçalho/rodapé de página e com tabelas legíveis.

    O modo 'layout' do pypdf preserva as linhas e colunas (o modo padrão devolve
    uma palavra por linha neste PDF). Colunas de tabela, separadas por vários
    espaços, viram ' | '.
    """
    leitor = PdfReader(str(pdf))
    linhas: list[str] = []
    for pagina in leitor.pages:
        bruto = pagina.extract_text(extraction_mode="layout") or ""
        for linha in bruto.replace("​", "").splitlines():
            if _CABECALHO.match(linha) or _PAGINA.match(linha):
                continue
            linha = re.sub(r"^\s*•\s+", "- ", linha)
            linha = re.sub(r"\s{3,}", " | ", linha.strip()) if re.search(r"\S\s{3,}\S", linha.strip()) else linha.strip()
            linhas.append(linha)
    texto = "\n".join(linhas)
    return re.sub(r"\n{3,}", "\n\n", texto).strip()


def localizar_pdf(data_dir: Path) -> Path:
    achados = sorted(data_dir.glob("pol*ticas*.pdf"))
    if not achados:
        raise FileNotFoundError(f"PDF de políticas não encontrado em {data_dir}")
    return achados[0]


def carregar_politicas(data_dir: Path) -> str:
    return extrair_texto(localizar_pdf(data_dir))
