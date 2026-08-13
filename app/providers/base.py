"""Portas para os serviços externos.

Cada integração é um ``Protocol`` com pelo menos duas implementações: uma *fake*
determinística e uma real. A escolha vem de ``Settings``.

Isso não é abstração por esporte -- é o que permite a Fase 1 fechar o fluxo
inteiro sem conta em serviço de terceiro, mantém os testes offline e rápidos, e
deixa trocar daily.co por LiveKit sem tocar em service nenhum quando o custo do
vídeo apertar.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Protocol, runtime_checkable

from app.models.enums import CanalNotificacao, MetodoPagamento, StatusPagamento

# ---------------------------------------------------------------------------
# Pagamento
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CobrancaRequest:
    valor_centavos: int
    metodo: MetodoPagamento
    descricao: str
    chave_idempotencia: str
    pagador_nome: str
    pagador_email: str
    #: Comissão da plataforma (application_fee do split do Mercado Pago).
    comissao_centavos: int = 0
    recebedor_externo_id: str | None = None
    metadados: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Cobranca:
    provedor_pagamento_id: str
    status: StatusPagamento
    valor_centavos: int
    taxa_provedor_centavos: int = 0
    pix_qrcode: str | None = None
    pix_copia_cola: str | None = None
    pix_expira_em: datetime | None = None
    url_checkout: str | None = None
    payload_bruto: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class EventoPagamento:
    """Resultado da validação de um webhook."""

    evento_id_externo: str
    tipo: str
    provedor_pagamento_id: str
    status: StatusPagamento
    payload: Mapping[str, Any] = field(default_factory=dict)


@runtime_checkable
class PaymentProvider(Protocol):
    nome: str

    async def criar_cobranca(self, req: CobrancaRequest) -> Cobranca: ...

    async def consultar(self, provedor_pagamento_id: str) -> Cobranca: ...

    async def estornar(
        self, provedor_pagamento_id: str, valor_centavos: int | None = None
    ) -> Cobranca: ...

    def validar_webhook(self, headers: Mapping[str, str], corpo: bytes) -> EventoPagamento: ...

    def taxa_estimada(self, metodo: MetodoPagamento, valor_centavos: int) -> Decimal:
        """Percentual estimado para exibir no simulador de recebimento."""
        ...


# ---------------------------------------------------------------------------
# Vídeo
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Sala:
    nome: str
    url: str
    expira_em: datetime
    provedor_sala_id: str | None = None


@dataclass(frozen=True, slots=True)
class Participante:
    id: str
    nome: str
    #: ``True`` para o profissional: é ele quem admite o paciente do lobby.
    eh_dono: bool = False


@runtime_checkable
class VideoProvider(Protocol):
    nome: str

    async def criar_sala(self, nome: str, expira_em: datetime, *, privada: bool = True) -> Sala: ...

    async def emitir_token(self, sala: str, participante: Participante, expira_em: datetime) -> str:
        """Token de entrada, com TTL curto.

        **Nunca é persistido**: um token guardado no banco vaza junto com o banco.
        """
        ...

    async def admitir(self, sala: str, participante_id: str) -> None: ...

    async def encerrar_sala(self, nome: str) -> None: ...


# ---------------------------------------------------------------------------
# Notificação
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ResultadoEnvio:
    sucesso: bool
    provedor_msg_id: str | None = None
    erro: str | None = None


@runtime_checkable
class NotificationProvider(Protocol):
    nome: str

    def suporta(self, canal: CanalNotificacao) -> bool: ...

    async def enviar(
        self,
        canal: CanalNotificacao,
        destino: str,
        template: str,
        contexto: Mapping[str, Any],
        chave_idempotencia: str,
    ) -> ResultadoEnvio: ...


# ---------------------------------------------------------------------------
# Transcrição e embeddings (Fase 5)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TrechoBruto:
    ordem: int
    inicio_ms: int
    fim_ms: int
    texto: str
    falante: str = "DESCONHECIDO"


@runtime_checkable
class TranscriptionProvider(Protocol):
    nome: str

    async def transcrever(self, audio: bytes, idioma: str = "pt") -> Sequence[TrechoBruto]: ...


@runtime_checkable
class EmbeddingProvider(Protocol):
    nome: str

    @property
    def dimensao(self) -> int: ...

    async def gerar(self, textos: Sequence[str]) -> Sequence[Sequence[float]]: ...


# ---------------------------------------------------------------------------
# Armazenamento
# ---------------------------------------------------------------------------


@runtime_checkable
class StorageProvider(Protocol):
    """Object storage privado.

    ``salvar`` devolve a **chave** do objeto, não uma URL: quem decide se
    alguém pode ver o arquivo é a aplicação, no momento do acesso.
    """

    nome: str

    async def salvar(self, caminho: str, conteudo: bytes, content_type: str) -> str: ...

    async def ler(self, caminho: str) -> bytes:
        """Lê o objeto. Usado para servir arquivo cifrado por rota autorizada."""
        ...

    async def remover(self, caminho: str) -> None: ...

    async def url_temporaria(self, caminho: str, ttl_segundos: int = 60) -> str:
        """Link assinado de curta duração. Só para arquivo não cifrado pelo app."""
        ...
