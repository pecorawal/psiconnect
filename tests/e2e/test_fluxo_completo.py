"""O walking skeleton inteiro, de ponta a ponta.

Cadastro do profissional → agenda → cadastro do paciente → sintomas → matching
→ escolha do horário → pagamento → sala → lobby → admissão → encerramento.

É o portão de regressão da Fase 1: se este teste quebra, o produto parou de
fazer o que promete.
"""

from __future__ import annotations

import re

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.sessao_web import COOKIE_CSRF
from app.models import (
    Agendamento,
    Avaliacao,
    CreditoSessao,
    EventoPontuacao,
    EventoSessao,
    Sessao,
    Sintoma,
    SintomaEspecialidade,
    StatusAgendamento,
    StatusCredito,
    StatusSessao,
    TipoEventoSessao,
    TipoPontuacao,
)

pytestmark = [pytest.mark.e2e, pytest.mark.db]

UUID_RE = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"


def navegador(app: FastAPI) -> AsyncClient:
    """Um cliente por pessoa — como dois navegadores diferentes."""
    return AsyncClient(
        transport=ASGITransport(app=app), base_url="http://teste", follow_redirects=False
    )


async def csrf(c: AsyncClient, caminho: str = "/") -> dict[str, str]:
    await c.get(caminho)
    return {"X-CSRF-Token": c.cookies[COOKIE_CSRF]}


