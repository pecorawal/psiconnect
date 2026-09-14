# ADR 0005 — Comissão parametrizável, default 12%

- **Status:** aceita
- **Data:** 2026-08-12 (revisada em 2026-08-13: default de 5% para 12%)

## Contexto

As duas fontes de requisito **discordavam**:

- `funcionalidades.md`: "o custo da plataforma será de **5%** sobre o valor de
  cada atendimento concluído";
- mapa mental (ramo *Cadastro do Psicólogo*): "a plataforma deve mostrar para
  ele que **3%** será o custo pelo uso da plataforma".

Fixar qualquer um dos dois no código transformaria uma decisão comercial — que
muda com o mercado e com a estrutura de custos — em deploy.

## Decisão

A comissão é um **parâmetro de sistema**, não uma constante:

- `ParametroSistema['comissao.percentual_padrao']`, editável pelo admin sem
  deploy;
- `RegraComissao` permite override por escopo
  (`GLOBAL | PROFISSIONAL | PLANO | PROFISSIONAL_PLANO`) com vigência;
- o valor `.env` `COMISSAO_PERCENTUAL_PADRAO` serve **apenas** para semear o
  parâmetro na primeira execução.

O **default passou de 5% para 12%** para cobrir os custos de API por sessão e a
infraestrutura.

## A conta

Custo variável por sessão de 50 min, com as APIs pagas:

| Item | Custo | Origem |
|---|---|---|
| Vídeo (daily.co) | ~R$ 2,20 | US$ 0,004/participante-minuto × 50 min × 2 |
| Transcrição (Whisper API) | ~R$ 1,65 | ~US$ 0,006/min |
| **Total** | **~R$ 3,85** | |

Sobre uma sessão de R$ 150,00:

| Comissão | Plataforma recebe | − custo de API | Margem | Profissional perde |
|---|---|---|---|---|
| 5% | R$ 7,50 | R$ 3,85 | R$ 3,65 | ~10% |
| 8% | R$ 12,00 | R$ 3,85 | R$ 8,15 | ~13% |
| **12%** | **R$ 18,00** | R$ 3,85 | **R$ 14,15** | **~17%** |

Break-even em sessões/mês para cobrir a infraestrutura fixa:

| Infra fixa | a 5% | a 8% | a 12% |
|---|---|---|---|
| R$ 1.000 | 274 | 123 | **71** |
| R$ 2.000 | 548 | 245 | **141** |
| R$ 4.000 | 1.096 | 491 | **283** |

## Duas correções de premissa que a conta revelou

1. **A taxa do gateway não era prejuízo da plataforma.** No modelo implementado
   ela é descontada do **profissional** (`taxa_provedor_centavos` é uma linha
   separada de `comissao_plataforma_centavos`). A 5% a plataforma tinha margem
   positiva de R$ 3,65 por sessão — magra, mas não negativa. O que apertava era
   a **infraestrutura fixa**, não os pagamentos.

2. **O custo variável é majoritariamente evitável.** LiveKit self-hosted e
   `faster-whisper` local transformam os R$ 3,85/sessão em infra fixa, e ainda
   resolvem a transferência internacional de áudio de psicoterapia (LGPD
   art. 33 — ver [ADR 0003](0003-sem-gravacao-apenas-transcricao.md)). Feita
   essa troca, o break-even a 12% cai de 141 para 111 sessões/mês com R$ 2.000
   de infra — e uma comissão menor volta a ser viável.

## Consequência a acompanhar

Com 12% de comissão mais a taxa do crédito (~4,98%), **o profissional perde
~17% do valor da sessão**. É esse o número que ele vê em
`/profissional/simulador` antes de definir o preço, e é por ele que vai comparar
a plataforma com as alternativas. Vale monitorar a taxa de conclusão do
onboarding de profissionais depois da mudança.

## A regra que evita o pior bug desta área

`CompraPlano.percentual_comissao_aplicado` **congela** o percentual no momento
da compra. Mudar a comissão de 5% para 12% **não** reprecifica compras de ontem
nem repasses já calculados. Sem isso, o ajuste alteraria retroativamente o
quanto cada profissional deveria ter recebido.

Por isso a atualização exige um comando explícito:

```bash
python -m app.seeds --atualizar-parametros
```

O `python -m app.seeds` comum **não** sobrescreve parâmetros já gravados —
mudar uma regra de negócio não pode ser efeito colateral de rodar o seed.

## Consequências

- Cálculo centralizado em `services/regras/precificacao.py`, função pura, com a
  invariante testada: `taxa + comissão + imposto + líquido == bruto` — nenhum
  centavo criado ou perdido.
- A tela `/profissional/simulador` mostra ao profissional, ao vivo, quanto sobra
  por método de pagamento — cumprindo o requisito do mapa mental de informar
  "os custos adicionais com pagamento por cartão e impostos".
- Todo dinheiro é `int` em centavos (`app/core/dinheiro.py`), nunca `float`.
