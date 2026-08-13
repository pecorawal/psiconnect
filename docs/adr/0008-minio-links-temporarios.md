# ADR 0008 — MinIO para arquivos, com estratégia por sensibilidade

- **Status:** aceita
- **Data:** 2026-08-13

## Contexto

A plataforma passou a receber dois tipos de arquivo:

- **foto de perfil** do profissional — exibida a qualquer paciente que procure
  atendimento;
- **cópia de documento de identificação** do responsável legal — exigida pelo
  art. 14 §5 da LGPD para verificar que o consentimento partiu de quem tem
  legitimidade ([ADR 0010](0010-cadastro-de-menores.md)).

A primeira versão guardava o documento como `BYTEA` no Postgres. Isso é ruim por
dois motivos: um arquivo de 8 MB numa coluna infla dump, backup e replicação; e
qualquer consulta descuidada carrega o blob para a memória.

O pedido foi explícito: **object storage com links temporários, e só o
administrador e a própria pessoa podem ver os documentos**.

## A tensão que precisou ser resolvida

**Link temporário e criptografia pela aplicação se excluem.** Se o app cifra o
arquivo antes de subir, o link assinado entrega bytes ilegíveis — o navegador
não renderiza nada. Se o app não cifra e confia na criptografia do servidor de
storage, o link funciona, mas quem opera o storage consegue abrir o arquivo.

Não há escolha única correta: depende do que o arquivo é.

## Decisão

**MinIO** (S3-compatível) com bucket **privado**, e duas estratégias:

| Arquivo | Como | Por quê |
|---|---|---|
| Foto de perfil | link **presigned de 60 s**, via `/midia/foto/{id}` | precisa renderizar num `<img>`; o dano de um link vazado é pequeno |
| Documento de identidade | **cifrado no app** (AES-256-GCM) e servido por rota que decifra e faz stream | um link, por mais curto que seja o TTL, pode ser encaminhado, ficar no histórico ou cair num log de proxy |

Em ambos os casos **a autorização é da aplicação**, nunca do link. O storage não
é a fronteira de acesso.

`salvar()` devolve a **chave** do objeto, não uma URL — quem decide se alguém
pode ver o arquivo é o app, no momento do acesso, não no momento do upload. Por
isso `PerfilProfissional.foto_url` virou `foto_chave`, e a URL estável
`/midia/foto/{id}` redireciona para um link assinado novo a cada acesso.

## Quem pode ver um documento

Quatro regras, nesta ordem, em `app/web/rotas/midia.py`:

1. o próprio responsável, quando tem conta;
2. o responsável identificado pelo e-mail com que foi convidado;
3. o adolescente sobre quem é o cadastro — é documento a respeito dele;
4. administrador com permissão de leitura no módulo `documentos`.

Quem não se encaixa recebe **404, não 403**: um 403 confirmaria que o documento
existe.

O acesso é gravado em `AcessoDocumento` **antes** de o conteúdo sair — se o
registro falhar, o documento não sai. Controle de acesso sem trilha é promessa
sem prova.

## Verificado contra o MinIO real

| Verificação | Resultado |
|---|---|
| Link presigned funciona | HTTP 200 |
| Acesso direto sem assinatura | **HTTP 403** |
| Presigned de documento cifrado entrega texto legível | **não** |
| Aplicação decifra corretamente | sim |
| Blob movido para outro contexto (AAD trocado) | **rejeitado** |

O AAD é `verificacao_responsavel:{id}`: mover o blob de uma linha para outra faz
a decifragem falhar, em vez de mostrar o documento de outra pessoa.

## Consequências

- `boto3` é síncrono; as chamadas vão para `asyncio.to_thread`. Upload de foto e
  de documento são raros e não justificam mais uma dependência async.
- MinIO não implementa `PutPublicAccessBlock` (API da AWS). Lá o bucket já nasce
  privado — o 403 acima comprova —, então a chamada é informativa, não falha.
- `LocalStorageProvider` continua existindo para desenvolvimento sem MinIO, e
  cumpre o mesmo contrato, inclusive "links temporários" (assinados pela própria
  aplicação). Não é equivalente, mas mantém as rotas idênticas nos dois
  ambientes.
- Configuração incompleta **impede a aplicação de subir**, com mensagem que diz
  qual variável falta. Antes, o erro só aparecia no primeiro upload, como
  `InvalidAccessKeyId` vindo do boto3.
- **A imagem do documento é apagada após a verificação.** Permanece o registro de
  que houve, com tipo e últimos dígitos. Cumprida a função de provar o
  consentimento, manter a cópia só aumenta o estrago de um eventual vazamento.

## Porta 9010

O `docker-compose` expõe o MinIO em **9010/9011**, não no padrão 9000/9001, que
costuma já estar ocupado por outro projeto na mesma máquina.
