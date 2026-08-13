"""Termos versionados.

Os textos abaixo são **rascunhos de trabalho, não peças jurídicas**. Precisam de
revisão por advogado antes do go-live (ver docs/04-lgpd-e-compliance.md).

Mecanismo: cada texto é gravado com seu hash SHA-256. Ao aceitar, o usuário fica
ligado àquela versão exata. Mudar o texto exige nova versão -- a anterior é
encerrada com ``vigente_ate``, e os aceites antigos continuam apontando para o
que de fato foi lido.
"""

from __future__ import annotations

from typing import NamedTuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.seguranca import hash_conteudo
from app.core.tempo import agora_utc
from app.models import TermoVersionado, TipoTermo


class Def(NamedTuple):
    tipo: TipoTermo
    titulo: str
    conteudo: str


TERMOS_USO = """
# Termos de uso

O PsiConnect conecta pessoas que buscam atendimento psicológico a profissionais
com registro ativo em conselho profissional.

## O que a plataforma é
Um meio de encontro, agendamento e realização de sessões online. A relação
terapêutica é entre você e o profissional; a plataforma não interfere na conduta
clínica.

## O que a plataforma NÃO é
- **Não é serviço de emergência.** Em crise, ligue para o CVV (188). Em
  emergência médica, ligue 192.
- Não substitui atendimento presencial quando o profissional indicar.
- Não fornece diagnóstico nem prescrição por conta própria.

## Sua conta
Você é responsável por manter sua senha em sigilo e por não compartilhar seu
acesso. Avise-nos se suspeitar de uso indevido.

## Agendamento e cancelamento
Sessões podem ser remarcadas conforme a política vigente. O não comparecimento
pode consumir a sessão contratada.

## Encerramento
Você pode encerrar sua conta a qualquer momento. Alguns registros são mantidos
pelo prazo exigido por lei.
""".strip()

POLITICA_PRIVACIDADE = """
# Política de privacidade

Tratamos seus dados conforme a Lei Geral de Proteção de Dados (Lei 13.709/2018).

## Quais dados coletamos
- **Cadastro:** nome, e-mail, telefone, data de nascimento.
- **Dados de saúde:** os sintomas que você relata e o histórico das suas
  sessões. São dados **sensíveis** (art. 11) e recebem proteção reforçada.
- **Técnicos:** endereço IP, navegador e horários de acesso, para segurança e
  auditoria.

## O que NÃO guardamos
**Não gravamos o áudio nem o vídeo das suas sessões.** Quando a transcrição está
ativa e você consente, o áudio é processado e descartado; guardamos apenas o
texto, cifrado.

## Com quem compartilhamos
- Com o profissional que você escolher, no necessário ao atendimento.
- Com o processador de pagamentos, para cobrar.
- Com o provedor de videoconferência, para realizar a chamada.

Não vendemos seus dados nem os usamos para publicidade.

## Seus direitos (art. 18)
Confirmar o tratamento, acessar, corrigir, solicitar anonimização ou eliminação,
portar seus dados e revogar consentimento. Use a área "Meus dados" ou fale com
nosso encarregado.

## Por quanto tempo guardamos
Pelo tempo da relação e pelos prazos legais aplicáveis. Registros clínicos sob
responsabilidade do profissional seguem as normas do conselho.
""".strip()

CONSENT_DADOS_SAUDE = """
# Consentimento para tratamento de dados de saúde

Ao marcar esta opção, você autoriza expressamente o PsiConnect a tratar seus
**dados pessoais sensíveis de saúde** — os sintomas que informar e o histórico
de atendimentos — com a finalidade específica de:

1. sugerir profissionais com experiência no que você está sentindo;
2. permitir o agendamento e a realização das sessões;
3. manter o histórico necessário à continuidade do seu cuidado.

Este consentimento é **específico, informado e revogável**. Você pode retirá-lo a
qualquer momento em "Meus dados", sem prejuízo dos atendimentos já realizados.
A revogação impede novos tratamentos com base neste consentimento.
""".strip()

CONSENT_TRANSCRICAO = """
# Ciência sobre a transcrição da sessão

Esta sessão terá **transcrição automática** do que for falado, para auxiliar o
profissional no acompanhamento do seu caso.

**O que acontece:**
- O áudio é convertido em texto durante a sessão.
- O texto é armazenado **cifrado** e fica acessível a você e ao profissional.
- **O áudio e o vídeo não são gravados nem armazenados.**

Você pode solicitar a exclusão da transcrição a qualquer momento.

Ao marcar a caixa abaixo, você declara estar ciente e de acordo.
""".strip()

