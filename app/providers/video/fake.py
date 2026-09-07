"""Provedor de vídeo falso.

O ponto: **o fluxo de lobby e admissão é real** — só o WebRTC é falso. A sala
simulada é uma página servida pela própria aplicação, com cronômetro e botões,
de modo que a Fase 1 inteira pode ser demonstrada sem conta no daily.co.

Isso tira o provedor de vídeo do caminho crítico: se a conta demorar a ser
liberada, o walking skeleton fecha assim mesmo.
"""

from __future__ import annotations

import secrets
from datetime import datetime

from app.core.config import Settings
from app.providers.base import Participante, Sala


class FakeVideoProvider:
    nome = "fake"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._salas: dict[str, Sala] = {}
        #: Quem já foi admitido pelo profissional, por sala.
        self._admitidos: dict[str, set[str]] = {}

    async def criar_sala(self, nome: str, expira_em: datetime, *, privada: bool = True) -> Sala:
        sala = Sala(
            nome=nome,
            # Precisa bater com a rota real: o iframe da sala aponta para cá,
            # e um caminho errado só aparece como quadro em branco.
            url=f"{self._settings.app_base_url}/sessao/simulada/{nome}",
            expira_em=expira_em,
            provedor_sala_id=f"fake-sala-{secrets.token_hex(6)}",
        )
        self._salas[nome] = sala
        self._admitidos.setdefault(nome, set())
        return sala

    async def emitir_token(self, sala: str, participante: Participante, expira_em: datetime) -> str:
        # Formato opaco, como o real: nada de informação útil embutida.
        return f"faketoken.{sala}.{participante.id}.{secrets.token_urlsafe(16)}"

    async def admitir(self, sala: str, participante_id: str) -> None:
        self._admitidos.setdefault(sala, set()).add(participante_id)

    def foi_admitido(self, sala: str, participante_id: str) -> bool:
        return participante_id in self._admitidos.get(sala, set())

    async def encerrar_sala(self, nome: str) -> None:
        self._salas.pop(nome, None)
        self._admitidos.pop(nome, None)

    def url_de_entrada(self, sala_url: str, token: str) -> str:
        separador = "&" if "?" in sala_url else "?"
        return f"{sala_url}{separador}token={token}"
