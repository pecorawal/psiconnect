"""Enfileiramento de notificações (outbox).

Nada é enviado dentro da transação do caso de uso. Se o envio falhasse — e
provedor externo falha —, o agendamento faria rollback junto; se o envio desse
certo mas a transação falhasse depois, o paciente receberia confirmação de uma
consulta que não existe. Gravar uma linha e deixar o worker enviar resolve os
dois lados.

A ``chave_idempotencia`` é o que impede reenvio: rodar o worker duas vezes, ou
reprocessar um agendamento, não gera segunda mensagem.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.tempo import TZ_BR, agora_utc, para_local
from app.models import (
    Agendamento,
    CanalNotificacao,
    Notificacao,
    StatusNotificacao,
    Usuario,
)

log = get_logger(__name__)

#: Templates conhecidos. Nomes estáveis: viram assunto de e-mail e, no
#: WhatsApp, precisam casar com um template HSM aprovado pela Meta (Fase 3).
TEMPLATE_AGENDAMENTO_CONFIRMADO = "agendamento_confirmado"
TEMPLATE_LINK_SESSAO = "link_sessao"
TEMPLATE_LEMBRETE_24H = "lembrete_24h"
TEMPLATE_AVALIACAO_PENDENTE = "avaliacao_pendente"


class NotificacaoService:
    def __init__(self, sessao: AsyncSession) -> None:
        self.sessao = sessao

    async def enfileirar(
        self,
        *,
        usuario: Usuario,
        canal: CanalNotificacao,
        template: str,
        contexto: dict[str, Any],
        chave_idempotencia: str,
        agendada_para: datetime | None = None,
        destino: str | None = None,
    ) -> Notificacao | None:
        """Grava uma notificação a enviar. Devolve ``None`` se já existia.

        Por padrão o destino sai do próprio usuário: e-mail para EMAIL, telefone
        para WhatsApp. Sem o dado cadastrado, o canal é pulado — não é erro, é
        ausência de dado.

        ``destino`` explícito existe para mensagens dirigidas a **terceiros**: o
        convite ao responsável legal precisa ir para o contato DELE, não para o
        do adolescente. Sem essa distinção, o menor receberia o próprio convite
        e poderia se autoautorizar — o que anularia a verificação do art. 14.
        """
        destino = destino or self._destino(usuario, canal)
        if not destino:
            return None

        ja_existe = await self.sessao.scalar(
            select(Notificacao.id).where(Notificacao.chave_idempotencia == chave_idempotencia)
        )
        if ja_existe is not None:
            return None

        notificacao = Notificacao(
            usuario_id=usuario.id,
            canal=canal,
            template=template,
            destino=destino,
            contexto=contexto,
            agendada_para=agendada_para or agora_utc(),
            chave_idempotencia=chave_idempotencia,
        )
        self.sessao.add(notificacao)
        await self.sessao.flush()
        return notificacao

    async def notificar_agendamento_confirmado(self, agendamento: Agendamento) -> int:
        """Requisito: avisar por WhatsApp **e** e-mail que a consulta está marcada."""
        paciente = agendamento.paciente.usuario
        contexto = self._contexto(agendamento, paciente.timezone)

        enviadas = 0
        for canal in (CanalNotificacao.EMAIL, CanalNotificacao.WHATSAPP):
            criada = await self.enfileirar(
                usuario=paciente,
                canal=canal,
                template=TEMPLATE_AGENDAMENTO_CONFIRMADO,
                contexto=contexto,
                chave_idempotencia=f"confirmado:{agendamento.id}:{canal.value}",
            )
            enviadas += 1 if criada else 0

        # O profissional também precisa saber.
        await self.enfileirar(
            usuario=agendamento.profissional.usuario,
            canal=CanalNotificacao.EMAIL,
            template=TEMPLATE_AGENDAMENTO_CONFIRMADO,
            contexto=contexto,
            chave_idempotencia=f"confirmado-prof:{agendamento.id}",
        )
        return enviadas

    async def agendar_link_da_sessao(
        self, agendamento: Agendamento, sala_url: str, antecedencia_min: int
    ) -> int:
        """R8 — o link sai ``antecedencia_min`` antes do horário.

        Vira uma linha com ``agendada_para``, não um ``sleep``: reentrante,
        auditável e testável sem esperar 20 minutos.
        """
        quando = agendamento.inicio_utc - timedelta(minutes=antecedencia_min)
        contexto = {**self._contexto(agendamento, TZ_BR.key), "sala_url": sala_url}

        enviadas = 0
        for usuario, papel in (
            (agendamento.paciente.usuario, "paciente"),
            (agendamento.profissional.usuario, "profissional"),
        ):
            for canal in (CanalNotificacao.EMAIL, CanalNotificacao.WHATSAPP):
                criada = await self.enfileirar(
                    usuario=usuario,
                    canal=canal,
                    template=TEMPLATE_LINK_SESSAO,
                    contexto=contexto,
                    chave_idempotencia=f"link:{agendamento.id}:{papel}:{canal.value}",
                    agendada_para=quando,
                )
                enviadas += 1 if criada else 0
        return enviadas

    async def pendentes(self, limite: int = 50) -> list[Notificacao]:
        """O que já venceu e ainda não foi enviado.

        ``FOR UPDATE SKIP LOCKED`` permite rodar mais de um worker sem que dois
        peguem a mesma linha — e sem que um espere o outro.
        """
        agora = agora_utc()
        return list(
            (
                await self.sessao.execute(
                    select(Notificacao)
                    .where(
                        Notificacao.status == StatusNotificacao.PENDENTE,
                        Notificacao.agendada_para <= agora,
                    )
                    .order_by(Notificacao.agendada_para)
                    .limit(limite)
                    .with_for_update(skip_locked=True)
                )
            )
            .scalars()
            .all()
        )

    # --- Internos -----------------------------------------------------------

    def _destino(self, usuario: Usuario, canal: CanalNotificacao) -> str | None:
        if canal is CanalNotificacao.EMAIL:
            return usuario.email
        if canal in (CanalNotificacao.WHATSAPP, CanalNotificacao.PUSH):
            return usuario.telefone_e164
        return str(usuario.id)

    def _contexto(self, agendamento: Agendamento, timezone: str) -> dict[str, Any]:
        """Só o necessário para montar a mensagem.

        Nada de sintoma ou conteúdo clínico: o corpo de um e-mail passa por
        servidores que não controlamos.
        """
        from zoneinfo import ZoneInfo

        tz = ZoneInfo(timezone)
        local = para_local(agendamento.inicio_utc, tz)
        return {
            "agendamento_id": str(agendamento.id),
            "profissional_nome": agendamento.profissional.nome_exibicao,
            "profissional_foto": agendamento.profissional.foto_url,
            "paciente_nome": agendamento.paciente.usuario.primeiro_nome,
            "data": local.strftime("%d/%m/%Y"),
            "hora": local.strftime("%H:%M"),
            "duracao_min": agendamento.duracao_min,
        }


def chave_de(prefixo: str, *partes: uuid.UUID | str) -> str:
    return ":".join([prefixo, *(str(p) for p in partes)])
