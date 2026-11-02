"""Leitura e limpeza dos CSVs da loja.

Como os CSVs reais podem variar (separador, encoding, nomes de arquivo e de coluna em
português ou inglês, formato de preço e de data), tudo passa por aqui e sai com um esquema
canônico fixo. Se uma coluna da sua base tiver outro nome, basta incluí-lo em ALIASES.
"""
from __future__ import annotations

import io
import json
import math
import re
import unicodedata
from pathlib import Path

import pandas as pd

# --------------------------------------------------------------------------
# Utilitários de texto
# --------------------------------------------------------------------------


def norm(valor) -> str:
    """Minúsculas, sem acento e com espaços normalizados (para comparar textos)."""
    s = unicodedata.normalize("NFKD", str(valor if valor is not None else ""))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s).strip().casefold()


def singular(token: str) -> str:
    """Singularização grosseira para casar 'violões' com 'violão'."""
    if len(token) > 4 and token.endswith("oes"):
        return token[:-3] + "ao"
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def tokens_busca(texto: str) -> list[str]:
    return [singular(t) for t in re.findall(r"[a-z0-9]+", norm(texto)) if len(t) > 1]


# --------------------------------------------------------------------------
# Conversores
# --------------------------------------------------------------------------


def parse_dinheiro(valor) -> float | None:
    """'R$ 1.299,90' | '1299.90' | '599.9' | '1.299' | 1299.9  ->  float."""
    if valor is None or (isinstance(valor, float) and math.isnan(valor)):
        return None
    if isinstance(valor, (int, float)):
        return float(valor)
    s = re.sub(r"[^\d,.\-]", "", str(valor))
    if not s or s in {"-", ",", "."}:
        return None
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:
        s = s.replace(",", ".")
    elif s.count(".") > 1 or re.fullmatch(r"-?\d{1,3}\.\d{3}", s):
        s = s.replace(".", "")  # '1.299' em base brasileira = milhar
    try:
        return float(s)
    except ValueError:
        return None


def parse_percentual(valor) -> float | None:
    """'10%' | 10 | '0,15' -> 10.0 | 10.0 | 15.0  (valores < 1 são fração)."""
    n = parse_dinheiro(str(valor).replace("%", "")) if valor not in (None, "") else None
    if n is None:
        return None
    return round(n * 100, 4) if 0 < n < 1 else n


def parse_inteiro(valor) -> int:
    n = parse_dinheiro(valor)
    return int(n) if n is not None else 0


_VERDADEIROS = {"1", "true", "t", "sim", "s", "yes", "y", "ativo", "ativa", "active"}


def parse_bool(valor) -> bool | None:
    """'1' | 'true' | 'sim' -> True; '0' | 'false' -> False; vazio -> None (sem informação)."""
    s = norm(valor)
    if s in {"", "nan", "none"}:
        return None
    return s in _VERDADEIROS


def _parse_uma_data(valor) -> pd.Timestamp:
    s = str(valor).strip()
    if s.casefold() in {"", "nan", "nat", "none"}:
        return pd.NaT
    # ISO (aaaa-mm-dd) é lida à parte: com dayfirst=True o pandas inverte mês e dia
    # ('2026-11-10' viraria 11/out). Já 'dd/mm/aaaa' precisa de dayfirst.
    if re.match(r"^\d{4}-\d{1,2}-\d{1,2}", s):
        return pd.to_datetime(s[:10], format="%Y-%m-%d", errors="coerce")
    return pd.to_datetime(s, dayfirst=True, errors="coerce")


def parse_data(serie: pd.Series) -> pd.Series:
    """Datas em dd/mm/aaaa ou ISO -> Timestamp (NaT quando inválida)."""
    return pd.to_datetime(serie.map(_parse_uma_data), errors="coerce")


def parse_json(valor) -> dict | None:
    try:
        d = json.loads(valor) if str(valor).strip() else None
    except (TypeError, ValueError):
        return None
    return d if isinstance(d, dict) and d else None


# --------------------------------------------------------------------------
# Esquema canônico
# --------------------------------------------------------------------------

