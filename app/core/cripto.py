"""Criptografia de dados sensíveis em repouso.

AES-256-GCM: cifra e autentica ao mesmo tempo, então um byte alterado no banco
é detectado na leitura em vez de virar texto corrompido.

O **AAD** (dados adicionais autenticados) amarra o texto cifrado ao seu
contexto. Sem ele, alguém com acesso ao banco poderia mover o blob da linha A
para a linha B e a decifragem funcionaria — o conteúdo apareceria como
pertencente a outra pessoa. Com AAD, a decifragem falha.

Usado por:
* documento de identificação do responsável legal (art. 14 da LGPD);
* CPF de paciente e profissional;
* transcrições de sessão (Fase 5).
"""

from __future__ import annotations

import base64
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.erros import ErroDominio

TAMANHO_NONCE = 12  # recomendado para GCM
TAMANHO_CHAVE = 32  # AES-256


class ChaveNaoConfigurada(ErroDominio):
    codigo = "chave_cripto_ausente"
    status_http = 500
    mensagem_padrao = "Criptografia não configurada. Avise o suporte."


class DadoCorrompido(ErroDominio):
    codigo = "dado_corrompido"
    status_http = 500
    mensagem_padrao = "Não foi possível ler este dado."


def gerar_chave_base64() -> str:
    """Gera uma chave nova. Para popular ``CHAVE_CRIPTO_TRANSCRICAO``."""
    return base64.b64encode(os.urandom(TAMANHO_CHAVE)).decode()


def _chave(chave_base64: str) -> AESGCM:
    if not chave_base64:
        raise ChaveNaoConfigurada(
            "CHAVE_CRIPTO_TRANSCRICAO não está definida. Gere uma com: "
            'python -c "from app.core.cripto import gerar_chave_base64; '
            'print(gerar_chave_base64())"'
        )
    try:
        bruta = base64.b64decode(chave_base64)
    except Exception as exc:
        raise ChaveNaoConfigurada("CHAVE_CRIPTO_TRANSCRICAO não é base64 válido.") from exc
    if len(bruta) != TAMANHO_CHAVE:
        raise ChaveNaoConfigurada(
            f"A chave precisa ter {TAMANHO_CHAVE} bytes; veio com {len(bruta)}."
        )
    return AESGCM(bruta)


def cifrar(conteudo: bytes, *, chave_base64: str, contexto: str) -> bytes:
    """Cifra ``conteudo``. O ``contexto`` vira AAD e precisa ser o mesmo na leitura.

    O nonce vai no início do resultado: ele não é secreto, só precisa ser único
    por operação com a mesma chave.
    """
    nonce = os.urandom(TAMANHO_NONCE)
    cifrado = _chave(chave_base64).encrypt(nonce, conteudo, contexto.encode())
    return nonce + cifrado


def decifrar(blob: bytes, *, chave_base64: str, contexto: str) -> bytes:
    """Decifra. Levanta ``DadoCorrompido`` se o conteúdo ou o contexto não batem."""
    if len(blob) <= TAMANHO_NONCE:
        raise DadoCorrompido()
    nonce, cifrado = blob[:TAMANHO_NONCE], blob[TAMANHO_NONCE:]
    try:
        return _chave(chave_base64).decrypt(nonce, cifrado, contexto.encode())
    except InvalidTag as exc:
        raise DadoCorrompido() from exc


def cifrar_texto(texto: str, *, chave_base64: str, contexto: str) -> bytes:
    return cifrar(texto.encode(), chave_base64=chave_base64, contexto=contexto)


def decifrar_texto(blob: bytes, *, chave_base64: str, contexto: str) -> str:
    return decifrar(blob, chave_base64=chave_base64, contexto=contexto).decode()
