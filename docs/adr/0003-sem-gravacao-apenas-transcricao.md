# ADR 0003 — Só transcrição; áudio e vídeo brutos são descartados

- **Status:** aceita
- **Data:** 2026-08-12

## Contexto

O `funcionalidades.md` original diz que a sessão "será gravada, criptografada e
com transcrição do conteúdo falado entre paciente e profissional", e que a
transcrição deve ir para um banco vetorial "para que seja aproveitada
futuramente com inteligência artificial".

Gravar áudio e vídeo de psicoterapia é o maior risco isolado do projeto: é dado
pessoal **sensível** de saúde (LGPD art. 11), de altíssimo impacto em caso de
vazamento, e cujo armazenamento cria obrigações de criptografia em repouso,
política de retenção, trilha de auditoria e resposta a incidente.

## Decisão

**Processar o áudio em memória, gerar a transcrição e o embedding, e descartar a
mídia bruta.** Nenhum arquivo de áudio ou vídeo é persistido.

- `TrechoTranscricao.texto_cifrado` guarda o texto com AES-256-GCM, com
  AAD = `transcricao_id|ordem` (impede trocar trechos entre sessões).
- `TrechoTranscricao.embedding` é um `Vector(1536)` com índice HNSW.
- O consentimento é específico e destacado, por sessão, registrado em
  `AceiteTermo` com o hash SHA-256 do texto exato que a pessoa leu.

## Justificativa

- Atende ao objetivo declarado ("auxiliar o psicólogo ao final da sessão" e uso
  futuro de IA) com uma fração do risco e do custo de storage.
- Reduz drasticamente a superfície de um eventual incidente: um vazamento expõe
  texto de sessões, não a voz e o rosto dos pacientes.

## Consequências e pontos em aberto

- **A transcrição não é prontuário.** A Res. CFP 001/2009 mantém o registro
  documental como obrigação do **profissional**, com guarda mínima de 5 anos. Se
  o produto decidir oferecer prontuário eletrônico, isso muda retenção,
  exportação e responsabilidade — e exige nova ADR.
- **O vetor também é dado sensível.** O embedding deriva de conteúdo clínico e
  há literatura sobre inversão de embeddings; ele recebe o mesmo controle de
  acesso do texto. Admin **não** vê transcrição, só metadado.
- **Transferência internacional.** Enviar áudio de psicoterapia para uma API nos
  EUA é uma questão de LGPD art. 33 por si só. Recomendação: transcrever
  localmente (`faster-whisper`) por padrão; a API é opt-in explícito.
- `EMBEDDING_DIM=1536` está fixado na migration. Modelos locais multilíngues
  bons (e5-large, BGE-M3) produzem 1024 dimensões e **não cabem** na coluna. Se
  a opção for embedding local, mudar a dimensão **antes da Fase 5** — depois
  custa re-embedding de tudo.