# canônico -> nomes aceitos (já normalizados: sem acento, minúsculos, "_"), em português e inglês
ALIASES: dict[str, dict[str, list[str]]] = {
    "categorias": {
        "id": ["id", "categoria_id", "id_categoria", "category_id"],
        "nome": ["nome", "categoria", "name"],
    },
    "produtos": {
        "id": ["id", "id_produto", "produto_id", "product_id", "codigo", "cod", "sku"],
        "nome": ["nome", "produto", "nome_produto", "modelo", "name", "product_name"],
        "marca": ["marca", "fabricante", "brand"],
        "categoria": ["categoria", "tipo", "linha", "familia", "category"],
        "categoria_id": ["categoria_id", "id_categoria", "category_id"],
        "descricao": ["descricao", "description"],
        "especificacoes": ["especificacoes", "specs"],
        "preco": ["preco", "preco_tabela", "valor", "preco_venda", "preco_atual", "price", "price_brl"],
        "estoque": ["estoque", "quantidade", "qtd", "qtd_estoque", "estoque_atual", "stock", "stock_quantity"],
        "status": ["status", "situacao", "ativo"],
    },
    "clientes": {
        "id": ["id", "id_cliente", "cliente_id", "customer_id", "codigo"],
        "nome": ["nome", "cliente", "nome_cliente", "name", "customer_name"],
        "email": ["email", "e_mail"],
        "telefone": ["telefone", "celular", "whatsapp", "fone", "phone"],
        "cidade": ["cidade", "municipio", "city"],
    },
    "pedidos": {
        "id": ["id", "id_pedido", "pedido", "numero", "numero_pedido", "order_id"],
        "cliente_id": ["cliente_id", "id_cliente", "cliente", "customer_id"],
        "data": ["data", "data_pedido", "data_compra", "criado_em", "order_date"],
        "status": ["status", "situacao", "status_pedido"],
        "valor": ["valor", "valor_total", "total", "total_brl", "total_amount"],
        "itens": ["itens", "produtos", "produto", "descricao"],
        "canal": ["canal", "origem", "canal_venda", "tipo_venda", "channel", "sales_channel"],
        "rastreio": ["rastreio", "codigo_rastreio", "tracking", "tracking_code"],
        "data_entrega": ["data_entrega", "entregue_em", "data_recebimento", "delivery_date", "delivered_at"],
        "previsao": ["previsao_entrega", "previsao", "data_prevista", "estimated_delivery"],
        "pagamento": ["pagamento", "forma_pagamento", "payment_method"],
        "observacoes": ["observacoes", "obs", "notes"],
    },
    "itens_pedido": {
        "pedido_id": ["pedido_id", "id_pedido", "order_id", "pedido"],
        "produto_id": ["produto_id", "id_produto", "product_id"],
        "quantidade": ["quantidade", "qtd", "quantity"],
    },
    "promocoes": {
        "id": ["id", "id_promocao", "promocao_id", "promotion_id"],
        "nome": ["nome", "promocao", "campanha", "descricao", "description"],
        "produto_id": ["produto_id", "id_produto", "produto", "sku", "product_id"],
        "categoria": ["categoria", "tipo", "linha", "category"],
        "desconto_pct": ["desconto_pct", "desconto", "percentual", "percentual_desconto",
                         "desconto_percentual", "discount_percent", "discount"],
        "ativa": ["ativa", "ativo", "is_active", "active"],
        "inicio": ["inicio", "data_inicio", "vigencia_inicio", "valido_de", "start_date"],
        "fim": ["fim", "data_fim", "data_termino", "validade", "valido_ate", "vigencia_fim", "end_date"],
    },
}

OBRIGATORIAS = {
    "categorias": {"id", "nome"},
    "produtos": {"id", "nome", "preco"},
    "clientes": {"id", "email"},
    "pedidos": {"id", "cliente_id", "status"},
    "itens_pedido": {"pedido_id", "produto_id"},
    "promocoes": {"desconto_pct"},
}

# palavras que o nome do arquivo deve conter / não pode conter
ARQUIVOS: dict[str, tuple[list[str], list[str]]] = {
    "produtos": (["produto", "product"], []),
    "clientes": (["cliente", "customer"], []),
    "itens_pedido": (["item"], []),
    "pedidos": (["pedido", "order"], ["item"]),
    "promocoes": (["promo"], []),
    "categorias": (["categor"], []),
}


