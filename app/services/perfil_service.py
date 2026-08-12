"""Perfil do profissional: dados, foto e especialidades."""

from __future__ import annotations

import io
import uuid
from dataclasses import dataclass

from PIL import Image, UnidentifiedImageError
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.erros import (
    DescricaoMuitoLonga,
    ErroDominio,
    MaximoEspecialidadesAtingido,
    NaoEncontrado,
)
from app.core.logging import get_logger
from app.models import (
    LIMITE_DESCRICAO,
    LIMITE_ESPECIALIDADES,
    Conselho,
    Especialidade,
    PerfilProfissional,
    Usuario,
)
from app.models.perfil import ProfissionalEspecialidade
from app.providers.base import StorageProvider
from app.services.parametros_service import ParametrosService

log = get_logger(__name__)

#: A foto é exibida em card e em avatar; 800px cobre os dois com folga.
LADO_MAXIMO_PX = 800
TAMANHO_MAXIMO_BYTES = 5 * 1024 * 1024
FORMATOS_ACEITOS = {"JPEG", "PNG", "WEBP"}

# fmt: off
UFS = frozenset({
    "AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT", "MS",
    "MG", "PA", "PB", "PR", "PE", "PI", "RJ", "RN", "RS", "RO", "RR", "SC",
    "SP", "SE", "TO",
})
# fmt: on


class RegistroInvalido(ErroDominio):
    codigo = "registro_invalido"
    mensagem_padrao = "Informe um número de registro válido."


class ImagemInvalida(ErroDominio):
    codigo = "imagem_invalida"
    mensagem_padrao = "Envie uma imagem JPEG, PNG ou WEBP de até 5 MB."


class PrecoInvalido(ErroDominio):
    codigo = "preco_invalido"
    mensagem_padrao = "A faixa de preço precisa ser mínimo ≤ padrão ≤ máximo."


@dataclass(frozen=True, slots=True)
class DadosPerfil:
    nome_exibicao: str
    conselho: Conselho
    registro_numero: str
    registro_uf: str
    descricao: str