class TestFluxoCompleto:
    async def test_do_cadastro_ate_a_sessao_encerrada(
        self, app: FastAPI, sessao: AsyncSession, settings: Settings
    ) -> None:
        # ------------------------------------------------------------------
        # 1. O profissional se cadastra
        # ------------------------------------------------------------------
        async with navegador(app) as psi, navegador(app) as pac:
            headers = await csrf(psi, "/cadastro/profissional")
            r = await psi.post(
                "/cadastro/profissional",
                data={
                    "nome_completo": "Ana Paula Ribeiro",
                    "email": "ana.e2e@teste.br",
                    "senha": "senha-forte-e2e-2026",
                    "aceite": "1",
                },
                headers=headers,
            )
            assert r.status_code == 303, "cadastro do profissional"
            assert r.headers["location"] == "/profissional/perfil"

            # 2. Preenche o perfil
            r = await psi.post(
                "/profissional/perfil",
                data={
                    "nome_exibicao": "Ana Paula Ribeiro",
                    "conselho": "CRP",
                    "registro_numero": "654321",
                    "registro_uf": "SP",
                    "descricao": "Atendo adultos com foco em ansiedade e sono.",
                },
                headers=headers,
            )
            assert r.status_code == 303
            assert r.headers["location"] == "/profissional/especialidades"

            # 3. Escolhe especialidades com preço.
            #    Não "as duas primeiras": o teste precisa de uma especialidade
            #    que de fato case com o sintoma que o paciente vai marcar,
            #    senão o matching (corretamente) não devolve ninguém.
            par = (
                await sessao.execute(
                    select(SintomaEspecialidade.sintoma_id, SintomaEspecialidade.especialidade_id)
                    .join(Sintoma, Sintoma.id == SintomaEspecialidade.sintoma_id)
                    .where(
                        SintomaEspecialidade.peso == 5,
                        # Sintoma de risco tem fluxo próprio; aqui queremos o caminho comum.
                        Sintoma.bandeira_risco.is_(False),
                    )
                    .limit(1)
                )
            ).first()
            assert par is not None, "o seed de sintomas/especialidades precisa estar carregado"
            sintoma_alvo, especialidade_alvo = par

            pagina = (await psi.get("/profissional/especialidades")).text
            assert str(especialidade_alvo) in pagina

            dados: dict[str, object] = {
                "especialidade_id": [str(especialidade_alvo)],
                f"preco_{especialidade_alvo}": "150,00",
            }
            r = await psi.post("/profissional/especialidades", data=dados, headers=headers)
            assert r.status_code == 303
            assert r.headers["location"] == "/profissional/agenda"

            # 4. Monta a agenda semanal (todos os dias, manhã e tarde, para
            #    garantir que haja slot livre no horizonte de busca)
            for dia in range(7):
                for inicio, fim in (("09:00", "12:00"), ("14:00", "18:00")):
                    r = await psi.post(
                        "/profissional/agenda",
                        data={"dia_semana": str(dia), "inicio": inicio, "fim": fim},
                        headers={**headers, "HX-Request": "true"},
                    )
                    assert r.status_code == 200

            # 4b. A plataforma verifica o registro no conselho e aprova.
            #     Sem isso o profissional NÃO aparece para pacientes — e é assim
            #     que deve ser: em produção quem aprova é o admin (Fase 6),
            #     depois de conferir o CRP no cadastro público do CFP.
            from app.models import PerfilProfissional, StatusCadastro

            perfil = await sessao.scalar(
                select(PerfilProfissional).where(PerfilProfissional.registro_numero == "654321")
            )
            assert perfil is not None
            assert perfil.status_cadastro is StatusCadastro.EM_ANALISE, (
                "perfil completo deve ficar aguardando verificação, não aprovado"
            )
            r = await psi.post(f"/dev/aprovar-profissional/{perfil.usuario_id}", headers=headers)
            assert r.status_code == 303

            # ------------------------------------------------------------------
            # 5. O paciente se cadastra
            # ------------------------------------------------------------------
            headers_pac = await csrf(pac, "/cadastro/paciente")
            r = await pac.post(
                "/cadastro/paciente",
                data={
                    "nome_completo": "Bruno Alves",
                    "email": "bruno.e2e@teste.br",
                    "senha": "senha-forte-e2e-2026",
                    "data_nascimento": "1992-03-15",
                    "aceite": "1",
                },
                headers=headers_pac,
            )
            assert r.status_code == 303
            assert r.headers["location"] == "/paciente/sintomas"

            # 6. Conta como está se sentindo — marcando o sintoma que leva à
            #    especialidade da profissional.
            pagina = (await pac.get("/paciente/sintomas")).text
            assert str(sintoma_alvo) in pagina
            dados_sintomas: dict[str, object] = {
                "sintoma_id": [str(sintoma_alvo)],
                f"intensidade_{sintoma_alvo}": "5",
            }
            r = await pac.post("/paciente/sintomas", data=dados_sintomas, headers=headers_pac)
            assert r.status_code == 303
            assert r.headers["location"] == "/paciente/profissionais"

            # 7. Vê o ranking de profissionais
            pagina = (await pac.get("/paciente/profissionais")).text
            assert "Ana Paula Ribeiro" in pagina, "o matching precisa achar a profissional"
            assert "aderência" in pagina

            # Usa a profissional que ESTE teste criou, não a primeira do
            # ranking: o banco de desenvolvimento tem a profissional do seed de
            # demonstração, que também atende essa especialidade e pode vir na
            # frente. Fixar o id torna o teste determinístico.
            prof_id = str(perfil.usuario_id)
            assert f"/paciente/agendar/{prof_id}" in pagina

            # 8. Escolhe um horário
            pagina = (await pac.get(f"/paciente/agendar/{prof_id}")).text
            inicios = re.findall(r'name="inicio" value="([^"]+)"', pagina)
            esp_ids = re.findall(rf'name="especialidade_id"\s+value="({UUID_RE})"', pagina)
            assert inicios, "deveria haver slots livres"

            r = await pac.post(
                f"/paciente/agendar/{prof_id}",
                data={"inicio": inicios[0], "especialidade_id": esp_ids[0]},
                headers=headers_pac,
            )
            assert r.status_code == 303
            destino = r.headers["location"]
            assert destino.startswith("/paciente/checkout/")
            agendamento_id = destino.rsplit("/", 1)[1]

            # O horário já está travado, mesmo antes de pagar.
            agendamento = await sessao.get(Agendamento, agendamento_id)
            assert agendamento is not None
            assert agendamento.status is StatusAgendamento.PENDENTE_PAGAMENTO
            assert agendamento.reserva_expira_em is not None

            # 9. Paga (cartão aprova na hora no provider fake)
            pagina = (await pac.get(f"/paciente/checkout/{agendamento_id}")).text
            assert "R$" in pagina
            r = await pac.post(
                f"/paciente/checkout/{agendamento_id}",
                data={"plano": "AVULSO", "metodo": "CARTAO_CREDITO"},
                headers=headers_pac,
            )
            assert r.status_code == 303
            assert r.headers["location"] == "/painel"

            await sessao.refresh(agendamento)
            assert agendamento.status is StatusAgendamento.CONFIRMADO
            assert agendamento.reserva_expira_em is None

            # O crédito foi consumido e ligado a este agendamento.
            credito = await sessao.scalar(
                select(CreditoSessao).where(CreditoSessao.agendamento_id == agendamento.id)
            )
            assert credito is not None
            assert credito.status is StatusCredito.CONSUMIDO

            # 10. O painel mostra nome e foto de quem vai atender (requisito)
            pagina = (await pac.get("/painel")).text
            assert "Ana Paula Ribeiro" in pagina

            # ------------------------------------------------------------------
            # 11. A sala é preparada (o worker faria em T-20min)
            # ------------------------------------------------------------------
            r = await psi.post(f"/dev/preparar-sala/{agendamento_id}", headers=headers)
            assert r.status_code == 303

            sessao_atendimento = await sessao.scalar(
                select(Sessao).where(Sessao.agendamento_id == agendamento.id)
            )
            assert sessao_atendimento is not None
            assert sessao_atendimento.status is StatusSessao.SALA_PRONTA
            assert sessao_atendimento.sala_url

            # 12. O paciente vê o disclaimer e precisa consentir
            r = await pac.get(f"/sessao/{agendamento_id}")
            assert r.status_code == 200
            assert "transcri" in r.text.lower()
            assert "não são gravados" in r.text

            # Sem marcar a caixa, não entra.
            r = await pac.post(f"/sessao/{agendamento_id}/consentir", data={}, headers=headers_pac)
            assert r.status_code == 422

            r = await pac.post(
                f"/sessao/{agendamento_id}/consentir",
                data={"ciente": "1"},
                headers=headers_pac,
            )
            assert r.status_code == 303

            # 13. O paciente fica no lobby aguardando
            r = await pac.get(f"/sessao/{agendamento_id}/lobby")
            assert r.status_code == 200
            assert "Aguardando" in r.text or "espera" in r.text.lower()

            # 14. O profissional entra e admite
            r = await psi.get(f"/sessao/{agendamento_id}/lobby")
            assert r.status_code == 303  # profissional entra direto na sala

            r = await psi.post(f"/sessao/{agendamento_id}/admitir", headers=headers)
            assert r.status_code == 303

            # 15. Agora o polling do paciente manda ele para a sala
            r = await pac.get(f"/sessao/{agendamento_id}/estado")
            assert r.headers.get("HX-Redirect") == f"/sessao/{agendamento_id}/sala"

            r = await pac.get(f"/sessao/{agendamento_id}/sala")
            assert r.status_code == 200
            assert "sala-simulada" in r.text  # provider fake, fluxo real

            # 16. O profissional encerra
            r = await psi.post(f"/sessao/{agendamento_id}/encerrar", headers=headers)
            assert r.status_code == 303

            await sessao.refresh(agendamento)
            await sessao.refresh(sessao_atendimento)
            assert agendamento.status is StatusAgendamento.REALIZADO
            assert sessao_atendimento.status is StatusSessao.ENCERRADA

            # ------------------------------------------------------------------
            # 17. A trilha de eventos conta a história inteira
            # ------------------------------------------------------------------
            eventos = list(
                (
                    await sessao.execute(
                        select(EventoSessao.tipo)
                        .where(EventoSessao.sessao_id == sessao_atendimento.id)
                        .order_by(EventoSessao.ocorrido_em)
                    )
                )
                .scalars()
                .all()
            )
            assert TipoEventoSessao.SALA_CRIADA in eventos
            assert TipoEventoSessao.DISCLAIMER_ACEITO in eventos
            assert TipoEventoSessao.ENTROU_LOBBY in eventos
            assert TipoEventoSessao.ADMITIDO in eventos
            assert TipoEventoSessao.ENCERRADA in eventos

            # 18. A gamificação registrou a sessão realizada
            pontos = list(
                (
                    await sessao.execute(
                        select(EventoPontuacao).where(
                            EventoPontuacao.referencia_id == sessao_atendimento.id
                        )
                    )
                )
                .scalars()
                .all()
            )
            tipos = {p.tipo for p in pontos}
            assert TipoPontuacao.SESSAO_REALIZADA in tipos

            # ------------------------------------------------------------------
            # 19. R11 — a avaliação pendente bloqueia MARCAR nova sessão
            # ------------------------------------------------------------------
            pagina = (await pac.get(f"/paciente/agendar/{prof_id}")).text
            inicios = re.findall(r'name="inicio" value="([^"]+)"', pagina)
            esp_ids = re.findall(rf'name="especialidade_id"\s+value="({UUID_RE})"', pagina)
            r = await pac.post(
                f"/paciente/agendar/{prof_id}",
                data={"inicio": inicios[0], "especialidade_id": esp_ids[0]},
                headers=headers_pac,
            )
            assert r.status_code == 422
            assert "Avalie sua última sessão" in r.text

            # ...mas continua podendo sair e ver os próprios dados. Bloquear
            # isso prenderia a pessoa no produto e violaria o art. 18 da LGPD.
            assert (await pac.get("/painel")).status_code == 200

            # 20. Responde as 3 perguntas
            r = await pac.get(f"/avaliacao/{agendamento_id}")
            assert r.status_code == 200
            assert "Como foi usar a plataforma" in r.text
            assert "Como foi o atendimento do profissional" in r.text
            assert "seu cuidado" in r.text

            r = await pac.post(
                f"/avaliacao/{agendamento_id}",
                data={
                    "nota_plataforma": "5",
                    "nota_profissional": "5",
                    "nota_proprio_cuidado": "4",
                    "comentario_profissional": "Me senti acolhido.",
                },
                headers=headers_pac,
            )
            assert r.status_code == 303

            avaliacao = await sessao.scalar(
                select(Avaliacao).where(Avaliacao.agendamento_id == agendamento.id)
            )
            assert avaliacao is not None
            assert avaliacao.nota_proprio_cuidado == 4

            # 21. Com a avaliação em dia, marcar volta a funcionar
            r = await pac.post(
                f"/paciente/agendar/{prof_id}",
                data={"inicio": inicios[0], "especialidade_id": esp_ids[0]},
                headers=headers_pac,
            )
            assert r.status_code == 303, "avaliação em dia deveria liberar o agendamento"


class TestProtecoesDoFluxo:
    async def test_paciente_nao_ve_sessao_de_outro(
        self, app: FastAPI, sessao: AsyncSession, settings: Settings
    ) -> None:
        """404, não 403: confirmar a existência já vazaria informação."""
        from tests import fabricas as f
        from tests.web.test_profissional import logar

        prof = await f.criar_profissional(sessao)
        vitima = await f.criar_paciente(sessao)
        esp = await f.criar_especialidade(sessao)
        await f.adicionar_especialidade(sessao, prof, esp, ordem=1)
        agendamento = await f.criar_agendamento(sessao, prof, vitima, esp)

        bisbilhoteiro = await f.criar_paciente(sessao)
        async with navegador(app) as c:
            await logar(c, app, sessao, settings, bisbilhoteiro.usuario_id)
            r = await c.get(f"/sessao/{agendamento.id}")
        assert r.status_code in (404, 422)