def _norm_coluna(c: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", norm(c)).strip("_")


_MOJIBAKE = re.compile("[ÃÂ][\u0080-¿]")


def _ler_texto(caminho: Path) -> str:
    for enc in ("utf-8-sig", "latin-1"):
        try:
            texto = caminho.read_text(encoding=enc)
            break
        except UnicodeDecodeError:
            continue
    else:  # pragma: no cover
        raise ValueError(f"Não consegui decodificar {caminho}")
    if _MOJIBAKE.search(texto):  # UTF-8 que foi lido como ANSI e salvo de novo ('ViolÃ£o')
        for enc in ("cp1252", "latin-1"):
            try:
                return texto.encode(enc).decode("utf-8")
            except (UnicodeEncodeError, UnicodeDecodeError):
                continue
    return texto


def _ler_csv(caminho: Path) -> pd.DataFrame:
    texto = _ler_texto(caminho)
    cabecalho = texto.splitlines()[0] if texto.strip() else ""
    sep = ";" if cabecalho.count(";") > cabecalho.count(",") else ","
    df = pd.read_csv(io.StringIO(texto), sep=sep, dtype=str, keep_default_na=False)
    df.columns = [_norm_coluna(c) for c in df.columns]
    for c in df.columns:
        df[c] = df[c].astype(str).str.strip()
    return df


def _canonizar(df: pd.DataFrame, tabela: str, origem: Path) -> pd.DataFrame:
    saida = pd.DataFrame(index=df.index)
    for canonico, aceitos in ALIASES[tabela].items():
        achada = next((a for a in aceitos if a in df.columns), None)
        if achada is not None:
            saida[canonico] = df[achada]
        elif canonico in OBRIGATORIAS[tabela]:
            raise ValueError(
                f"{origem.name}: não encontrei a coluna '{canonico}'. "
                f"Colunas do arquivo: {list(df.columns)}. "
                f"Inclua o nome certo em ALIASES['{tabela}']['{canonico}'] (data_loader.py)."
            )
        else:
            saida[canonico] = ""
    return saida


def _achar_csv(data_dir: Path, tabela: str) -> Path | None:
    incluir, excluir = ARQUIVOS[tabela]
    for p in sorted(data_dir.glob("*.csv")):
        nome = norm(p.stem)
        if any(k in nome for k in incluir) and not any(k in nome for k in excluir):
            return p
    return None


# --------------------------------------------------------------------------
# Limpeza por tabela
# --------------------------------------------------------------------------

_STATUS_INATIVO = ("descont", "discont", "inativ", "inactive", "fora de linha", "archiv", "removed", "deleted")
_STATUS_EM_BREVE = ("coming", "em breve", "pre venda", "pre-venda", "lancamento")
_FALSOS = {"0", "false", "nao", "n", "f"}


def _limpar_categorias(df: pd.DataFrame) -> pd.DataFrame:
    return df.drop_duplicates(subset=["id"], keep="last").reset_index(drop=True)


def _limpar_produtos(df: pd.DataFrame, categorias: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["nome"] = df["nome"].str.replace(r"\s+", " ", regex=True).str.strip()
    df["preco"] = df["preco"].map(parse_dinheiro)
    df["estoque"] = df["estoque"].map(parse_inteiro)
    df["categoria"] = df["categoria"].str.strip().str.title()  # só para categorias escritas direto no produto
    if not categorias.empty:  # categoria vem de outra tabela (category_id -> nome, sem alterar a caixa)
        mapeada = df["categoria_id"].map(dict(zip(categorias["id"], categorias["nome"])))
        df["categoria"] = mapeada.where(mapeada.notna(), df["categoria"])
    df["categoria"] = df["categoria"].fillna("")
    st = df["status"].map(norm)
    df["descontinuado"] = st.map(lambda s: any(k in s for k in _STATUS_INATIVO) or s in _FALSOS)
    df["em_breve"] = st.map(lambda s: any(k in s for k in _STATUS_EM_BREVE))  # ainda não está à venda
    df["especificacoes"] = df["especificacoes"].map(parse_json)
    df = df.dropna(subset=["preco"])
    df = df[df["nome"] != ""].drop_duplicates(subset=["id"], keep="last")
    # texto de busca em tokens no singular, igual ao da consulta ('violões' -> 'violao')
    juntar = lambda t: " ".join(tokens_busca(t))  # noqa: E731
    df["_busca"] = (df["nome"] + " " + df["marca"] + " " + df["categoria"]).map(juntar)
    specs_txt = df["especificacoes"].map(lambda d: " ".join(map(str, d.values())) if d else "")
    df["_busca_ampla"] = (df["nome"] + " " + df["marca"] + " " + df["categoria"] + " "
                          + df["descricao"] + " " + specs_txt).map(juntar)
    return df.reset_index(drop=True)


def _limpar_clientes(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["email"] = df["email"].str.lower()
    return df.drop_duplicates(subset=["id"], keep="last").reset_index(drop=True)


def _chave_pedido(valor) -> str:
    return str(valor).strip().upper().lstrip("#")


def _limpar_pedidos(df: pd.DataFrame, itens: pd.DataFrame, produtos: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["id"] = df["id"].map(_chave_pedido)
    df["valor"] = df["valor"].map(parse_dinheiro)
    for c in ("data", "data_entrega", "previsao"):
        df[c] = parse_data(df[c])
    # itens do pedido vêm de outra tabela (order_items -> nome do produto)
    nomes = dict(zip(produtos["id"], produtos["nome"]))
    por_pedido: dict[str, list[dict]] = {}
    for _, r in itens.iterrows():
        por_pedido.setdefault(_chave_pedido(r["pedido_id"]), []).append({
            "produto": nomes.get(r["produto_id"], f"produto {r['produto_id']}"),
            "produto_id": r["produto_id"],
            "quantidade": parse_inteiro(r["quantidade"]) or 1,
        })
    df["itens_lista"] = df["id"].map(lambda i: por_pedido.get(i, []))
    return df.drop_duplicates(subset=["id"], keep="last").reset_index(drop=True)


def _limpar_promocoes(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["desconto_pct"] = df["desconto_pct"].map(parse_percentual)
    df["inicio"] = parse_data(df["inicio"])
    df["fim"] = parse_data(df["fim"])
    df["ativa"] = df["ativa"].map(parse_bool)
    df["_categoria_norm"] = df["categoria"].map(lambda c: singular(norm(c)))
    return df.dropna(subset=["desconto_pct"]).reset_index(drop=True)


def _vazia(tabela: str) -> pd.DataFrame:
    return pd.DataFrame({c: pd.Series(dtype=str) for c in ALIASES[tabela]})


def _carregar_bruta(data_dir: Path, tabela: str) -> pd.DataFrame:
    caminho = _achar_csv(data_dir, tabela)
    if caminho is None:
        if tabela == "produtos":
            achados = [p.name for p in sorted(data_dir.glob("*.csv"))]
            raise FileNotFoundError(
                f"Nenhum CSV de produtos encontrado em {data_dir}. O nome do arquivo deve conter "
                f"'produto' ou 'product'. CSVs na pasta: {achados or 'nenhum'}")
        return _vazia(tabela)
    return _canonizar(_ler_csv(caminho), tabela, caminho)


def carregar_tudo(data_dir: Path) -> dict[str, pd.DataFrame]:
    """Carrega e limpa as tabelas. Só 'produtos' é indispensável."""
    data_dir = Path(data_dir)
    bruto = {t: _carregar_bruta(data_dir, t) for t in ALIASES}
    categorias = _limpar_categorias(bruto["categorias"])
    produtos = _limpar_produtos(bruto["produtos"], categorias)
    return {
        "categorias": categorias,
        "produtos": produtos,
        "clientes": _limpar_clientes(bruto["clientes"]),
        "pedidos": _limpar_pedidos(bruto["pedidos"], bruto["itens_pedido"], produtos),
        "promocoes": _limpar_promocoes(bruto["promocoes"]),
    }
