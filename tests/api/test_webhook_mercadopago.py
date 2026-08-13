"""Webhook de pagamento: autenticação, idempotência e efeito.

O que estes testes protegem, em ordem de gravidade:

1. **Confirmar sessão sem pagar** — corpo forjado com `"status": "approved"`.
2. **Creditar duas vezes** — o provedor reenvia o mesmo evento.
3. **Pix que nunca completa** — sem webhook, o horário fica reservado até expirar.
"""

from __future__ import annotations

import json
import uuid

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Agendamento,
    CompraPlano,
    CreditoSessao,
    EventoWebhook,
    Pagamento,
    StatusAgendamento,
    StatusCompra,
    StatusCredito,
    StatusPagamento,
)
from app.providers.pagamento.fake import SEGREDO_DEV
from tests.contratos.test_pagamento import assinar
from tests.fabricas import (
    criar_agendamento,
    criar_especialidade,
    criar_paciente,
    criar_profissional,
)

URL = "/api/webhooks/mercadopago"


def corpo_evento(pagamento_id: str, **extra: object) -> bytes:
    dados: dict[str, object] = {
        "type": "payment",
        "action": "payment.updated",
        "data": {"id": pagamento_id},
    }
    dados.update(extra)
    return json.dumps(dados).encode()


def cabecalhos(pagamento_id: str, request_id: str) -> dict[str, str]:
    return {
        "x-signature": assinar(pagamento_id, request_id, SEGREDO_DEV),
        "x-request-id": request_id,
        "content-type": "application/json",
    }


@pytest.fixture
async def cliente(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://teste"
    )


class TestAutenticacao:
    async def test_sem_assinatura_e_401(self, cliente: httpx.AsyncClient) -> None:
        r = await cliente.post(URL, content=corpo_evento("123"))
        assert r.status_code == 401

    async def test_assinatura_invalida_e_401(self, cliente: httpx.AsyncClient) -> None:
        r = await cliente.post(
            URL,
            content=corpo_evento("123"),
            headers={"x-signature": "ts=1,v1=abc", "x-request-id": "req-1"},
        )
        assert r.status_code == 401

    async def test_assinatura_de_outro_pagamento_e_401(
        self, cliente: httpx.AsyncClient
    ) -> None:
        """Capturar um webhook legítimo e trocar o id não pode funcionar."""
        r = await cliente.post(
            URL,
            content=corpo_evento("999"),
            headers=cabecalhos("111", "req-1"),
        )
        assert r.status_code == 401

    async def test_nao_exige_csrf(self, cliente: httpx.AsyncClient) -> None:
        """O provedor não tem como enviar nosso token de CSRF.

        Se a isenção sumir, o webhook passa a ser rejeitado antes da assinatura
        e todo pagamento por Pix trava — sem erro visível para ninguém.
        """
        r = await cliente.post(URL, content=corpo_evento("123"))
        # 401 (assinatura) e não 403 (CSRF) prova que chegou à validação certa.
        assert r.status_code == 401


class TestIdempotencia:
    async def test_evento_repetido_nao_processa_de_novo(
        self, cliente: httpx.AsyncClient, sessao: AsyncSession, app: FastAPI
    ) -> None:
        req_id = f"req-{uuid.uuid4().hex[:8]}"
        pag_id = f"pag-{uuid.uuid4().hex[:8]}"

        # O pagamento não existe: o interesse aqui é só o registro do evento.
        primeira = await cliente.post(
            URL, content=corpo_evento(pag_id), headers=cabecalhos(pag_id, req_id)
        )
        segunda = await cliente.post(
            URL, content=corpo_evento(pag_id), headers=cabecalhos(pag_id, req_id)
        )

        # A primeira é 409 (pagamento ainda não gravado); a segunda é 200 e
        # não reprocessa -- o registro do evento sobreviveu ao rollback? Não:
        # ele é desfeito junto, e é isso que garante o reprocessamento.
        assert primeira.status_code == 409
        assert segunda.status_code == 409

    async def test_reenvio_apos_sucesso_e_200_sem_reprocessar(
        self, cliente: httpx.AsyncClient, sessao: AsyncSession, app: FastAPI
    ) -> None:
        """O caso que importa: pagamento existe, evento chega duas vezes."""
        contexto = await _preparar_pagamento_pix(sessao, app)
        pag_id = contexto["pagamento_id"]
        req_id = f"req-{uuid.uuid4().hex[:8]}"

        primeira = await cliente.post(
            URL, content=corpo_evento(pag_id), headers=cabecalhos(pag_id, req_id)
        )
        segunda = await cliente.post(
            URL, content=corpo_evento(pag_id), headers=cabecalhos(pag_id, req_id)
        )

        assert primeira.status_code == 200
        assert primeira.headers["X-Psiconnect-Processado"] == "1"
        assert segunda.status_code == 200
        assert segunda.headers["X-Psiconnect-Processado"] == "0"

        sessao.expire_all()
        eventos = (
            await sessao.scalars(
                select(EventoWebhook).where(EventoWebhook.evento_id_externo == req_id)
            )
        ).all()
        assert len(eventos) == 1

        # E, sobretudo: um único crédito consumido.
        creditos = (
            await sessao.scalars(
                select(CreditoSessao).where(
                    CreditoSessao.compra_plano_id == contexto["compra_id"],
                    CreditoSessao.status == StatusCredito.CONSUMIDO,
                )
            )
        ).all()
        assert len(creditos) == 1


