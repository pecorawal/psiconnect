"""Checkout: compra do plano, crédito e confirmação do agendamento.

O fluxo decidido é **sintomas → horário → pagamento**, então quando o paciente
chega aqui o horário já está reservado em ``PENDENTE_PAGAMENTO`` e travado pela
constraint EXCLUDE. O checkout só decide se essa reserva vira consulta ou
expira.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.erros import NaoEncontrado, PagamentoRecusado, ReservaExpirada
from app.core.logging import get_logger
from app.core.tempo import agora_utc
from app.models import (
    Agendamento,
    CompraPlano,
    CreditoSessao,
    MetodoPagamento,
    Pagamento,
    PerfilPaciente,
    PerfilProfissional,
    Plano,
    SlugPlano,
    StatusAgendamento,
    StatusCompra,
    StatusCredito,
    StatusPagamento,
)
from app.providers.base import CobrancaRequest, PaymentProvider
from app.services.agendamento_service import AgendamentoService
from app.services.notificacao_service import NotificacaoService
from app.services.parametros_service import ParametrosService
from app.services.regras.precificacao import TabelaTaxas, calcular_reparticao

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ResultadoCheckout:
    compra: CompraPlano
    pagamento: Pagamento
    agendamento: Agendamento
    #: True quando o Pix ficou pendente e o paciente ainda precisa pagar.
    aguardando_pagamento: bool


class CheckoutService:
    def __init__(
        self,
        sessao: AsyncSession,
        parametros: ParametrosService,
        pagamentos: PaymentProvider,
    ) -> None:
        self.sessao = sessao
        self.parametros = parametros
        self.pagamentos = pagamentos

    async def listar_planos(self) -> list[Plano]:
        return list(
            (
                await self.sessao.execute(
                    select(Plano).where(Plano.ativo.is_(True)).order_by(Plano.ordem_exibicao)
                )
            )
            .scalars()
            .all()
        )

    async def finalizar(
        self,
        *,
        paciente: PerfilPaciente,
        agendamento_id: uuid.UUID,
        plano_slug: SlugPlano,
        metodo: MetodoPagamento,
    ) -> ResultadoCheckout:
        agendamento = await self.sessao.get(Agendamento, agendamento_id)
        if agendamento is None or agendamento.paciente_id != paciente.usuario_id:
            # 404 em vez de 403: não confirmamos nem a existência do
            # agendamento de outra pessoa.
            raise NaoEncontrado("Agendamento não encontrado.")

        if agendamento.status is not StatusAgendamento.PENDENTE_PAGAMENTO:
            if agendamento.status is StatusAgendamento.EXPIRADO:
                raise ReservaExpirada()
            raise NaoEncontrado("Este agendamento não está aguardando pagamento.")

        if agendamento.reserva_expira_em and agendamento.reserva_expira_em < agora_utc():
            raise ReservaExpirada()

        plano = await self.sessao.scalar(select(Plano).where(Plano.slug == plano_slug))
        if plano is None:
            raise NaoEncontrado("Plano não encontrado.")

        # Query explícita, não `agendamento.profissional`: acesso lazy em
        # contexto async estoura MissingGreenlet.
        profissional = await self.sessao.get(PerfilProfissional, agendamento.profissional_id)
        if profissional is None:
            raise NaoEncontrado("Profissional não encontrado.")

        # --- Valores -------------------------------------------------------
        valor_sessao = agendamento.valor_centavos
        desconto = plano.desconto_percentual
        valor_com_desconto = int(valor_sessao * (100 - desconto) / 100)
        valor_total = valor_com_desconto * plano.quantidade_sessoes

        comissao = await self.parametros.comissao_percentual()
        reparticao = calcular_reparticao(
            valor_total,
            percentual_comissao=comissao,
            metodo=metodo,
            tabela=await self._tabela_taxas(),
        )

        # --- Compra e créditos ---------------------------------------------
        compra = CompraPlano(
            paciente_id=paciente.usuario_id,
            profissional_id=agendamento.profissional_id,
            plano_id=plano.id,
            especialidade_id=agendamento.especialidade_id,
            quantidade_total=plano.quantidade_sessoes,
            valor_sessao_centavos=valor_com_desconto,
            valor_total_centavos=valor_total,
            # CONGELADO: mudar o parâmetro global amanhã não reprecifica esta
            # compra nem o repasse já calculado (ADR 0005).
            percentual_comissao_aplicado=comissao,
            expira_em=agora_utc() + timedelta(days=plano.validade_dias),
        )
        self.sessao.add(compra)
        await self.sessao.flush()

        creditos = [
            CreditoSessao(compra_plano_id=compra.id, expira_em=compra.expira_em)
            for _ in range(plano.quantidade_sessoes)
        ]
        self.sessao.add_all(creditos)
        await self.sessao.flush()

        # --- Cobrança -------------------------------------------------------
        chave = f"compra:{compra.id}"
        cobranca = await self.pagamentos.criar_cobranca(
            CobrancaRequest(
                valor_centavos=valor_total,
                metodo=metodo,
                descricao=f"{plano.nome} — {agendamento.especialidade.nome}",
                chave_idempotencia=chave,
                pagador_nome=paciente.usuario.nome_completo,
                pagador_email=paciente.usuario.email,
                comissao_centavos=reparticao.comissao_plataforma_centavos,
                # Sem isto o split não acontece: a cobrança é aceita e a
                # comissão simplesmente não é retida. Fica nulo enquanto o
                # profissional não conectar a conta por OAuth.
                recebedor_externo_id=profissional.mp_user_id,
                metadados={
                    "agendamento_id": str(agendamento.id),
                    "compra_id": str(compra.id),
                },
            )
        )

        pagamento = Pagamento(
            compra_plano_id=compra.id,
            provedor=self.pagamentos.nome,
            provedor_pagamento_id=cobranca.provedor_pagamento_id,
            metodo=metodo,
            status=cobranca.status,
            valor_bruto_centavos=valor_total,
            taxa_provedor_centavos=cobranca.taxa_provedor_centavos,
            comissao_plataforma_centavos=reparticao.comissao_plataforma_centavos,
            imposto_retido_centavos=reparticao.imposto_retido_centavos,
            liquido_profissional_centavos=reparticao.liquido_profissional_centavos,
            pix_qrcode=cobranca.pix_qrcode,
            pix_copia_cola=cobranca.pix_copia_cola,
            pix_expira_em=cobranca.pix_expira_em,
            chave_idempotencia=chave,
            agendamento_origem_id=agendamento.id,
            payload_bruto=dict(cobranca.payload_bruto),
        )
        self.sessao.add(pagamento)
        await self.sessao.flush()

        if cobranca.status is StatusPagamento.RECUSADO:
            compra.status = StatusCompra.CANCELADA
            await self.sessao.flush()
            log.info("checkout.recusado", compra_id=str(compra.id))
            raise PagamentoRecusado()

        if cobranca.status is StatusPagamento.APROVADO:
            await self.confirmar_pagamento(pagamento, agendamento)
            aguardando = False
        else:
            # Pix pendente: a reserva continua valendo até o TTL. O worker
            # expira se o pagamento não vier.
            aguardando = True

        return ResultadoCheckout(
            compra=compra,
            pagamento=pagamento,
            agendamento=agendamento,
            aguardando_pagamento=aguardando,
        )

    async def confirmar_pagamento(self, pagamento: Pagamento, agendamento: Agendamento) -> None:
        """Aprova a compra, consome um crédito e confirma o agendamento.

        Idempotente: chamar duas vezes (webhook reenviado, por exemplo) não
        consome dois créditos.
        """
        if agendamento.status is StatusAgendamento.CONFIRMADO:
            return

        pagamento.status = StatusPagamento.APROVADO
        pagamento.aprovado_em = agora_utc()

        compra = await self.sessao.get(CompraPlano, pagamento.compra_plano_id)
        if compra is None:
            raise NaoEncontrado("Compra não encontrada.")
        compra.status = StatusCompra.ATIVA

        await self._consumir_credito(compra.id, agendamento.id)

        servico = AgendamentoService(self.sessao, self.parametros)
        await servico.confirmar(agendamento.id)

        # Requisito: avisar por WhatsApp e e-mail que a consulta está marcada.
        # Vai para a outbox, não sai daqui: um provedor fora do ar não pode
        # derrubar a confirmação de um pagamento já aprovado.
        await NotificacaoService(self.sessao).notificar_agendamento_confirmado(agendamento)

        log.info(
            "checkout.confirmado",
            compra_id=str(compra.id),
            agendamento_id=str(agendamento.id),
        )

    async def desfazer_pagamento(
        self, pagamento: Pagamento, agendamento: Agendamento
    ) -> None:
        """Recusa, cancelamento ou estorno: solta o horário e devolve o crédito.

        Sem isto, um Pix que expira ou um estorno deixariam o horário travado
        (`PENDENTE_PAGAMENTO` participa da constraint de sobreposição) e o
        profissional perderia a vaga sem receber nada.

        Idempotente: um segundo webhook de estorno não faz nada.
        """
        compra = await self.sessao.get(CompraPlano, pagamento.compra_plano_id)
        if compra is None or compra.status is StatusCompra.CANCELADA:
            return

        compra.status = StatusCompra.CANCELADA

        # Devolve os créditos que ainda não foram usados. Os já consumidos
        # ficam como estão: a sessão pode já ter acontecido, e apagar o rastro
        # de quem pagou o quê inviabilizaria a conciliação.
        creditos = await self.sessao.scalars(
            select(CreditoSessao).where(
                CreditoSessao.compra_plano_id == compra.id,
                CreditoSessao.status == StatusCredito.CONSUMIDO,
                CreditoSessao.agendamento_id == agendamento.id,
            )
        )
        for credito in creditos:
            credito.status = StatusCredito.ESTORNADO
            credito.agendamento_id = None
            credito.consumido_em = None

        if agendamento.status in (
            StatusAgendamento.PENDENTE_PAGAMENTO,
            StatusAgendamento.CONFIRMADO,
        ):
            # EXPIRADO, não CANCELADO_*: a origem é a falta de pagamento, não
            # uma decisão do paciente nem do profissional. A distinção importa
            # para a política de no-show e para os relatórios.
            agendamento.status = StatusAgendamento.EXPIRADO

        await self.sessao.flush()
        log.info(
            "checkout.desfeito",
            compra_id=str(compra.id),
            agendamento_id=str(agendamento.id),
            motivo=pagamento.status.value,
        )

    async def _consumir_credito(
        self, compra_id: uuid.UUID, agendamento_id: uuid.UUID
    ) -> CreditoSessao:
        """Pega um crédito disponível e o amarra ao agendamento.

        Uma linha por crédito (e não um contador) é o que torna isto atômico e
        auditável: dá para responder "qual crédito pagou qual sessão?".
        """
        credito = await self.sessao.scalar(
            select(CreditoSessao)
            .where(
                CreditoSessao.compra_plano_id == compra_id,
                CreditoSessao.status == StatusCredito.DISPONIVEL,
            )
            .order_by(CreditoSessao.criado_em)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if credito is None:
            raise NaoEncontrado("Não há sessões disponíveis nesta compra.")

        credito.status = StatusCredito.CONSUMIDO
        credito.agendamento_id = agendamento_id
        credito.consumido_em = agora_utc()
        await self.sessao.flush()
        return credito

    async def _tabela_taxas(self) -> TabelaTaxas:
        from app.models.parametro import ChaveParametro

        return TabelaTaxas(
            pix=await self.parametros.decimal(ChaveParametro.TAXA_PIX_PERCENTUAL, Decimal("0.99")),
            debito=await self.parametros.decimal(
                ChaveParametro.TAXA_DEBITO_PERCENTUAL, Decimal("1.99")
            ),
            credito=await self.parametros.decimal(
                ChaveParametro.TAXA_CREDITO_PERCENTUAL, Decimal("4.98")
            ),
        )
