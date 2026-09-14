# ADR 0004 — Mercado Pago com split de marketplace

- **Status:** aceita (com pontos a confirmar)
- **Data:** 2026-08-12

## Contexto

O produto precisa receber por Pix, cartão de débito e cartão de crédito, e
repassar ao psicólogo o valor menos a comissão da plataforma.

Havia uma decisão estrutural anterior à escolha do gateway: **o dinheiro passa
pela plataforma ou vai direto ao profissional?**

## Decisão

**Mercado Pago com split de pagamento (marketplace)**, atrás da porta
`PaymentProvider` (`app/providers/base.py`), com `FakePaymentProvider` completo
para a Fase 1 e testes.

O profissional autoriza a aplicação via **OAuth**; o pagamento é criado com o
token do vendedor e uma `application_fee` igual à comissão. O valor do
atendimento vai para a conta do profissional; só a comissão vai para a
plataforma.

## Justificativa

1. **Regulatória, e é a razão principal.** No modelo "recebo tudo e repasso
   depois", a plataforma passa a movimentar recursos de terceiros, o que
   caracteriza arranjo de pagamento e atrai exigências do BACEN. Com split, o
   dinheiro nunca é da plataforma.
2. Mercado Pago é dominante no Brasil, tem Pix + cartão + split nativos, SDK
   Python e sandbox utilizável.
3. Alternativa considerada: **Asaas**, que tem NFS-e integrada (cobriria também
   o requisito de nota fiscal do mapa mental). Fica registrado como candidato
   caso a emissão de NF pelo profissional entre no escopo.

## Pontos a confirmar com o Mercado Pago (antes da Fase 2)

- A aplicação marketplace exige **CNPJ** da plataforma?
- Quando a `application_fee` é liberada para saque?
- Comportamento de estorno **parcial** com split.
- Regras de split especificamente em **Pix**.

## Consequências

- **Fricção no onboarding**: o profissional só recebe depois de conectar a conta
  do Mercado Pago. Esse passo precisa entrar no wizard da Fase 2.
- **Quem paga a taxa do gateway**: o modelo separa `taxa_provedor_centavos` de
  `comissao_plataforma_centavos`, e hoje a taxa é descontada do **profissional**.
  Com a comissão de 12% mais o crédito (~4,98%), ele perde ~17% do valor da
  sessão — número exibido no simulador antes de definir o preço.
  Ver [ADR 0005](0005-comissao-parametrizavel.md).
