"""Persistência da conversa: um JSON por sessão em sessions/.

Guardamos só o texto de cada turno (cliente/assistente) e, à parte, as tools
chamadas. Ao retomar uma sessão, o modelo recebe os turnos em texto; os
resultados brutos das tools não são reaproveitados (o texto da resposta já
carrega os fatos relevantes e dados voláteis, como estoque, devem ser reconsultados).
"""
from __future__ import annotations

import datetime as dt
import json
import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Turno:
    cliente: str
    assistente: str
    tools: list[dict] = field(default_factory=list)  # [{"nome", "args", "resultado"}]
    quando: str = ""


@dataclass
class Sessao:
    id: str
    turnos: list[Turno] = field(default_factory=list)

    @staticmethod
    def nova(agora: dt.datetime) -> "Sessao":
        return Sessao(id=f"{agora:%Y%m%d-%H%M%S}")

    @staticmethod
    def _caminho(pasta: Path, sessao_id: str) -> Path:
        seguro = re.sub(r"[^A-Za-z0-9_.-]", "_", sessao_id)
        return pasta / f"{seguro}.json"

    def salvar(self, pasta: Path) -> Path:
        pasta.mkdir(parents=True, exist_ok=True)
        caminho = self._caminho(pasta, self.id)
        dados = {"id": self.id, "turnos": [t.__dict__ for t in self.turnos]}
        caminho.write_text(json.dumps(dados, ensure_ascii=False, indent=2), encoding="utf-8")
        caminho.with_suffix(".txt").write_text(self.para_texto(), encoding="utf-8")  # cópia legível
        return caminho

    @staticmethod
    def carregar(pasta: Path, sessao_id: str) -> "Sessao | None":
        caminho = Sessao._caminho(pasta, sessao_id)
        if not caminho.exists():
            return None
        dados = json.loads(caminho.read_text(encoding="utf-8"))
        return Sessao(id=dados["id"], turnos=[Turno(**t) for t in dados["turnos"]])

    def para_markdown(self) -> str:
        linhas = [f"# Conversa `{self.id}`", ""]
        for t in self.turnos:
            linhas.append(f"**Cliente:** {t.cliente}")
            linhas.append("")
            for c in t.tools:
                args = json.dumps(c["args"], ensure_ascii=False)
                linhas.append(f"> 🔧 `{c['nome']}({args})`")
            if t.tools:
                linhas.append("")
            linhas.append(f"**Téo:** {t.assistente}")
            linhas.append("")
        return "\n".join(linhas).rstrip() + "\n"

    def para_texto(self) -> str:
        linhas = [f"Conversa {self.id}", "=" * 40, ""]
        for t in self.turnos:
            if t.quando:
                linhas.append(f"[{t.quando}]")
            linhas.append(f"Cliente: {t.cliente}")
            for c in t.tools:
                linhas.append(f"  (ferramenta: {c['nome']}({json.dumps(c['args'], ensure_ascii=False)}))")
            linhas.append(f"Téo: {t.assistente}")
            linhas.append("")
        return "\n".join(linhas).rstrip() + "\n"
