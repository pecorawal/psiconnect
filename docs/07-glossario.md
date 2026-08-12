# Glossário

O código é escrito em português no domínio. Este é o vocabulário canônico —
usá-lo consistentemente evita que a mesma coisa apareça com três nomes.

## Domínio

| Termo | Significado |
|---|---|
| **Agendamento** | Uma consulta marcada. É um **fato**, imutável em sua essência. Não confundir com disponibilidade |
| **Disponibilidade** (`DisponibilidadeRecorrente`) | A **regra** de quando o profissional atende ("toda terça, 14h–18h"). Não é uma consulta |
| **Slot** | Um horário livre, **derivado** da disponibilidade menos bloqueios e ocupados. Nunca é persistido |
| **Bloqueio** (`BloqueioAgenda`) | Exceção à disponibilidade: férias, feriado, "essa quinta não" |
| **Sessão** (`Sessao`) | O atendimento em si — a sala de vídeo e seu ciclo de vida. **Não** é sessão de login (`SessaoLogin`) |
| **Lobby** | Sala de espera virtual onde o paciente aguarda o profissional admiti-lo |
| **Crédito** (`CreditoSessao`) | Direito a uma sessão, comprado via plano. Uma linha por crédito, não um contador |
| **Plano** | Avulso, pacote de 5 ou pacote de 10 sessões |
| **Repartição** | A divisão do valor pago entre taxa do gateway, comissão, impostos e líquido do profissional |
| **Comissão** | O percentual retido pela plataforma. Default 5%, parametrizável |
| **Sintoma** | O que o paciente sente, em linguagem leiga ("não consigo dormir") |
| **Especialidade** | A área de atuação do profissional, em termos técnicos ("insônia", "TCC") |
| **Matching** | O ranqueamento de profissionais a partir dos sintomas, via `SintomaEspecialidade.peso` |
| **Pontualidade** | Entrada do profissional na sala dentro da janela; gera pontos |
| **No-show** | Ausência além da tolerância de 15 minutos |

## Regulatório

| Termo | Significado |
|---|---|
| **CRP** | Conselho Regional de Psicologia — registro obrigatório do psicólogo |
| **CREFITO** | Conselho de Fisioterapia e Terapia Ocupacional |
| **CFP** | Conselho Federal de Psicologia |
| **e-Psi** | Cadastro para atendimento online, **descontinuado** pela Res. CFP 9/2024 |
| **LGPD** | Lei 13.709/2018. Lei brasileira aplicável — baseada no GDPR, não na HIPAA |
| **Dado sensível** | Categoria da LGPD (art. 11) que inclui saúde. Exige base legal específica |
| **Titular** | A pessoa a quem os dados se referem (art. 18 lista seus direitos) |
| **Encarregado / DPO** | Responsável pelo canal de comunicação com titulares e com a ANPD |
| **RIPD** | Relatório de Impacto à Proteção de Dados Pessoais |
| **Prontuário** | Registro documental do atendimento. Obrigação **do profissional**, não da plataforma |

## Técnico

| Termo | Significado |
|---|---|
| **Porta / Provider** | `Protocol` que abstrai um serviço externo, com implementação fake e real |
| **Outbox** | Padrão em que a notificação vira uma linha no banco e um worker a envia depois |
| **Advisory lock** | Trava do Postgres usada para restrições **agregadas** que `EXCLUDE` não cobre |
| **EXCLUDE constraint** | Constraint do Postgres que impede sobreposição de ranges. Exige `btree_gist` |
| **Fragmento / partial** | Pedaço de HTML devolvido a uma requisição HTMX, em vez da página inteira |
| **Walking skeleton** | Fatia end-to-end mais fina que funciona, usada como estratégia da Fase 1 |
| **Centavos** | Toda quantia monetária é `int` em centavos. `float` é proibido para dinheiro |
