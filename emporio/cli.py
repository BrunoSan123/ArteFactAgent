"""Interface de linha de comando."""
from __future__ import annotations

import argparse
import os
import sys

from google import genai

from . import config
from .agent import Atendente
from .policies import carregar_politicas
from .rag import Retriever, dividir_por_secao, gemini_embedder
from .prompts import montar_system_prompt
from .repository import Loja
from .session import Sessao
from .tools import build_tools

AJUDA = "Comandos: /novo (nova conversa) · /exportar [nome] (salva em examples/) · /sair"


def montar_atendente(sessao: Sessao, verbose: bool) -> Atendente:
    chave = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not chave:
        sys.exit("Defina GEMINI_API_KEY no arquivo .env (veja .env.example).")
    client = genai.Client(api_key=chave)
    loja = Loja(config.DATA_DIR, lambda: config.agora().date())
    log = (lambda m: print(f"  \033[2m[{m}]\033[0m")) if verbose else None
    retriever = Retriever(
        dividir_por_secao(carregar_politicas(config.DATA_DIR)),
        embedder=gemini_embedder(client, config.EMBEDDING_MODEL),
        cache_dir=config.CACHE_DIR, modelo=config.EMBEDDING_MODEL, log=log)
    tools = build_tools(loja, config.agora, config.SESSIONS_DIR / "encaminhamentos.jsonl", retriever)
    return Atendente(
        client=client,
        model=config.MODEL,
        tools=tools,
        montar_system=lambda: montar_system_prompt(config.agora_legivel()),
        sessao=sessao,
        log=log,
    )


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Téo — atendente virtual da Empório da Música (com RAG)")
    ap.add_argument("--sessao", help="id de uma sessão salva para retomar")
    ap.add_argument("-v", "--verbose", action="store_true", help="mostra as ferramentas chamadas")
    ap.add_argument("--checar-dados", action="store_true", help="lista inconsistências nos CSVs e sai")
    args = ap.parse_args(argv)

    if args.checar_dados:
        from .qualidade import relatorio
        for linha in relatorio(config.DATA_DIR):
            print("-", linha)
        return

    sessao = (args.sessao and Sessao.carregar(config.SESSIONS_DIR, args.sessao)) or Sessao.nova(config.agora())
    atendente = montar_atendente(sessao, args.verbose)

    if config.USANDO_DADOS_EXEMPLO:
        print("\033[33m⚠ Usando dados de EXEMPLO (data/.dados_de_exemplo). "
              "Substitua os CSVs pelos reais e apague esse arquivo.\033[0m")
    print(f"Sessão: {sessao.id} · modelo: {config.MODEL}\n{AJUDA}\n")
    if sessao.turnos:
        print(f"(conversa retomada, {len(sessao.turnos)} turnos anteriores)\n")

    while True:
        try:
            texto = input("Você: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not texto:
            continue
        if texto == "/sair":
            break
        if texto == "/novo":
            sessao = Sessao.nova(config.agora())
            atendente = montar_atendente(sessao, args.verbose)
            print(f"Nova conversa: {sessao.id}")
            continue
        if texto.startswith("/exportar"):
            nome = texto.partition(" ")[2].strip() or sessao.id
            destino = config.EXAMPLES_DIR / f"{nome}.md"
            config.EXAMPLES_DIR.mkdir(exist_ok=True)
            destino.write_text(sessao.para_markdown(), encoding="utf-8")
            print(f"Salvo em {destino}")
            continue
        resposta = atendente.responder(texto, agora=config.agora().isoformat(timespec="seconds"))
        print(f"Téo: {resposta}\n")
        sessao.salvar(config.SESSIONS_DIR)

    if sessao.turnos:
        sessao.salvar(config.SESSIONS_DIR)
        print(f"Conversa salva (retome com: python main.py --sessao {sessao.id})")