class PerfilProfissionalService:
    def __init__(
        self,
        sessao: AsyncSession,
        parametros: ParametrosService,
        armazenamento: StorageProvider | None = None,
    ) -> None:
        self.sessao = sessao
        self.parametros = parametros
        self.armazenamento = armazenamento

    # --- Perfil -------------------------------------------------------------

    async def salvar_perfil(self, usuario: Usuario, dados: DadosPerfil) -> PerfilProfissional:
        descricao = dados.descricao.strip()
        if len(descricao) > LIMITE_DESCRICAO:
            raise DescricaoMuitoLonga(
                f"A descrição tem {len(descricao)} caracteres; o limite é {LIMITE_DESCRICAO}.",
                campo="descricao",
            )

        uf = dados.registro_uf.strip().upper()
        if uf not in UFS:
            raise RegistroInvalido("UF inválida.", campo="registro_uf")

        numero = "".join(c for c in dados.registro_numero if c.isalnum())
        if not numero:
            raise RegistroInvalido(campo="registro_numero")

        perfil = usuario.perfil_profissional
        if perfil is None:
            perfil = PerfilProfissional(
                usuario_id=usuario.id,
                nome_exibicao=dados.nome_exibicao.strip(),
                conselho=dados.conselho,
                registro_numero=numero,
                registro_uf=uf,
                descricao=descricao,
            )
            self.sessao.add(perfil)
        else:
            perfil.nome_exibicao = dados.nome_exibicao.strip()
            perfil.conselho = dados.conselho
            perfil.registro_numero = numero
            perfil.registro_uf = uf
            perfil.descricao = descricao

        try:
            await self.sessao.flush()
        except IntegrityError as exc:
            await self.sessao.rollback()
            if "uq_registro_conselho" in str(exc.orig):
                raise RegistroInvalido(
                    "Este registro já está cadastrado por outro profissional.",
                    campo="registro_numero",
                ) from exc
            raise
        return perfil

    async def salvar_foto(
        self, perfil: PerfilProfissional, conteudo: bytes, nome_arquivo: str
    ) -> str:
        """Normaliza e grava a foto de perfil.

        Reprocessar a imagem com Pillow não é só estética: reencodar descarta
        metadados EXIF -- que incluem **coordenadas de GPS** em foto de celular.
        Publicar a casa do profissional junto com a foto seria um belo vazamento.
        """
        if self.armazenamento is None:
            raise ErroDominio("Armazenamento não configurado.")
        if len(conteudo) > TAMANHO_MAXIMO_BYTES:
            raise ImagemInvalida("A imagem passa de 5 MB.", campo="foto")

        try:
            # verify() invalida o objeto, então é preciso reabrir para trabalhar.
            Image.open(io.BytesIO(conteudo)).verify()
            original = Image.open(io.BytesIO(conteudo))
        except (UnidentifiedImageError, OSError) as exc:
            raise ImagemInvalida(campo="foto") from exc

        if original.format not in FORMATOS_ACEITOS:
            raise ImagemInvalida(campo="foto")

        imagem: Image.Image = original
        if imagem.mode not in ("RGB", "L"):
            imagem = imagem.convert("RGB")
        imagem.thumbnail((LADO_MAXIMO_PX, LADO_MAXIMO_PX), Image.Resampling.LANCZOS)

        buffer = io.BytesIO()
        imagem.save(buffer, format="JPEG", quality=85, optimize=True)

        caminho = f"perfis/{perfil.usuario_id}/foto.jpg"
        url = await self.armazenamento.salvar(caminho, buffer.getvalue(), "image/jpeg")
        perfil.foto_url = url
        await self.sessao.flush()
        log.info("perfil.foto_salva", profissional_id=str(perfil.usuario_id))
        return url

    # --- Especialidades -----------------------------------------------------

    async def listar_catalogo(self) -> list[Especialidade]:
        return list(
            (
                await self.sessao.execute(
                    select(Especialidade)
                    .where(Especialidade.ativo.is_(True))
                    .order_by(Especialidade.categoria, Especialidade.ordem_exibicao)
                )
            )
            .scalars()
            .all()
        )

    async def definir_especialidades(
        self,
        perfil: PerfilProfissional,
        escolhas: list[tuple[uuid.UUID, int, int, int]],
    ) -> list[ProfissionalEspecialidade]:
        """Substitui as especialidades do profissional.

        ``escolhas`` = ``[(especialidade_id, preco_min, preco_padrao, preco_max)]``
        em centavos, na ordem de prioridade.

        O limite de 5 é checado aqui para dar mensagem decente, mas quem
        realmente garante é o banco (``CHECK ordem 1..5`` + ``UNIQUE``): mesmo um
        bug futuro nesta função não consegue gravar a sexta.
        """
        maximo = await self.parametros.max_especialidades()
        if len(escolhas) > min(maximo, LIMITE_ESPECIALIDADES):
            raise MaximoEspecialidadesAtingido(
                f"Escolha no máximo {maximo} especialidades — são aquelas em que "
                "você tem mais domínio.",
                campo="especialidades",
            )
        if not escolhas:
            raise ErroDominio("Escolha ao menos uma especialidade.", campo="especialidades")

        for _, minimo, padrao, maximo_preco in escolhas:
            if not (0 < minimo <= padrao <= maximo_preco):
                raise PrecoInvalido(campo="preco")

        await self.sessao.execute(
            delete(ProfissionalEspecialidade).where(
                ProfissionalEspecialidade.profissional_id == perfil.usuario_id
            )
        )
        await self.sessao.flush()

        novas = [
            ProfissionalEspecialidade(
                profissional_id=perfil.usuario_id,
                especialidade_id=esp_id,
                ordem=ordem,
                preco_min_centavos=minimo,
                preco_padrao_centavos=padrao,
                preco_max_centavos=maximo_preco,
            )
            for ordem, (esp_id, minimo, padrao, maximo_preco) in enumerate(escolhas, start=1)
        ]
        self.sessao.add_all(novas)
        await self.sessao.flush()
        log.info(
            "perfil.especialidades_definidas",
            profissional_id=str(perfil.usuario_id),
            quantidade=len(novas),
        )
        return novas

    async def buscar_perfil(self, profissional_id: uuid.UUID) -> PerfilProfissional:
        perfil = await self.sessao.get(PerfilProfissional, profissional_id)
        if perfil is None:
            raise NaoEncontrado("Profissional não encontrado.")
        return perfil
