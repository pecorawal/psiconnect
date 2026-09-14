# Documentação do PsiConnect

| Documento | Conteúdo |
|---|---|
| [00-visao-original.md](00-visao-original.md) | O `funcionalidades.md` original do `efatafy`, preservado sem edição |
| [01-visao-produto.md](01-visao-produto.md) | O que é o produto, para quem, e o fluxo de cada persona |
| [02-modelo-dominio.md](02-modelo-dominio.md) | Entidades, relacionamentos e as decisões de modelagem |
| [03-regras-negocio.md](03-regras-negocio.md) | R1–R11: cada regra, onde vive e como é garantida |
| [04-lgpd-e-compliance.md](04-lgpd-e-compliance.md) | LGPD, resoluções do CFP, e o que ainda precisa de parecer jurídico |
| [05-roadmap.md](05-roadmap.md) | Fases 0–7 com escopo e critério de pronto |
| [06-integracoes.md](06-integracoes.md) | Mercado Pago, daily.co, WhatsApp, transcrição, NFS-e |
| [07-glossario.md](07-glossario.md) | Vocabulário do domínio |

## Decisões de arquitetura (ADR)

| ADR | Decisão |
|---|---|
| [0001](adr/0001-stack-fastapi-htmx.md) | FastAPI + Jinja2 + HTMX em vez de SPA |
| [0002](adr/0002-sessao-cookie-vs-jwt.md) | Cookie de sessão httpOnly em vez de JWT |
| [0003](adr/0003-sem-gravacao-apenas-transcricao.md) | Só transcrição; mídia bruta descartada |
| [0004](adr/0004-mercado-pago-split.md) | Mercado Pago com split marketplace |
| [0005](adr/0005-comissao-parametrizavel.md) | Comissão parametrizável, default 12% |
| [0006](adr/0006-disponibilidade-vs-agendamento.md) | Disponibilidade (regra) ≠ Agendamento (fato) |
| [0007](adr/0007-pwdlib-argon2.md) | `pwdlib`/Argon2id em vez de `passlib` |
| [0008](adr/0008-minio-links-temporarios.md) | MinIO com estratégia por sensibilidade do arquivo |
| [0009](adr/0009-papeis-e-permissoes.md) | Papéis e permissões no esquema do Pectec Nexos |
| [0010](adr/0010-cadastro-de-menores.md) | Adolescentes com consentimento do responsável |
| [0011](adr/0011-sse-no-lobby.md) | SSE na sala de espera, polling como plano B |
| [0012](adr/0012-pwa-sem-cache-de-dado-clinico.md) | PWA que instala, mas não cacheia dado clínico |

## Assets

- [`assets/sistema-atendimento-agendamento.png`](assets/sistema-atendimento-agendamento.png) — mapa mental original
- [`assets/mindmap.txt`](assets/mindmap.txt) — export textual dos ramos do mapa mental
