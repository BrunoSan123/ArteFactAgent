"""Consultas sobre os dados da loja. Aqui vive toda a regra de negócio dos dados;
as tools (tools.py) apenas expõem essas consultas ao modelo."""
from __future__ import annotations

import datetime as dt
import re
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Callable

import pandas as pd

from .data_loader import carregar_tudo, norm, singular, tokens_busca


def reais(valor) -> float:
    """Arredonda para centavos com arredondamento comercial (meio centavo sobe).

    round() do Python trabalha sobre float e erra meio centavo (599,90 x 0,85 = 509,915 -> 509,91).
    """
    return float(Decimal(str(valor)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def preco_com_desconto(preco, desconto_pct) -> float:
    return reais(Decimal(str(preco)) * (Decimal(100) - Decimal(str(desconto_pct))) / Decimal(100))


_STATUS_PEDIDO = {"delivered": "entregue", "shipped": "enviado", "confirmed": "confirmado",
                  "pending": "pendente", "cancelled": "cancelado", "canceled": "cancelado"}


def _status_pt(status: str) -> str:
    return _STATUS_PEDIDO.get(norm(status), status)


def _pagamento_pt(forma: str) -> str | None:
    f = norm(forma)
    if not f:
        return None
    m = re.fullmatch(r"credit(?:o)?_?(\d+)x?", f.replace(" ", "_"))
    if m:
        return f"cartão de crédito em {m.group(1)}x"
    return {"pix": "PIX", "boleto": "boleto", "debit": "cartão de débito", "debito": "cartão de débito",
            "credit": "cartão de crédito"}.get(f, forma)


def _data_br(ts) -> str | None:
    return None if pd.isna(ts) else ts.strftime("%d/%m/%Y")


class Loja:
    def __init__(self, data_dir: Path, hoje_fn: Callable[[], dt.date]):
        tabelas = carregar_tudo(data_dir)
        self.produtos = tabelas["produtos"]
        self.clientes = tabelas["clientes"]
        self.pedidos = tabelas["pedidos"]
        self.promocoes = tabelas["promocoes"]
        self._hoje = hoje_fn

    # ----------------------------------------------------------- promoções
    def _promocoes_ativas(self) -> pd.DataFrame:
        """Vigentes hoje. Vale a flag 'ativa' (quando existe) E a janela inicio/fim (quando existe)."""
        hoje = pd.Timestamp(self._hoje())
        p = self.promocoes
        flag = p["ativa"].map(lambda v: True if v is None or pd.isna(v) else bool(v))
        ok = flag & (p["inicio"].isna() | (p["inicio"] <= hoje)) & (p["fim"].isna() | (p["fim"] >= hoje))
        return p[ok.astype(bool)]

    def _melhor_promocao(self, produto: pd.Series) -> dict | None:
        ativas = self._promocoes_ativas()
        if ativas.empty:
            return None
        cat = singular(norm(produto["categoria"]))
        por_produto = ativas["produto_id"].str.upper() == str(produto["id"]).upper()
        por_categoria = (ativas["produto_id"] == "") & (ativas["_categoria_norm"] != "") & (ativas["_categoria_norm"] == cat)
        casa = ativas[por_produto | por_categoria]
        if casa.empty:
            return None
        melhor = casa.sort_values("desconto_pct", ascending=False).iloc[0]
        return {
            "nome": melhor["nome"] or None,
            "desconto_pct": round(float(melhor["desconto_pct"]), 2),
            "valido_ate": _data_br(melhor["fim"]),  # None quando a base não informa
        }

    def formatar_produto(self, p: pd.Series) -> dict:
        promo = self._melhor_promocao(p)
        preco = float(p["preco"])
        atual = preco_com_desconto(preco, promo["desconto_pct"]) if promo else reais(preco)
        disponivel = int(p["estoque"]) > 0 and not p["descontinuado"] and not p["em_breve"]
        return {
            "id": p["id"],
            "nome": p["nome"],
            "marca": p["marca"] or None,
            "categoria": p["categoria"] or None,
            "preco_tabela": reais(preco),
            "preco_atual": atual,
            "promocao": promo,
            "disponivel": disponivel,
            "descontinuado": bool(p["descontinuado"]),
            "em_breve": bool(p["em_breve"]),
            "unidades_em_estoque": int(p["estoque"]),
        }

    # -------------------------------------------------------------- busca
    @staticmethod
    def _filtrar_texto(df: pd.DataFrame, consulta: str, coluna: str = "_busca") -> pd.DataFrame:
        for tok in tokens_busca(consulta):
            df = df[df[coluna].str.contains(tok, regex=False)]
        return df

    def buscar_produtos(self, consulta="", categoria="", marca="", preco_min=0.0,
                        preco_max=0.0, so_disponiveis=False, limite=8) -> dict:
        df = self.produtos
        if consulta:
            achados = self._filtrar_texto(df, consulta)
            if achados.empty:  # nada no nome/marca/categoria: tenta também na descrição e specs
                achados = self._filtrar_texto(df, consulta, "_busca_ampla")
            df = achados
        if categoria:
            alvo = singular(norm(categoria))
            df = df[df["categoria"].map(lambda c: singular(norm(c))).str.contains(alvo, regex=False)]
        if marca:  # a base pode não ter coluna de marca: procura também no nome ("Yamaha C40")
            alvo = norm(marca)
            df = df[(df["marca"].map(norm) + " " + df["nome"].map(norm)).str.contains(alvo, regex=False)]

        pares = [(r, self.formatar_produto(r)) for _, r in df.iterrows()]
        if preco_min:
            pares = [(r, i) for r, i in pares if i["preco_atual"] >= preco_min]
        if preco_max:
            pares = [(r, i) for r, i in pares if i["preco_atual"] <= preco_max]
        if so_disponiveis:
            pares = [(r, i) for r, i in pares if i["disponivel"]]
        pares.sort(key=lambda par: (not par[1]["disponivel"], par[1]["preco_atual"]))
        limite = max(1, min(int(limite or 8), 20))
        escolhidos = pares[:limite]

        if len(escolhidos) <= 3:  # poucos resultados: inclui descrição e ficha técnica
            for r, item in escolhidos:
                if r["descricao"]:
                    item["descricao"] = r["descricao"][:400]
                if r["especificacoes"]:
                    item["especificacoes"] = r["especificacoes"]
        return {"total_encontrado": len(pares), "produtos": [i for _, i in escolhidos]}

    def obter_produto(self, id_ou_nome: str) -> pd.Series | None:
        df = self.produtos
        exato = df[df["id"].str.upper() == id_ou_nome.strip().upper()]
        if not exato.empty:
            return exato.iloc[0]
        achados = self._filtrar_texto(df, id_ou_nome)
        return achados.iloc[0] if len(achados) == 1 else None

    # ------------------------------------------------------------ promoções
    def listar_promocoes(self, categoria: str = "") -> dict:
        ativas = self._promocoes_ativas()
        if categoria:
            alvo = singular(norm(categoria))
            ativas = ativas[(ativas["_categoria_norm"] == alvo) | (ativas["produto_id"] != "")]
        itens = []
        for _, r in ativas.iterrows():
            alvo_txt = r["categoria"] or None
            desconto = round(float(r["desconto_pct"]), 2)
            item = {"nome": r["nome"] or None, "desconto_pct": desconto, "valido_ate": _data_br(r["fim"])}
            if r["produto_id"]:
                prod = self.obter_produto(r["produto_id"])
                if prod is not None:
                    info = self.formatar_produto(prod)
                    item.update({"produto": prod["nome"], "preco_tabela": info["preco_tabela"],
                                 "preco_atual": info["preco_atual"], "disponivel": info["disponivel"]})
                else:
                    item["produto"] = r["produto_id"]
            else:
                item["aplica_se_a_categoria"] = alvo_txt
            itens.append(item)
        return {"hoje": self._hoje().strftime("%d/%m/%Y"), "promocoes_ativas": itens}

    # --------------------------------------------------------------- pedidos
    def consultar_pedido(self, id_pedido: str, email: str) -> dict:
        nao_achou = {"erro": "Não encontrei um pedido com esse número e e-mail. "
                             "Peça ao cliente para conferir os dados."}
        pid = str(id_pedido).strip().upper().lstrip("#")
        ped = self.pedidos[self.pedidos["id"] == pid]
        if ped.empty:
            return nao_achou
        ped = ped.iloc[0]
        cli = self.clientes[self.clientes["id"] == ped["cliente_id"]]
        if cli.empty or cli.iloc[0]["email"] != str(email).strip().lower():
            return nao_achou  # mesma mensagem: não revela se o pedido existe
        hoje = pd.Timestamp(self._hoje())
        entrega, previsao = ped["data_entrega"], ped["previsao"]
        entregue = norm(ped["status"]) in {"delivered", "entregue"}
        resposta = {
            "id_pedido": ped["id"],
            "cliente": cli.iloc[0]["nome"],
            "data_pedido": _data_br(ped["data"]),
            "status": _status_pt(ped["status"]),
            "status_original": ped["status"],
            "valor_total": None if pd.isna(ped["valor"]) else round(float(ped["valor"]), 2),
            "itens": ped["itens_lista"] or ped["itens"] or None,
            "forma_de_pagamento": _pagamento_pt(ped["pagamento"]),
            "canal_de_compra": ped["canal"] or None,
            "codigo_rastreio": ped["rastreio"] or None,
            "previsao_entrega": _data_br(previsao),
            "observacoes": ped["observacoes"] or None,
        }
        if not pd.isna(entrega):
            resposta["data_entrega"] = _data_br(entrega)
            resposta["dias_desde_a_entrega"] = int((hoje - entrega).days)
        elif entregue and not pd.isna(previsao):
            # a base não registra a data real de entrega: a previsão é só uma aproximação
            resposta["dias_desde_a_previsao_de_entrega"] = int((hoje - previsao).days)
            resposta["aviso"] = ("A data real de entrega não consta no sistema; "
                                 "dias_desde_a_previsao_de_entrega é aproximado.")
        return resposta
