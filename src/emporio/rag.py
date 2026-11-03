"""RAG sobre o manual de políticas: chunking por seção + busca semântica.

Pipeline: texto do PDF -> chunks por seção/subseção -> embeddings (Gemini, com cache em
disco) -> busca por similaridade de cosseno em memória (numpy). Sem banco vetorial:
são ~30 chunks, então uma matriz numpy é mais simples e igualmente exata.

Se a API de embeddings falhar (cota, rede), a busca cai para um ranking lexical
(BM25 simplificado) em vez de derrubar o atendimento.
"""
from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np

from .data_loader import norm

# ------------------------------------------------------------------ chunking

_H1 = re.compile(r"^(\d{1,2})\.\s+(\S.{0,70})$")
_H2 = re.compile(r"^(\d{1,2})\.(\d{1,2})\s+(\S.{0,70})$")


@dataclass
class Trecho:
    id: str          # "4.2"
    titulo: str      # "Trocas por Defeito"
    pai: str         # "Política de Trocas e Devoluções" ("" no nível 1)
    corpo: str

    @property
    def rotulo(self) -> str:
        return f"{self.id} {self.titulo}" if self.id != "0" else self.titulo

    @property
    def texto_indexado(self) -> str:
        """O que vira embedding: o título (com a seção-mãe) junto do corpo."""
        cabeca = f"{self.pai} > {self.titulo}" if self.pai else self.titulo
        return f"{cabeca}\n{self.corpo}"


def dividir_por_secao(texto: str) -> list[Trecho]:
    """Quebra o manual nas seções numeradas (1., 1.1, 4.2...).

    Cada subseção vira um chunk; uma seção que tem texto próprio antes da primeira
    subseção (ex.: a tabela de pagamentos na seção 3) também. Chunks por estrutura
    mantêm cada regra inteira, com suas condições, no mesmo trecho.
    """
    trechos: list[Trecho] = []
    atual = Trecho("0", "Capa e contato da loja", "", "")
    corpo: list[str] = []
    ultimo_h1, titulo_h1 = 0, ""

    def fechar():
        atual.corpo = "\n".join(corpo).strip()
        if atual.corpo:
            trechos.append(atual)

    for linha in texto.splitlines():
        s = linha.strip()
        m2, m1 = _H2.match(s), _H1.match(s)
        if m1 and int(m1.group(1)) == ultimo_h1 + 1 and ":" not in s and not s.endswith("."):
            fechar()
            ultimo_h1, titulo_h1 = int(m1.group(1)), m1.group(2).strip()
            atual, corpo = Trecho(str(ultimo_h1), titulo_h1, "", ""), []
        elif m2 and int(m2.group(1)) == ultimo_h1 and ":" not in s and not s.endswith("."):
            fechar()
            atual = Trecho(f"{m2.group(1)}.{m2.group(2)}", m2.group(3).strip(), titulo_h1, "")
            corpo = []
        else:
            corpo.append(linha)
    fechar()
    return trechos


# -------------------------------------------------------------- embeddings

Embedder = Callable[[list[str], str], np.ndarray]


def gemini_embedder(client, modelo: str) -> Embedder:
    from google.genai import types

    def embed(textos: list[str], tarefa: str) -> np.ndarray:
        vetores: list = []
        for i in range(0, len(textos), 50):
            r = client.models.embed_content(
                model=modelo, contents=textos[i:i + 50],
                config=types.EmbedContentConfig(task_type=tarefa))
            vetores.extend(e.values for e in r.embeddings)
        return np.asarray(vetores, dtype=np.float32)

    return embed


def _normalizar(m: np.ndarray) -> np.ndarray:
    return m / np.clip(np.linalg.norm(m, axis=-1, keepdims=True), 1e-9, None)


# ---------------------------------------------------------------- lexical

_STOP = set("""a o as os e em um uma uns umas de da do das dos para por com sem que se na no nas nos ao aos
como posso quero qual quais pode ser tem ter eu minha meu sua seu sobre quanto quando onde mais ou
ja foi vai sao esta estou preciso gostaria saber""".split())


def _stems(texto: str) -> list[str]:
    """Radical grosseiro (5 primeiras letras): 'devolver' ~ 'devolucao', 'parcelar' ~ 'parcelamento'."""
    return [t[:5] for t in re.findall(r"[a-z0-9]+", norm(texto)) if len(t) > 2 and t not in _STOP]


class _Lexical:
    def __init__(self, textos: list[str]):
        self.contagens = [Counter(_stems(t)) for t in textos]
        n = len(textos)
        df = Counter(s for c in self.contagens for s in c)
        self.idf = {s: math.log(1 + n / d) for s, d in df.items()}

    def pontuar(self, consulta: str) -> np.ndarray:
        q = set(_stems(consulta))
        return np.array([sum(self.idf.get(s, 0) * (1 + math.log(c[s])) for s in q if s in c)
                         for c in self.contagens])


# --------------------------------------------------------------- retriever


class Retriever:
    def __init__(self, trechos: list[Trecho], embedder: Embedder | None = None,
                 cache_dir: Path | None = None, modelo: str = "",
                 log: Callable[[str], None] | None = None):
        self.trechos = trechos
        self.embedder = embedder
        self.cache_dir = cache_dir
        self.modelo = modelo
        self.log = log or (lambda _m: None)
        self.backend = "embeddings" if embedder else "lexical"
        self._docs: np.ndarray | None = None
        self._lexical = _Lexical([t.texto_indexado for t in trechos])

    def _vetores_docs(self) -> np.ndarray:
        if self._docs is not None:
            return self._docs
        textos = [t.texto_indexado for t in self.trechos]
        chave = hashlib.sha1((self.modelo + "\x00" + "\x00".join(textos)).encode()).hexdigest()[:16]
        arquivo = self.cache_dir / f"politicas-{chave}.npy" if self.cache_dir else None
        if arquivo and arquivo.exists():
            self._docs = np.load(arquivo)
        else:
            self._docs = _normalizar(self.embedder(textos, "RETRIEVAL_DOCUMENT"))
            if arquivo:
                arquivo.parent.mkdir(parents=True, exist_ok=True)
                np.save(arquivo, self._docs)
        return self._docs

    def _pontuar(self, consulta: str) -> np.ndarray:
        if self.embedder is not None and self.backend == "embeddings":
            try:
                q = _normalizar(self.embedder([consulta], "RETRIEVAL_QUERY"))[0]
                return self._vetores_docs() @ q
            except Exception as e:  # noqa: BLE001 - degradar, não falhar
                self.backend = "lexical (fallback)"
                self.log(f"embeddings indisponíveis ({type(e).__name__}); usando busca lexical")
        return self._lexical.pontuar(consulta)

    def buscar(self, consulta: str, k: int = 4) -> list[dict]:
        pontos = self._pontuar(consulta)
        ordem = np.argsort(-pontos)[:k]
        return [{"secao": self.trechos[i].rotulo, "relevancia": round(float(pontos[i]), 3),
                 "texto": self.trechos[i].corpo}
                for i in ordem if pontos[i] > 0]
