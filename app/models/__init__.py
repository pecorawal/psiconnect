"""Modelos do domínio.

Importar tudo aqui é o que popula ``Base.metadata`` -- o Alembic depende disso
para o ``--autogenerate`` enxergar as tabelas.
"""

from app.db.base import Base
from app.models.agenda import Agendamento, BloqueioAgenda, DisponibilidadeRecorrente
from app.models.enums import (
    STATUS_OCUPAM_AGENDA,
    CanalNotificacao,
    Conselho,
    MetodoPagamento,
    OrigemSessaoLogin,
    Papel,
    SlugPlano,
    StatusAgendamento,
    StatusCadastro,
    StatusCompra,
    StatusCredito,
    StatusNotificacao,
    StatusPagamento,
    StatusSessao,
    TipoEventoSessao,
    TipoPontuacao,
    TipoTermo,
    TipoToken,
)
from app.models.lgpd import AceiteTermo, LogAuditoria, TermoVersionado
from app.models.operacional import Avaliacao, EventoPontuacao, Notificacao
from app.models.pagamento import CompraPlano, CreditoSessao, Pagamento, Plano
from app.models.parametro import ChaveParametro, ParametroSistema
from app.models.perfil import (
    LIMITE_DESCRICAO,
    LIMITE_ESPECIALIDADES,
    PerfilPaciente,
    PerfilProfissional,
    ProfissionalEspecialidade,
)
from app.models.sessao import EventoSessao, Sessao
from app.models.taxonomia import (
    Especialidade,
    PacienteSintoma,
    Sintoma,
    SintomaEspecialidade,
)
from app.models.usuario import SessaoLogin, TokenVerificacao, Usuario

__all__ = [
    "LIMITE_DESCRICAO",
    "LIMITE_ESPECIALIDADES",
    "STATUS_OCUPAM_AGENDA",
    "AceiteTermo",
    "Agendamento",
    "Avaliacao",
    "Base",
    "BloqueioAgenda",
    "CanalNotificacao",
    "ChaveParametro",
    "CompraPlano",
    "Conselho",
    "CreditoSessao",
    "DisponibilidadeRecorrente",
    "Especialidade",
    "EventoPontuacao",
    "EventoSessao",
    "LogAuditoria",
    "MetodoPagamento",
    "Notificacao",
    "OrigemSessaoLogin",
    "PacienteSintoma",
    "Pagamento",
    "Papel",
    "ParametroSistema",
    "PerfilPaciente",
    "PerfilProfissional",
    "Plano",
    "ProfissionalEspecialidade",
    "Sessao",
    "SessaoLogin",
    "Sintoma",
    "SintomaEspecialidade",
    "SlugPlano",
    "StatusAgendamento",
    "StatusCadastro",
    "StatusCompra",
    "StatusCredito",
    "StatusNotificacao",
    "StatusPagamento",
    "StatusSessao",
    "TermoVersionado",
    "TipoEventoSessao",
    "TipoPontuacao",
    "TipoTermo",
    "TipoToken",
    "TokenVerificacao",
    "Usuario",
]
