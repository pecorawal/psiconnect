# ADR 0005 — Comissão parametrizável, default 5%

- **Status:** aceita
- **Data:** 2026-08-12

## Contexto

As duas fontes de requisito **discordavam**:

- `funcionalidades.md`: "o custo da plataforma será de **5%** sobre o valor de
  cada atendimento concluído";
- mapa mental (ramo *Cadastro do Psicólogo*): "a plataforma deve mostrar para
  ele que **3%** será o custo pelo uso da plataforma".

Fixar qualquer um dos dois no código transformaria uma decisão comercial — que
muda com o mercado — em deploy.

## Decisão

A comissão é um **parâmetro de sistema**, não uma constante:

- `ParametroSistema['comissao.percentual_padrao']`, default **5.0**, editável
  pelo admin sem deploy;
- `RegraComissao` permite override por escopo
  (`GLOBAL | PROFISSIONAL | PLANO | PROFISSIONAL_PLANO`) com vigência;
- o valor `.env` `COMISSAO_PERCENTUAL_PADRAO` serve **apenas** para semear o
  parâmetro na primeira execução.

## A regra que evita o pior bug desta área

`CompraPlano.percentual_comissao_aplicado` **congela** o percentual no momento
da compra. Mudar a comissão global amanhã **não** reprecifica compras de ontem,
nem altera repasses já calculados. Sem isso, um ajuste de 5% para 6% mudaria
retroativamente o quanto cada profissional deveria ter recebido.

## Consequências

- Cálculo centralizado em `services/regras/precificacao.py`, função pura, com a
  invariante testada: `taxa + comissão + imposto + líquido == bruto` — nenhum
  centavo criado ou perdido.
- A tela `/profissional/simulador` mostra ao profissional, ao vivo, quanto sobra
  por método de pagamento — cumprindo o requisito do mapa mental de informar
  "os custos adicionais com pagamento por cartão e impostos".
- Todo dinheiro é `int` em centavos (`app/core/dinheiro.py`), nunca `float`.