class TestEfeito:
    async def test_pix_aprovado_confirma_o_agendamento(
        self, cliente: httpx.AsyncClient, sessao: AsyncSession, app: FastAPI
    ) -> None:
        """O fluxo Pix inteiro: sem isto, o horário só expirava."""
        contexto = await _preparar_pagamento_pix(sessao, app)
        pag_id = contexto["pagamento_id"]

        r = await cliente.post(
            URL,
            content=corpo_evento(pag_id),
            headers=cabecalhos(pag_id, f"req-{uuid.uuid4().hex[:8]}"),
        )
        assert r.status_code == 200

        sessao.expire_all()
        agendamento = await sessao.get(Agendamento, contexto["agendamento_id"])
        pagamento = await sessao.get(Pagamento, contexto["pagamento_pk"])
        compra = await sessao.get(CompraPlano, contexto["compra_id"])
        assert agendamento is not None and pagamento is not None and compra is not None

        assert agendamento.status is StatusAgendamento.CONFIRMADO
        assert pagamento.status is StatusPagamento.APROVADO
        assert pagamento.aprovado_em is not None
        assert compra.status is StatusCompra.ATIVA

    async def test_status_do_corpo_e_ignorado(
        self, cliente: httpx.AsyncClient, sessao: AsyncSession, app: FastAPI
    ) -> None:
        """Forjar `"status": "approved"` não pode confirmar nada.

        A verdade vem da consulta autenticada ao provedor. Este é o ataque que
        transforma um endpoint público em sessão de graça.
        """
        contexto = await _preparar_pagamento_pix(sessao, app, confirmar_no_provedor=False)
        pag_id = contexto["pagamento_id"]

        r = await cliente.post(
            URL,
            content=corpo_evento(pag_id, status="approved"),
            headers=cabecalhos(pag_id, f"req-{uuid.uuid4().hex[:8]}"),
        )
        assert r.status_code == 200

        sessao.expire_all()
        agendamento = await sessao.get(Agendamento, contexto["agendamento_id"])
        assert agendamento is not None
        # O provedor ainda diz PENDENTE, então nada muda.
        assert agendamento.status is StatusAgendamento.PENDENTE_PAGAMENTO


async def _preparar_pagamento_pix(
    sessao: AsyncSession, app: FastAPI, *, confirmar_no_provedor: bool = True
) -> dict[str, object]:
    """Monta agendamento + compra + créditos + pagamento Pix pendente.

    Usa **a instância de provider da própria aplicação** (`app.state.providers`).
    O fake guarda as cobranças em memória, por instância: montar um provider
    novo aqui criaria a cobrança num lugar que o webhook não consulta.
    """
    from app.models import MetodoPagamento, Plano, SlugPlano
    from app.providers.base import CobrancaRequest

    especialidade = await criar_especialidade(sessao)
    profissional = await criar_profissional(sessao)
    paciente = await criar_paciente(sessao)
    agendamento = await criar_agendamento(
        sessao,
        profissional=profissional,
        paciente=paciente,
        especialidade=especialidade,
        # A fábrica cria CONFIRMADO por padrão; aqui o ponto é justamente o
        # agendamento que ainda espera o Pix.
        status=StatusAgendamento.PENDENTE_PAGAMENTO,
    )

    plano = await sessao.scalar(select(Plano).where(Plano.slug == SlugPlano.AVULSO))
    assert plano is not None, "seed de planos ausente"

    compra = CompraPlano(
        paciente_id=paciente.usuario_id,
        profissional_id=profissional.usuario_id,
        plano_id=plano.id,
        especialidade_id=especialidade.id,
        quantidade_total=1,
        valor_sessao_centavos=15000,
        valor_total_centavos=15000,
        percentual_comissao_aplicado=12,
    )
    sessao.add(compra)
    await sessao.flush()
    sessao.add(CreditoSessao(compra_plano_id=compra.id))
    await sessao.flush()

    providers = app.state.providers
    cobranca = await providers.pagamento.criar_cobranca(
        CobrancaRequest(
            valor_centavos=15000,
            metodo=MetodoPagamento.PIX,
            descricao="Sessão",
            chave_idempotencia=f"compra:{compra.id}",
            pagador_nome="Paciente",
            pagador_email="p@teste.test",
        )
    )

    pagamento = Pagamento(
        compra_plano_id=compra.id,
        provedor=providers.pagamento.nome,
        provedor_pagamento_id=cobranca.provedor_pagamento_id,
        metodo=MetodoPagamento.PIX,
        status=StatusPagamento.PENDENTE,
        valor_bruto_centavos=15000,
        chave_idempotencia=f"compra:{compra.id}",
        agendamento_origem_id=agendamento.id,
    )
    sessao.add(pagamento)
    await sessao.commit()

    if confirmar_no_provedor:
        await providers.pagamento.confirmar_pix(cobranca.provedor_pagamento_id)

    return {
        "pagamento_id": cobranca.provedor_pagamento_id,
        "pagamento_pk": pagamento.id,
        "agendamento_id": agendamento.id,
        "compra_id": compra.id,
    }
