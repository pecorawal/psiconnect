"""Primitivas de segurança: senha, tokens e CSRF.

Ver ADR 0002 (sessão) e ADR 0007 (hash de senha).
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

from pwdlib import PasswordHash

#: Argon2id com os parâmetros recomendados. NÃO usar passlib: o passlib 1.7.4
#: lê `bcrypt.__about__`, atributo removido no bcrypt 4.1 -- é exatamente a
#: combinação que o spike anterior pedia e que não funciona hoje.
_hasher = PasswordHash.recommended()

TAMANHO_MINIMO_SENHA = 8
TAMANHO_TOKEN_BYTES = 32


def gerar_hash_senha(senha: str) -> str:
    return _hasher.hash(senha)


def verificar_senha(senha: str, hash_armazenado: str) -> tuple[bool, str | None]:
    """Verifica a senha e, se preciso, devolve um hash atualizado.

    O segundo elemento vem preenchido quando os parâmetros de custo mudaram: o
    chamador deve gravá-lo. É assim que se endurece o hash ao longo do tempo sem
    forçar ninguém a trocar de senha.
    """
    return _hasher.verify_and_update(senha, hash_armazenado)


def gerar_token_opaco() -> str:
    """Token de sessão ou de verificação. Só o portador o conhece."""
    return secrets.token_urlsafe(TAMANHO_TOKEN_BYTES)


def hash_token(token: str) -> str:
    """SHA-256 do token, que é o que vai para o banco.

    Não precisa de KDF lento: o token já tem 256 bits de entropia, então força
    bruta é inviável -- diferente de uma senha escolhida por humano.
    """
    return hashlib.sha256(token.encode()).hexdigest()


def hash_conteudo(texto: str) -> str:
    """Hash de um termo. É o que transforma o aceite numa prova."""
    return hashlib.sha256(texto.encode()).hexdigest()


def comparar_seguro(a: str, b: str) -> bool:
    """Comparação em tempo constante, para tokens e CSRF."""
    return hmac.compare_digest(a, b)


def gerar_chave_acesso() -> tuple[str, str]:
    """As "chaves de acesso aleatórias" do requisito original.

    Devolve ``(chave_em_claro, hash)``. A chave é exibida **uma vez**; o banco
    guarda só o hash.
    """
    chave = secrets.token_urlsafe(TAMANHO_TOKEN_BYTES)
    return chave, hash_token(chave)


def validar_forca_senha(senha: str) -> str | None:
    """Devolve a mensagem de erro, ou ``None`` se a senha serve.

    Regras propositalmente simples: comprimento é o que mais importa, e exigir
    símbolos leva a "Senha@123", que é pior do que uma frase longa.
    """
    if len(senha) < TAMANHO_MINIMO_SENHA:
        return f"A senha precisa ter pelo menos {TAMANHO_MINIMO_SENHA} caracteres."
    if senha.isdigit():
        return "A senha não pode ser só números."
    if senha.lower() in {"12345678", "senha123", "password", "psiconnect"}:
        return "Esta senha é muito comum. Escolha outra."
    return None
