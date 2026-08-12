# ADR 0007 — `pwdlib` com Argon2id em vez de `passlib`

- **Status:** aceita
- **Data:** 2026-08-12

## Contexto

O `requirements.txt` do spike anterior pedia `passlib[bcrypt]==1.7.4`. Essa
combinação **não funciona hoje**: o backend bcrypt do passlib lê
`bcrypt.__about__.__version__`, atributo removido no `bcrypt` 4.1 e ausente no
5.x. O resultado vai de warning barulhento a falha de detecção do backend.

Além disso, o `passlib` 1.7.4 é de 2020 e o projeto está sem manutenção ativa —
uma dependência de segurança parada há anos.

## Decisão

**`pwdlib[argon2]`**, com `PasswordHash.recommended()` (Argon2id).

```python
# app/core/seguranca.py
from pwdlib import PasswordHash

hasher = PasswordHash.recommended()
```

## Justificativa

- É o que a própria documentação de segurança do FastAPI passou a recomendar no
  lugar do passlib.
- Argon2id é *memory-hard*: encarece ataque com GPU/ASIC muito mais que bcrypt.
- `pwdlib` traz `verify_and_update`, ou seja, **rehash transparente** — mudar
  parâmetros de custo no futuro não exige migração manual nem forçar troca de
  senha.

## Alternativa aceitável

Se houver aversão a Argon2 (por consumo de memória em container pequeno), usar
`bcrypt` diretamente (`bcrypt.hashpw` / `bcrypt.checkpw`), sem passlib no meio.
O que **não** se faz é voltar ao passlib.

## Consequência operacional

Argon2id consome memória por verificação de senha por design. Em container com
limite baixo, ajustar os parâmetros conscientemente — e nunca reduzi-los abaixo
das recomendações da OWASP sem registrar o porquê.
