#!/usr/bin/env python3
"""Gera os ícones PNG do PWA a partir da mesma marca do favicon.

Fica versionado (e não é rodado no build) porque o resultado é um binário no
repositório: quem trocar a cor ou o símbolo roda isto de novo em vez de abrir
um editor de imagem.

    .venv/bin/python scripts/gerar_icones.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

RAIZ = Path(__file__).resolve().parent.parent
DESTINO = RAIZ / "app" / "static" / "img"

TEAL = (15, 118, 110, 255)  # #0f766e -- o mesmo do favicon e do theme-color
BRANCO = (255, 255, 255, 255)
SIMBOLO = "Ψ"
FONTE = "/usr/share/fonts/liberation-sans-fonts/LiberationSans-Bold.ttf"

#: Supersampling: desenhar 4x maior e reduzir dá borda limpa sem antialias manual.
ESCALA = 4


def desenhar(lado: int, *, raio_relativo: float, glifo_relativo: float) -> Image.Image:
    grande = lado * ESCALA
    imagem = Image.new("RGBA", (grande, grande), (0, 0, 0, 0))
    pincel = ImageDraw.Draw(imagem)
    pincel.rounded_rectangle(
        (0, 0, grande - 1, grande - 1), radius=int(grande * raio_relativo), fill=TEAL
    )

    fonte = ImageFont.truetype(FONTE, int(grande * glifo_relativo))
    esquerda, topo, direita, base = pincel.textbbox((0, 0), SIMBOLO, font=fonte)
    pincel.text(
        ((grande - (direita - esquerda)) / 2 - esquerda, (grande - (base - topo)) / 2 - topo),
        SIMBOLO,
        font=fonte,
        fill=BRANCO,
    )
    return imagem.resize((lado, lado), Image.LANCZOS)


def main() -> None:
    DESTINO.mkdir(parents=True, exist_ok=True)
    saidas = {
        # Ícones comuns: cantos arredondados como no favicon.
        "icone-192.png": desenhar(192, raio_relativo=0.22, glifo_relativo=0.58),
        "icone-512.png": desenhar(512, raio_relativo=0.22, glifo_relativo=0.58),
        # Maskable: o sistema recorta o formato que quiser, então o fundo vai
        # até a borda e o símbolo encolhe para caber na zona segura de 80%.
        "icone-maskable-512.png": desenhar(512, raio_relativo=0.0, glifo_relativo=0.42),
        # iOS arredonda sozinho e não entende SVG aqui.
        "apple-touch-icon.png": desenhar(180, raio_relativo=0.0, glifo_relativo=0.55),
    }
    for nome, imagem in saidas.items():
        imagem.save(DESTINO / nome, format="PNG", optimize=True)
        print(f"{nome}: {(DESTINO / nome).stat().st_size} bytes")


if __name__ == "__main__":
    main()
