"""Ferramentas (function calling) expostas ao modelo.

Cada função é uma tool: o SDK gera o schema a partir da assinatura e da
docstring, então a docstring É o prompt da ferramenta — descreve quando usar.
"""
from __future__ import annotations

import datetime as dt
import json
import random
from pathlib import Path
from typing import Callable

from .rag import Retriever
from .repository import Loja, preco_com_desconto, reais


def _min_parcela(n: int) -> float:
    """Parcela mínima por faixa (manual, seção 3.1).

    Suposição: '3x sem mínimo, exceto abaixo de R$ 50,00' = parcela mínima de R$ 50.
    """
    if n == 1:
        return 0.0
    if n <= 3:
        return 50.0
    if n <= 6:
        return 80.0
    return 100.0


def build_tools(loja: Loja, agora_fn: Callable[[], dt.datetime], arquivo_encaminhamentos: Path,
                retriever: Retriever) -> list[Callable]:
    def buscar_produtos(
        consulta: str = "",
        categoria: str = "",
        marca: str = "",
        preco_min: float = 0,
        preco_max: float = 0,
        so_disponiveis: bool = False,
        limite: int = 8,
    ) -> dict:
        """Busca instrumentos no catálogo da loja e devolve preço, promoção vigente e estoque.

        Use SEMPRE que o cliente perguntar sobre preço, disponibilidade, modelos ou
        opções de um instrumento — nunca responda isso de memória.
        - consulta: palavras do modelo ou nome (ex.: "Takamine GD20", "Yamaha F310").
          Deixe vazio para listar só por filtros.
        - categoria: tipo de instrumento (ex.: "violão", "guitarra", "teclado", "sopro").
        - marca: fabricante (ex.: "Yamaha").
        - preco_min / preco_max: faixa de preço em reais, aplicada ao preço ATUAL
          (já com promoção). 0 = sem limite.
        - so_disponiveis: True para sugerir opções que o cliente pode comprar agora.
          Use False ao perguntar de um modelo específico, para saber se está
          esgotado ou descontinuado.
        Retorna preco_tabela (original), preco_atual, promocao (ou null), disponivel
        e descontinuado. Resultados disponíveis vêm primeiro.
        """
        return loja.buscar_produtos(consulta, categoria, marca, preco_min, preco_max,
                                    so_disponiveis, limite)

    def listar_promocoes_ativas(categoria: str = "") -> dict:
        """Lista as promoções vigentes HOJE, com percentual e data de término.

        Use quando o cliente perguntar sobre promoções, descontos ou Black Friday.
        Só afirme que existe promoção se ela aparecer aqui. Se o cliente citou uma
        campanha que não aparece, ela não está vigente (informe isso com transparência).
        - categoria: opcional, para filtrar (ex.: "violão").
        """
        return loja.listar_promocoes(categoria)

    def consultar_pedido(id_pedido: str, email_cliente: str) -> dict:
        """Consulta status, rastreio e dados de entrega de um pedido.

        Exige o número do pedido E o e-mail do cadastro do cliente (verificação de
        identidade; peça ambos antes de chamar). Retorna status, itens, pagamento,
        rastreio e previsão de entrega, além de dias_desde_a_entrega (ou, se a base só tiver a
        previsão, dias_desde_a_previsao_de_entrega, aproximado) para aplicar prazos de troca e
        devolução. O canal de compra (online ou loja) pode não constar: nesse caso, pergunte.
        """
        return loja.consultar_pedido(id_pedido, email_cliente)

    def simular_pagamento(id_produto: str, quantidade: int = 1) -> dict:
        """Calcula as condições de pagamento de um produto: PIX, débito, boleto e parcelamento.

        Use quando o cliente perguntar "quanto fica no PIX", "posso parcelar", "em quantas
        vezes". Faz a conta de forma exata (não calcule de cabeça). Se o produto estiver
        em promoção, o desconto de PIX NÃO se aplica.
        - id_produto: o id retornado por buscar_produtos (ex.: "81").
        """
        prod = loja.obter_produto(id_produto)
        if prod is None:
            return {"erro": "Produto não encontrado. Use o id retornado por buscar_produtos."}
        info = loja.formatar_produto(prod)
        if not info["disponivel"]:
            return {"erro": "Produto indisponível no momento.", "produto": info["nome"]}
        qtd = max(1, int(quantidade or 1))
        total = reais(info["preco_atual"] * qtd)
        em_promocao = info["promocao"] is not None

        max_n = max(n for n in range(1, 13) if n == 1 or total / n >= _min_parcela(n))
        pix = None if em_promocao else preco_com_desconto(total, 5)
        return {
            "produto": info["nome"],
            "quantidade": qtd,
            "preco_tabela_total": reais(info["preco_tabela"] * qtd),
            "total_atual": total,
            "em_promocao": em_promocao,
            "pix": {"valor": pix, "desconto_pct": 5} if pix is not None
                   else {"valor": None, "motivo": "PIX não acumula com preço promocional"},
            "debito_ou_boleto_a_vista": total,
            "credito_sem_juros": {
                "max_parcelas": max_n,
                "valor_parcela": reais(total / max_n),
            },
            "pode_combinar_formas_de_pagamento": total > 2000,
        }

    def consultar_politicas(pergunta: str) -> dict:
        """Busca nas políticas da loja os trechos que respondem à pergunta.

        Use para QUALQUER dúvida sobre regras: trocas, devoluções e arrependimento, garantia,
        formas de pagamento e parcelamento, frete e entrega, rastreamento, horários de
        funcionamento, regras de promoções, privacidade e dados da empresa (endereço,
        telefone, e-mail). Não use para preço, estoque ou pedidos (há ferramentas próprias).
        - pergunta: a dúvida em linguagem natural e completa
          (ex.: "prazo para devolver compra online por arrependimento").
        Retorna os trechos mais relevantes (secao, texto, relevancia). Base sua resposta
        neles; se nenhum responder, diga que não tem a informação.
        """
        trechos = retriever.buscar(pergunta, k=4)
        return {"trechos": trechos} if trechos else {"trechos": [], "aviso": "nada relevante encontrado"}

    def encaminhar_para_equipe(motivo: str, contato_cliente: str = "") -> dict:
        """Registra o caso e encaminha para a equipe humana. Retorna protocolo e prazo de retorno.

        Use para reclamações, defeito de produto/garantia, frete para outras cidades,
        cotação de instrumentos grandes e qualquer situação que você não consiga
        resolver com segurança. Resuma o caso em `motivo` e peça um contato
        (telefone ou e-mail) ao cliente antes de chamar.
        """
        agora = agora_fn()
        protocolo = f"EM-{agora:%Y%m%d}-{random.randint(100, 999)}"
        registro = {"protocolo": protocolo, "quando": agora.isoformat(timespec="seconds"),
                    "motivo": motivo, "contato": contato_cliente}
        arquivo_encaminhamentos.parent.mkdir(parents=True, exist_ok=True)
        with arquivo_encaminhamentos.open("a", encoding="utf-8") as f:
            f.write(json.dumps(registro, ensure_ascii=False) + "\n")
        return {"protocolo": protocolo, "prazo_de_retorno": "24 horas úteis"}

    return [buscar_produtos, listar_promocoes_ativas, consultar_pedido,
            simular_pagamento, consultar_politicas, encaminhar_para_equipe]