CONTRATO_PROFISSIONAL = """
# Contrato de prestação de serviços — profissional

## Objeto
O PsiConnect disponibiliza a infraestrutura de divulgação, agendamento,
pagamento e videochamada. O atendimento é prestado por você, de forma autônoma.

## Seus deveres
- Manter **registro ativo** no conselho profissional e informá-lo corretamente.
- Avaliar a adequação do atendimento a distância a cada caso, conforme a
  Resolução CFP nº 9/2024.
- Manter o **registro documental (prontuário)** dos atendimentos, conforme a
  Resolução CFP nº 001/2009. **A transcrição oferecida pela plataforma não
  constitui prontuário** e não substitui essa obrigação.
- Guardar sigilo profissional.

## Remuneração e custos
O valor da consulta é definido por você, dentro da faixa que cadastrar.
A plataforma retém um **percentual sobre cada atendimento concluído**, exibido no
seu painel antes de cada confirmação. Taxas do meio de pagamento e tributos
aplicáveis são discriminados no simulador de recebimento.

## Cancelamento
Qualquer das partes pode encerrar a relação a qualquer tempo, respeitados os
atendimentos já agendados.
""".strip()

CONSENT_RESPONSAVEL = """
# Consentimento do responsável legal

Você está autorizando o atendimento psicológico de um **adolescente sob sua
responsabilidade** na plataforma PsiConnect.

Ao confirmar, você declara que:

1. é o **pai, a mãe ou o responsável legal** pelo adolescente;
2. **autoriza** o tratamento dos dados pessoais dele, incluindo os **dados de
   saúde** necessários ao atendimento (Lei 13.709/2018, art. 14);
3. está ciente de que enviará **cópia de documento de identificação** para que a
   plataforma confirme que a autorização partiu de quem tem legitimidade.

## Sobre o documento que você envia

- É armazenado **criptografado**, e nem a equipe que opera a infraestrutura
  consegue abri-lo.
- Só **você** e um administrador autorizado podem visualizá-lo, e todo acesso
  fica registrado.
- Depois de conferido, **a imagem é apagada**. Permanece apenas o registro de que
  houve verificação, com o tipo do documento e os últimos dígitos.

## Sobre o sigilo do atendimento

O conteúdo das sessões é protegido por **sigilo profissional**. Autorizar o
atendimento **não** dá a você acesso ao que é falado em sessão. O profissional
compartilhará com você o que for necessário ao acompanhamento, conforme a ética
da profissão.

## Você pode voltar atrás

Este consentimento é **revogável a qualquer momento**, sem prejuízo dos
atendimentos já realizados.
""".strip()

TERMOS: tuple[Def, ...] = (
    Def(TipoTermo.TERMOS_USO, "Termos de uso", TERMOS_USO),
    Def(TipoTermo.POLITICA_PRIVACIDADE, "Política de privacidade", POLITICA_PRIVACIDADE),
    Def(TipoTermo.CONSENT_DADOS_SAUDE, "Consentimento — dados de saúde", CONSENT_DADOS_SAUDE),
    Def(TipoTermo.CONSENT_TRANSCRICAO, "Ciência — transcrição da sessão", CONSENT_TRANSCRICAO),
    Def(TipoTermo.CONTRATO_PROFISSIONAL, "Contrato do profissional", CONTRATO_PROFISSIONAL),
    Def(
        TipoTermo.CONSENT_RESPONSAVEL,
        "Consentimento do responsável legal",
        CONSENT_RESPONSAVEL,
    ),
)


async def semear_termos(sessao: AsyncSession) -> int:
    """Cria a versão 1 de cada termo que ainda não tenha uma versão vigente.

    Não sobrescreve nem versiona automaticamente: publicar uma nova versão é um
    ato deliberado, feito pelo admin.
    """
    vigentes = set(
        (
            await sessao.execute(
                select(TermoVersionado.tipo).where(TermoVersionado.vigente_ate.is_(None))
            )
        )
        .scalars()
        .all()
    )
    agora = agora_utc()
    novos = 0
    for d in TERMOS:
        if d.tipo in vigentes:
            continue
        sessao.add(
            TermoVersionado(
                tipo=d.tipo,
                versao=1,
                titulo=d.titulo,
                conteudo_md=d.conteudo,
                hash_sha256=hash_conteudo(d.conteudo),
                vigente_desde=agora,
            )
        )
        novos += 1
    await sessao.flush()
    return novos
