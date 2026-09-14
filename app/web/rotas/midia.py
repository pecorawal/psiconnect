"""Servir arquivos do object storage, sempre com autorização da aplicação.

O bucket é privado; nada aqui devolve um caminho direto para o storage. Duas
estratégias, escolhidas pelo que o arquivo é:

* **Foto de perfil** → redireciona para um link assinado de vida curta. A URL da
  aplicação (``/midia/foto/{id}``) é estável, então o template não precisa saber
  nada de storage, e o link real expira sozinho.

* **Documento de identificação** → a aplicação lê, **decifra** e devolve o
  conteúdo. Não há link: um endereço, por mais curto que seja o TTL, pode ser
  encaminhado, ficar no histórico ou cair num log de proxy. Para uma cópia de RG
  isso não compensa. Todo acesso é registrado em ``AcessoDocumento``.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Request, Response
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from app.core.deps import Config, Contexto, DbSession, ProvidersAtuais, UsuarioAtual
from app.core.erros import NaoEncontrado
from app.core.logging import get_logger
from app.core.tempo import agora_utc
from app.models import (
    AcessoDocumento,
    PerfilPaciente,
    PerfilProfissional,
    VerificacaoResponsavel,
)
from app.services.responsavel_service import ResponsavelService

log = get_logger(__name__)

router = APIRouter(prefix="/midia", tags=["midia"], include_in_schema=False)


def montar(templates: Jinja2Templates) -> APIRouter:
    @router.get("/foto/{profissional_id}", name="midia_foto")
    async def foto_profissional(
        request: Request,
        usuario: UsuarioAtual,
        sessao: DbSession,
        settings: Config,
        providers: ProvidersAtuais,
        profissional_id: uuid.UUID,
    ) -> Response:
        """Foto de perfil do profissional.

        Exige login — não porque a foto seja secreta (ela é mostrada a quem
        procura profissional), mas para que não dê para enumerar quem está
        cadastrado na plataforma varrendo ids.
        """
        perfil = await sessao.get(PerfilProfissional, profissional_id)
        if perfil is None or not perfil.foto_chave:
            raise NaoEncontrado("Foto não encontrada.")

        url = await providers.armazenamento.url_temporaria(
            perfil.foto_chave, ttl_segundos=settings.s3_url_ttl_segundos
        )
        # 302 e não 301: o link assinado muda a cada acesso e não pode ser
        # memorizado pelo navegador.
        return RedirectResponse(url, status_code=302)

    @router.get("/documento/{verificacao_id}", name="midia_documento")
    async def documento_responsavel(
        request: Request,
        usuario: UsuarioAtual,
        sessao: DbSession,
        settings: Config,
        providers: ProvidersAtuais,
        contexto: Contexto,
        verificacao_id: uuid.UUID,
    ) -> Response:
        """Documento de identificação do responsável legal.

        Só duas pessoas passam daqui: **o próprio responsável** e um
        administrador com permissão de leitura no módulo ``documentos``.
        Qualquer outra recebe 404 — não 403, para não confirmar que o documento
        existe.
        """
        verificacao = await sessao.get(VerificacaoResponsavel, verificacao_id)
        if verificacao is None:
            raise NaoEncontrado("Documento não encontrado.")

        motivo = await _motivo_do_acesso(sessao, usuario, verificacao)
        if motivo is None:
            log.warning(
                "documento.acesso_negado",
                usuario_id=str(usuario.id),
                verificacao_id=str(verificacao_id),
            )
            raise NaoEncontrado("Documento não encontrado.")

        servico = ResponsavelService(sessao, settings, providers.armazenamento)
        conteudo = await servico.ler_documento(verificacao)

        # Registrar ANTES de devolver: se o log falhar, o documento não sai.
        # Controle de acesso sem trilha é promessa sem prova.
        sessao.add(
            AcessoDocumento(
                usuario_id=usuario.id,
                recurso="VerificacaoResponsavel",
                recurso_id=verificacao.id,
                motivo=motivo,
                ip=contexto.ip,
                acessado_em=agora_utc(),
            )
        )
        await sessao.commit()

        return Response(
            content=conteudo,
            media_type=verificacao.documento_content_type or "application/octet-stream",
            headers={
                # inline: abre no navegador em vez de baixar — menos cópias
                # espalhadas pelo disco de quem consulta.
                "Content-Disposition": "inline",
                "Cache-Control": "no-store, private",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @router.get("/{token}", name="midia_local")
    async def arquivo_local(
        request: Request,
        usuario: UsuarioAtual,
        settings: Config,
        providers: ProvidersAtuais,
        token: str,
    ) -> Response:
        """Serve o "link assinado" do armazenamento local (só em dev).

        Existe para que ``LocalStorageProvider`` cumpra o mesmo contrato do S3 e
        as rotas fiquem idênticas nos dois ambientes.
        """
        armazenamento = providers.armazenamento
        validar = getattr(armazenamento, "validar_url_temporaria", None)
        if validar is None:
            raise NaoEncontrado("Arquivo não encontrado.")

        caminho = validar(token, settings.s3_url_ttl_segundos)
        conteudo = await armazenamento.ler(caminho)
        return Response(
            content=conteudo,
            media_type="image/jpeg",
            headers={"Cache-Control": "no-store, private"},
        )

    return router


async def _motivo_do_acesso(
    sessao: DbSession, usuario: object, verificacao: VerificacaoResponsavel
) -> str | None:
    """Por qual regra esta pessoa pode ver o documento — ``None`` se não pode."""
    from app.models import Usuario

    assert isinstance(usuario, Usuario)

    # 1. O próprio responsável, quando tem conta na plataforma.
    if verificacao.responsavel_usuario_id == usuario.id:
        return "dono"

    # 2. O responsável identificado pelo e-mail com que foi convidado.
    if (
        verificacao.responsavel_email
        and verificacao.responsavel_email.lower() == usuario.email.lower()
    ):
        return "dono"

    # 3. O adolescente sobre quem é o cadastro — é documento a respeito dele.
    paciente = await sessao.get(PerfilPaciente, verificacao.paciente_id)
    if paciente is not None and paciente.usuario_id == usuario.id:
        return "dono"

    # 4. Administrador com permissão de leitura em `documentos`.
    if usuario.permite("documentos", "read"):
        return "admin"

    return None
