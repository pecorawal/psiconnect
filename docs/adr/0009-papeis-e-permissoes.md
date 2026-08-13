# ADR 0009 — Papéis e permissões no esquema do Pectec Nexos

- **Status:** aceita
- **Data:** 2026-08-13

## Contexto

A autorização inicial era um enum fixo (`PACIENTE`, `PROFISSIONAL`, `ADMIN`)
verificado rota a rota com `requer_papel(...)`. Funciona enquanto só existe um
tipo de administrador — e deixa de funcionar no primeiro pedido de "o suporte
precisa remarcar consulta, mas não pode ver documento".

Foi pedido adotar o esquema de gestão de usuários já usado no **Pectec Nexos**,
outro sistema da casa.

## O que foi adotado

- **`Role` + `RolePermission`**: o admin cria papéis e marca, por módulo, o que
  aquele papel pode fazer (`pode_criar`, `pode_ler`, `pode_atualizar`,
  `pode_deletar`).
- **`MODULOS_SISTEMA`**: lista central do que é protegível.
- **Política central por prefixo**: `PREFIXO_PARA_MODULO` mapeia rota → módulo, e
  o método HTTP define a operação. Não há decorador por endpoint; a política
  inteira se lê num arquivo só.
- **Campos reservados** de 2FA e OpenID no `Usuario`, para que habilitá-los
  depois não exija migração disruptiva.

## O que NÃO foi adotado, e por quê

O Pectec Nexos autentica com **JWT no header, guardado em `localStorage`**. Isso
não veio, e a diferença é deliberada:

- O PsiConnect é **server-rendered com HTMX** e trata **dado sensível de saúde**.
  JWT em `localStorage` é legível por qualquer XSS; o cookie `HttpOnly` é
  invisível ao JS ([ADR 0002](0002-sessao-cookie-vs-jwt.md)).
- **Revogação imediata** é requisito aqui: suspender um profissional, encerrar a
  sessão de um aparelho perdido, atender pedido de exclusão da LGPD. JWT
  stateless só revoga com blocklist — ou seja, reinventando o session store.

**Modelo de permissão e transporte de token são decisões independentes.** Trazer
o primeiro não obriga a trazer o segundo.

## Duas adaptações

### 1. Dependência do FastAPI, não middleware do Starlette

O middleware original roda fora da injeção de dependência, então precisa abrir a
própria sessão de banco e validar o token na mão. Aqui a checagem é uma
dependência (`StaffAutorizado`), que reaproveita a sessão da requisição e o
usuário já resolvido — menos código e uma conexão a menos por requisição.

### 2. Negar por omissão

No original, rota não mapeada exige apenas login. Aqui, **tudo sob `/admin`
exige permissão explícita**, e rota administrativa sem módulo mapeado é negada
com log de erro. Numa plataforma de saúde, um endpoint administrativo novo não
pode nascer aberto porque alguém esqueceu de mapeá-lo.

O mesmo vale dentro do papel: **ausência de linha em `RolePermission` significa
"não pode"**. Um módulo novo não nasce liberado para todos os papéis existentes.

## `Papel` e `Role` são coisas diferentes

Misturá-las causaria confusão, então a separação é explícita:

| | O que é | Quem tem |
|---|---|---|
| **`Papel`** | o **tipo de conta** — define qual perfil existe e qual fluxo a pessoa percorre | todos |
| **`Role`** | um **conjunto de permissões administrativas** | só quem opera a plataforma |

`Usuario.role_id` é **nulo** para paciente e profissional: as regras deles são de
domínio ("só vejo o que é meu"), não CRUD por módulo. Um paciente não vira
profissional por ganhar uma permissão.

Ser `ADMIN` dá acesso à *área*; a `Role` diz o que se pode fazer *nela*. Um admin
recém-criado sem papel atribuído não acessa nada — e isso é intencional.

## O verbo HTTP passa a ter significado

Como o método define a operação, **editar precisa ser `PATCH`/`PUT`, não `POST`**:
um formulário de edição enviado por POST exigiria permissão de *criar*, e um
papel com direito apenas de *editar* levaria 403. Formulário HTML só fala GET e
POST, então as edições usam `hx-patch`.

Isso foi descoberto por um teste, não em produção — é um efeito colateral bom de
centralizar a política: usar o verbo errado deixa de ser inofensivo.

## Salvaguardas contra se trancar do lado de fora

1. Papel **de sistema** não pode ser alterado nem excluído.
2. Ninguém remove o **próprio** papel administrativo.
3. Não se remove o papel nem se desativa a **última** conta capaz de gerir
   usuários.
4. **Desativar é preferível a excluir** — e desativar revoga todas as sessões
   abertas na hora, senão a pessoa continuaria trabalhando até o cookie vencer.

## Papéis semeados

| Papel | Alcance | O que **não** alcança |
|---|---|---|
| Administrador | tudo | — |
| Suporte | contas, agendamentos, taxonomias | documentos, financeiro, configurações |
| Financeiro | pagamentos, repasses, notas | contas de paciente, documentos |

O corte comum: **ninguém que não precise vê documento de identificação**. E
nenhum papel lê conteúdo clínico — transcrição de sessão não é sequer um módulo,
porque não deve existir tela administrativa para isso.
