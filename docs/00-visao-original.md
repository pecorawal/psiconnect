<!--
  DOCUMENTO HISTÓRICO -- PRESERVADO NA ÍNTEGRA.

  Este é o `funcionalidades.md` original do repositório `efatafy`, escrito antes
  de qualquer decisão técnica. Está aqui sem edição para servir de fonte da
  verdade sobre a INTENÇÃO original do produto.

  Onde o produto divergiu deste texto, a decisão está registrada em docs/adr/.
  Divergências conhecidas:
    - taxa da plataforma: 5% aqui, 3% no mapa mental -> ADR 0005
      (parametrizável; default revisto para 12% para cobrir custos de API e infra)
    - "sessão gravada"  -> ADR 0003 (apenas transcrição; mídia bruta descartada)
    - ordem do fluxo     -> sintomas -> horário -> pagamento (ver docs/01-visao-produto.md)
-->

# Descrição de funcionalidades

com base no mapa mental e como expert em tecnologia quero desenvolver uma plataforma de atendimento online de consultas de psicologos e terapeutas a pessoas com difiuldades emocionais de qualquer espécie e que necessitem de um atendimento humanizado. a ideia central da plataforma é que o profissional se cadastre, defina até 05 ramos de atuação dentro de sua área profissional (normalmente ramos que o profissional tenha mais expertise) e em seguida ele monte sua agenda de trabalho, na versão inicial, o custo da plataforma será de 5% sobre o valor de cada atendimento concluído entre profissional e paciente. Do lado do paciente ele entra escolhe alguns sintomas pelo qual esteja se sentindo mau e escolhe as agendas de acordo com o horário de melhor disponibilidade. Funcionalidades core: o sistema deve ser inteligente o suficiente para confirmar a agenda do profissional, tão logo o paciente defina suas datas, o paciente recebe uma notificação no whatsapp e no email que a próxima consulta está agendada para a data e hora escolhidos e vai passar a mostrar os dados de nome e foto do profissional que atenderá. O sistema deve enviar os links para a chamada de vídeo uns 20 minutos antes do início da sessão, a sessão deve abrir dentro da plataforma com o vídeo sempre aberto e com a transcrição iniciada. Quando o paciente optar por entrar na sessão informar que a sessão será gravada, criptografada e com transcrição do conteúdo falado entre paciente e profissional. Informar no disclaimer uma caixa de seleção que o paciente está ciente da gravação e liberar a entrada no lobby da sala virtual, aguardando apenas o profissional fazer o aceite. Para manter a qualidade dos serviços, o profissional que for pontual na entrada da sala ganha pontos que vão decidir a melhor forma de bonificação posteriormente. Ao final de cada sessão obrigatoriamente o paciente também responde a 3 questões sobre plataforma, profissional e feedback sobre seu atendimento. Isso também gerará pontuação para o paciente, demonstrando que a plataforma tem bons resultados a profissionais e pacientes. Agora que demonstrei o funcionalidades.

Agora que demonstrei as funcionalidades, desenvolva um sistema em python que tenha um visual MUITO clean, de fácil aceitação e cadastramento, o sistema deve ser web, mas propiciar uso de tablet e celulares de maneira que seja possivel utilizar por qualquer um dos dispositivos. crie as agendas dos profissionais e evite que eles agendem mais do que 10 horas diárias, dados obrigatorios, nome, foto de perfil profissional, codigo do crp ou crefito, escolher no máximo 05 áreas de especialidade e criar sua propria descrição com até 500 caracteres. para o paciente, sao necessarios os dados pessoais, selecionar a forma de pagamento (pix, cartao debito ou credito), depois ele pode criar a agenda e selecionar o dia e hora da primeira agenda, a agenda sugere que ele faça no mesmo horário até 2 vezes por semana, o paciente nao pode agendar mais do que 3 sessões semanais. fazer os bancos de dados de cadastro e agendas de forma relacional, e as transcrições das sessoes de forma vetorial para que seja aproveitada futuramente com inteligencia artificial. faça as integrações necessárias para uma plataforma de video chamada,  pode ser google meet ou teams ou se vc tiver alguma para indicar e que seja de fácil integração e que seja uma chamada criptografada. no desenvolvimento do código leve em consideração essas integrações.

## videoconferencia recomendada
daily.co --> 10K minutos por mes


## Regulamentacao
Informações sobre HIPAA e BAA: o brasil esta regido pelas auditorias Healthcare (HIPAA + BAA)

Não. O Brasil não é regido, legalmente, pelas auditorias HIPAA (Health Insurance Portability and Accountability Act). A HIPAA é uma lei federal dos Estados Unidos promulgada em 1996, focada na proteção de informações de saúde naquele país. 

O que ocorre no Brasil é a adoção voluntária de normas internacionais, como a HIPAA, por empresas de saúde para aumentar a segurança de dados e a conformidade. 

Aqui estão os pontos principais sobre o cenário regulatório no Brasil:
LGPD (Lei Geral de Proteção de Dados): É a lei brasileira, em vigor desde 2020, que regula o tratamento de dados pessoais (incluindo sensíveis, como saúde) no Brasil. Ela é baseada na GDPR (Europa) e não na HIPAA.
Adoção Voluntária (HIPAA): Empresas que atuam com tecnologia de saúde (HealthTechs) ou clínicas que buscam padrões globais de segurança podem adotar a HIPAA e o BAA (Business Associate Agreement) para garantir a proteção de dados sensíveis, alinhando-se a normas de segurança mais rigorosas.
Anvisa e Auditorias: A Anvisa permite o uso de resultados de auditorias externas (internacionais ou nacionais) para apoiar decisões regulatórias, o que incentiva empresas a seguirem padrões elevados de auditoria. 

Em resumo, a legislação brasileira obrigatória é a LGPD, enquanto o HIPAA + BAA funciona como um padrão de excelência adotado voluntariamente por empresas brasileiras de saúde. 


## Melhores video conferencias para testar e embutir no código.
https://www.larksuite.com/pt_br/blog/video-conferencing-platform