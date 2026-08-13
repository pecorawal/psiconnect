"""Enums do domínio.

Todos são ``StrEnum``: o valor persistido é legível em ``psql``, o que importa
muito quando se investiga um agendamento em produção. Os tipos ENUM nativos do
Postgres são criados nas migrations.
"""

from __future__ import annotations

from enum import StrEnum


class Papel(StrEnum):
    PACIENTE = "PACIENTE"
    PROFISSIONAL = "PROFISSIONAL"
    ADMIN = "ADMIN"


class Conselho(StrEnum):
    CRP = "CRP"
    CREFITO = "CREFITO"


class StatusCadastro(StrEnum):
    RASCUNHO = "RASCUNHO"
    EM_ANALISE = "EM_ANALISE"
    APROVADO = "APROVADO"
    REJEITADO = "REJEITADO"
    SUSPENSO = "SUSPENSO"


class StatusPaciente(StrEnum):
    ATIVO = "ATIVO"
    #: Menor de 18 aguardando o responsável confirmar (LGPD art. 14).
    #: Nenhum atendimento acontece nesse estado.
    PENDENTE_RESPONSAVEL = "PENDENTE_RESPONSAVEL"
    SUSPENSO = "SUSPENSO"


class TipoDocumentoResponsavel(StrEnum):
    RG = "RG"
    CNH = "CNH"
    PASSAPORTE = "PASSAPORTE"
    CERTIDAO_NASCIMENTO = "CERTIDAO_NASCIMENTO"
    OUTRO = "OUTRO"


class StatusAgendamento(StrEnum):
    # PENDENTE_PAGAMENTO participa da constraint EXCLUDE de propósito: o fluxo é
    # sintomas -> horário -> pagamento, então o slot fica travado no checkout.
    PENDENTE_PAGAMENTO = "PENDENTE_PAGAMENTO"
    CONFIRMADO = "CONFIRMADO"
    EM_ANDAMENTO = "EM_ANDAMENTO"
    REALIZADO = "REALIZADO"
    CANCELADO_PACIENTE = "CANCELADO_PACIENTE"
    CANCELADO_PROFISSIONAL = "CANCELADO_PROFISSIONAL"
    NO_SHOW_PACIENTE = "NO_SHOW_PACIENTE"
    NO_SHOW_PROFISSIONAL = "NO_SHOW_PROFISSIONAL"
    EXPIRADO = "EXPIRADO"


#: Estados que ocupam o horário na agenda. Usado pela constraint EXCLUDE e por
#: toda contagem de limite -- manter os dois lados em sincronia.
STATUS_OCUPAM_AGENDA = (
    StatusAgendamento.PENDENTE_PAGAMENTO,
    StatusAgendamento.CONFIRMADO,
    StatusAgendamento.EM_ANDAMENTO,
)


class StatusSessao(StrEnum):
    AGENDADA = "AGENDADA"
    SALA_PRONTA = "SALA_PRONTA"
    LOBBY = "LOBBY"
    EM_ANDAMENTO = "EM_ANDAMENTO"
    ENCERRADA = "ENCERRADA"
    EXPIRADA = "EXPIRADA"
    CANCELADA = "CANCELADA"


class TipoEventoSessao(StrEnum):
    SALA_CRIADA = "SALA_CRIADA"
    LINK_ENVIADO = "LINK_ENVIADO"
    DISCLAIMER_ACEITO = "DISCLAIMER_ACEITO"
    ENTROU_LOBBY = "ENTROU_LOBBY"
    ADMITIDO = "ADMITIDO"
    ENTROU_SALA = "ENTROU_SALA"
    SAIU = "SAIU"
    ENCERRADA = "ENCERRADA"


class SlugPlano(StrEnum):
    AVULSO = "AVULSO"
    PACOTE_5 = "PACOTE_5"
    PACOTE_10 = "PACOTE_10"


class StatusCompra(StrEnum):
    PENDENTE = "PENDENTE"
    ATIVA = "ATIVA"
    CANCELADA = "CANCELADA"
    EXPIRADA = "EXPIRADA"
    CONCLUIDA = "CONCLUIDA"


class StatusCredito(StrEnum):
    DISPONIVEL = "DISPONIVEL"
    RESERVADO = "RESERVADO"
    CONSUMIDO = "CONSUMIDO"
    EXPIRADO = "EXPIRADO"
    ESTORNADO = "ESTORNADO"


class MetodoPagamento(StrEnum):
    PIX = "PIX"
    CARTAO_CREDITO = "CARTAO_CREDITO"
    CARTAO_DEBITO = "CARTAO_DEBITO"


class StatusPagamento(StrEnum):
    CRIADO = "CRIADO"
    PENDENTE = "PENDENTE"
    APROVADO = "APROVADO"
    RECUSADO = "RECUSADO"
    ESTORNADO = "ESTORNADO"
    CANCELADO = "CANCELADO"
    EXPIRADO = "EXPIRADO"


class TipoTermo(StrEnum):
    TERMOS_USO = "TERMOS_USO"
    POLITICA_PRIVACIDADE = "POLITICA_PRIVACIDADE"
    CONTRATO_PROFISSIONAL = "CONTRATO_PROFISSIONAL"
    CONSENT_DADOS_SAUDE = "CONSENT_DADOS_SAUDE"
    CONSENT_TRANSCRICAO = "CONSENT_TRANSCRICAO"
    #: Consentimento do responsável legal por menor de 18 (LGPD art. 14).
    CONSENT_RESPONSAVEL = "CONSENT_RESPONSAVEL"


class CanalNotificacao(StrEnum):
    EMAIL = "EMAIL"
    WHATSAPP = "WHATSAPP"
    IN_APP = "IN_APP"
    PUSH = "PUSH"


class StatusNotificacao(StrEnum):
    PENDENTE = "PENDENTE"
    ENVIANDO = "ENVIANDO"
    ENVIADA = "ENVIADA"
    FALHA = "FALHA"
    CANCELADA = "CANCELADA"


class TipoPontuacao(StrEnum):
    PONTUALIDADE_PROFISSIONAL = "PONTUALIDADE_PROFISSIONAL"
    SESSAO_REALIZADA = "SESSAO_REALIZADA"
    AVALIACAO_RESPONDIDA = "AVALIACAO_RESPONDIDA"
    PERFIL_COMPLETO = "PERFIL_COMPLETO"
    CANCELAMENTO_TARDIO = "CANCELAMENTO_TARDIO"
    NO_SHOW = "NO_SHOW"


class TipoToken(StrEnum):
    VERIFICACAO_EMAIL = "VERIFICACAO_EMAIL"
    RESET_SENHA = "RESET_SENHA"


class OrigemSessaoLogin(StrEnum):
    WEB = "WEB"
    APP = "APP"
