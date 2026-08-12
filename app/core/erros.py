"""Erros de domínio.

A camada de serviço nunca conhece HTTP. Ela levanta ``ErroDominio``, que carrega
um ``codigo`` estável (para testes e para a API) e uma ``mensagem_usuario`` em
português pronta para exibir. Quem traduz isso em status HTTP, fragmento HTMX ou
JSON é o *exception handler* registrado em ``app.main``.
"""

from __future__ import annotations

from typing import Any


class ErroDominio(Exception):
    """Base de todas as violações de regra de negócio.

    ``campo`` permite que a camada web destaque o input correspondente no
    formulário em vez de mostrar só um alerta genérico.
    """

    codigo: str = "erro_dominio"
    status_http: int = 422
    mensagem_padrao: str = "Não foi possível concluir a operação."

    def __init__(
        self,
        mensagem: str | None = None,
        *,
        campo: str | None = None,
        detalhes: dict[str, Any] | None = None,
    ) -> None:
        self.mensagem_usuario = mensagem or self.mensagem_padrao
        self.campo = campo
        self.detalhes = detalhes or {}
        super().__init__(self.mensagem_usuario)

    def as_dict(self) -> dict[str, Any]:
        return {
            "codigo": self.codigo,
            "mensagem": self.mensagem_usuario,
            "campo": self.campo,
            "detalhes": self.detalhes,
        }


# --- Acesso ----------------------------------------------------------------


class NaoAutenticado(ErroDominio):
    codigo = "nao_autenticado"
    status_http = 401
    mensagem_padrao = "Você precisa entrar para acessar esta página."


class NaoAutorizado(ErroDominio):
    codigo = "nao_autorizado"
    status_http = 403
    mensagem_padrao = "Você não tem permissão para esta ação."


class NaoEncontrado(ErroDominio):
    codigo = "nao_encontrado"
    status_http = 404
    mensagem_padrao = "Registro não encontrado."


class CredenciaisInvalidas(ErroDominio):
    codigo = "credenciais_invalidas"
    status_http = 401
    # Mensagem deliberadamente genérica: não revela se o e-mail existe.
    mensagem_padrao = "E-mail ou senha incorretos."


class EmailJaCadastrado(ErroDominio):
    codigo = "email_ja_cadastrado"
    status_http = 409
    mensagem_padrao = "Já existe uma conta com este e-mail."


# --- Perfil profissional ---------------------------------------------------


class MaximoEspecialidadesAtingido(ErroDominio):
    codigo = "maximo_especialidades"
    mensagem_padrao = "Você já escolheu o número máximo de especialidades."


class DescricaoMuitoLonga(ErroDominio):
    codigo = "descricao_muito_longa"
    mensagem_padrao = "A descrição deve ter no máximo 500 caracteres."


class CadastroIncompleto(ErroDominio):
    codigo = "cadastro_incompleto"
    mensagem_padrao = "Complete seu cadastro antes de continuar."


# --- Agenda ----------------------------------------------------------------


class JanelaInvalida(ErroDominio):
    codigo = "janela_invalida"
    mensagem_padrao = "O horário final precisa ser depois do inicial."


class DisponibilidadeSobreposta(ErroDominio):
    codigo = "disponibilidade_sobreposta"
    mensagem_padrao = "Este horário se sobrepõe a outro já cadastrado na sua agenda."


class SlotIndisponivel(ErroDominio):
    codigo = "slot_indisponivel"
    status_http = 409
    mensagem_padrao = "Este horário não está mais disponível. Escolha outro."


class LimiteHorasDiaExcedido(ErroDominio):
    codigo = "limite_horas_dia"
    mensagem_padrao = "Este profissional já atingiu o limite de horas de atendimento neste dia."


class LimiteSessoesSemanaExcedido(ErroDominio):
    codigo = "limite_sessoes_semana"
    mensagem_padrao = "Você atingiu o limite de sessões para esta semana."


class AgendamentoNoPassado(ErroDominio):
    codigo = "agendamento_no_passado"
    mensagem_padrao = "Não é possível agendar em um horário que já passou."


class AntecedenciaInsuficiente(ErroDominio):
    codigo = "antecedencia_insuficiente"
    mensagem_padrao = "Este horário está muito próximo. Escolha um com mais antecedência."


# --- Pagamento -------------------------------------------------------------


class PagamentoRecusado(ErroDominio):
    codigo = "pagamento_recusado"
    mensagem_padrao = "O pagamento não foi aprovado. Verifique os dados e tente novamente."


class SemCreditoDisponivel(ErroDominio):
    codigo = "sem_credito"
    mensagem_padrao = "Você não tem sessões disponíveis. Escolha um plano para continuar."


class ReservaExpirada(ErroDominio):
    codigo = "reserva_expirada"
    status_http = 410
    mensagem_padrao = "O tempo para concluir o pagamento acabou e o horário foi liberado."


# --- Sessão ----------------------------------------------------------------


class SessaoNaoDisponivel(ErroDominio):
    codigo = "sessao_nao_disponivel"
    mensagem_padrao = "A sala ainda não está aberta."


class ConsentimentoNecessario(ErroDominio):
    codigo = "consentimento_necessario"
    mensagem_padrao = "É preciso confirmar que você está ciente antes de entrar na sessão."


class AvaliacaoPendente(ErroDominio):
    codigo = "avaliacao_pendente"
    mensagem_padrao = "Avalie sua última sessão antes de agendar uma nova."


# --- Infra -----------------------------------------------------------------


class ProviderIndisponivel(ErroDominio):
    codigo = "provider_indisponivel"
    status_http = 503
    mensagem_padrao = "Serviço temporariamente indisponível. Tente novamente em instantes."
