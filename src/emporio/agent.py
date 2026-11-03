"""Loop de function calling com o Gemini.

Fazemos o loop manualmente (em vez do chamador automático do SDK) para registrar
quais ferramentas o modelo escolheu a cada turno — isso deixa o roteamento
"dados x políticas" visível nos logs e nos exemplos de conversa.
"""
from __future__ import annotations

import time
from typing import Callable

from google.genai import errors, types

from .session import Sessao, Turno

MSG_ERRO = ("Opa, tive um problema aqui do meu lado e não consegui concluir. "
            "Pode tentar de novo em instantes? Se preferir, fale com a equipe pelo WhatsApp.")


class Atendente:
    def __init__(self, client, model: str, tools: list[Callable],
                 montar_system: Callable[[], str], sessao: Sessao,
                 max_passos: int = 6, log: Callable[[str], None] | None = None,
                 espera_retry: float = 2.0):
        self.client = client
        self.model = model
        self.tools = {f.__name__: f for f in tools}
        self._funcoes = tools
        self.montar_system = montar_system
        self.sessao = sessao
        self.max_passos = max_passos
        self.log = log or (lambda _m: None)
        self.espera_retry = espera_retry
        self.contents: list[types.Content] = []
        for t in sessao.turnos:  # retoma conversa salva
            self.contents.append(types.Content(role="user", parts=[types.Part(text=t.cliente)]))
            self.contents.append(types.Content(role="model", parts=[types.Part(text=t.assistente)]))

    # ------------------------------------------------------------------ API
    def responder(self, texto: str, agora: str = "") -> str:
        n_antes = len(self.contents)
        self.contents.append(types.Content(role="user", parts=[types.Part(text=texto)]))
        chamadas: list[dict] = []
        resposta = MSG_ERRO
        try:
            resposta = self._loop(chamadas)
        except Exception as e:  # noqa: BLE001 - nunca derrubar o atendimento
            self.log(f"erro: {type(e).__name__}: {e}")
            del self.contents[n_antes:]  # descarta turno incompleto para não corromper o histórico
            self.sessao.turnos.append(Turno(texto, MSG_ERRO, chamadas, agora))
            return MSG_ERRO
        self.sessao.turnos.append(Turno(texto, resposta, chamadas, agora))
        return resposta

    # ----------------------------------------------------------- internals
    def _config(self) -> types.GenerateContentConfig:
        return types.GenerateContentConfig(
            system_instruction=self.montar_system(),
            tools=self._funcoes,
            temperature=0.3,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )

    def _gerar(self):
        config = self._config()
        for tentativa in range(3):
            try:
                return self.client.models.generate_content(
                    model=self.model, contents=self.contents, config=config)
            except errors.APIError as e:
                if getattr(e, "code", None) in (429, 500, 503) and tentativa < 2:
                    espera = self.espera_retry * (2 ** tentativa)
                    self.log(f"API retornou {e.code}; nova tentativa em {espera:.0f}s")
                    time.sleep(espera)
                    continue
                raise

    def _loop(self, chamadas: list[dict]) -> str:
        for _ in range(self.max_passos):
            resp = self._gerar()
            if not resp.candidates or not resp.candidates[0].content:
                raise RuntimeError("resposta vazia ou bloqueada pelo modelo")
            self.contents.append(resp.candidates[0].content)
            pedidos = resp.function_calls or []
            if not pedidos:
                texto = (resp.text or "").strip()
                if not texto:
                    raise RuntimeError("modelo não devolveu texto")
                return texto
            partes = []
            for fc in pedidos:
                args = dict(fc.args or {})
                resultado = self._executar(fc.name, args)
                chamadas.append({"nome": fc.name, "args": args, "resultado": resultado})
                self.log(f"tool {fc.name}({args})")
                partes.append(types.Part.from_function_response(
                    name=fc.name, response={"resultado": resultado}))
            self.contents.append(types.Content(role="user", parts=partes))
        raise RuntimeError("limite de passos de ferramentas excedido")

    def _executar(self, nome: str, args: dict):
        funcao = self.tools.get(nome)
        if funcao is None:
            return {"erro": f"ferramenta desconhecida: {nome}"}
        try:
            return funcao(**args)
        except Exception as e:  # noqa: BLE001 - o modelo pode se recuperar do erro
            return {"erro": f"falha ao executar {nome}: {e}"}
