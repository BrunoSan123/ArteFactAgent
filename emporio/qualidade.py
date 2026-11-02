"""Checagens de qualidade dos dados (python main.py --checar-dados).

Não corrige nada: lista o que parece inconsistente na base e como o agente trata cada caso.
"""
from __future__ import annotations

from pathlib import Path

from .data_loader import carregar_tudo


def relatorio(data_dir: Path) -> list[str]:
    t = carregar_tudo(data_dir)
    p, c, o, pr = t["produtos"], t["clientes"], t["pedidos"], t["promocoes"]
    achados: list[str] = []

    for email, g in c[c.duplicated("email", keep=False)].groupby("email"):
        achados.append(f"E-mail repetido entre clientes {', '.join(g['id'])}: {email} "
                       "(a verificação do pedido usa o e-mail do cliente dono do pedido)")
    for _, r in p[p["descontinuado"] & (p["estoque"] > 0)].iterrows():
        achados.append(f"Produto {r['id']} '{r['nome']}' está descontinuado mas tem {r['estoque']} un. em estoque "
                       "(tratado como indisponível)")
    for _, r in p[p["em_breve"] & (p["estoque"] > 0)].iterrows():
        achados.append(f"Produto {r['id']} '{r['nome']}' é 'em breve' mas tem {r['estoque']} un. em estoque "
                       "(tratado como ainda não à venda)")

    ids = set(p["id"])
    for _, r in pr[(pr["produto_id"] != "") & ~pr["produto_id"].isin(ids)].iterrows():
        achados.append(f"Promoção {r['id']} aponta para o produto inexistente {r['produto_id']}")

    precos = dict(zip(p["id"], p["preco"]))
    clientes = set(c["id"])
    for _, r in o.iterrows():
        if r["cliente_id"] not in clientes:
            achados.append(f"Pedido {r['id']} aponta para o cliente inexistente {r['cliente_id']}")
        itens = r["itens_lista"]
        if itens and all(i["produto_id"] in precos for i in itens) and r["valor"] == r["valor"]:
            soma = sum(precos[i["produto_id"]] * i["quantidade"] for i in itens)
            if abs(soma - r["valor"]) > 0.01:
                achados.append(f"Pedido {r['id']}: total R$ {r['valor']:.2f} difere da soma dos itens a preço atual "
                               f"R$ {soma:.2f} (o agente informa o total do pedido, não recalcula)")
    return achados or ["Nenhuma inconsistência encontrada."]
