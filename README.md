# Dashboard Técnico de FIIs

Painel técnico diário para **FIIs e FI-Infra negociados na B3**, desenvolvido em Python.

O script baixa dados de mercado, calcula indicadores técnicos, classifica o viés de cada ativo e gera um painel em HTML e CSV. Opcionalmente, também produz análises de correlação entre os ativos.

> **Importante:** esta ferramenta é educacional e não constitui recomendação de investimento.

---

## Ativos monitorados

### FIIs

- TRXF11
- CPTS11
- PSEC11
- VILG11
- XPCI11
- PVBI11
- XPML11
- RBRX11
- BRCR11
- VISC11
- RBVA11

### FI-Infra

- JURO11
- KDIF11

Os FI-Infra são mantidos separados dos FIIs tradicionais porque possuem características econômicas e riscos diferentes.

---

## O que o painel analisa

Para cada ativo, o script calcula:

- preço de fechamento;
- retorno em 20 pregões;
- retorno em 60 pregões;
- RSI de 14 períodos;
- média móvel de 9 períodos;
- média móvel de 21 períodos;
- preço em relação à MM21;
- MACD;
- Bandas de Bollinger;
- volume relativo;
- score de tendência;
- extensão técnica;
- nível de confiança;
- viés técnico final.

O objetivo não é gerar uma recomendação automática de compra ou venda, mas identificar o estado técnico atual do ativo.

---

## Metodologia

### Preço bruto e preço ajustado

O script mantém duas séries diferentes:

- `Close`: preço de fechamento bruto;
- `AdjClose`: fechamento ajustado por eventos corporativos e proventos, quando disponível.

O **preço exibido no painel** utiliza o fechamento bruto.

Os **retornos e indicadores técnicos** utilizam o fechamento ajustado.

Isso reduz distorções provocadas por distribuições de rendimentos, algo especialmente importante para FIIs.

---

## Score de tendência

O score direcional utiliza quatro condições.

| Indicador | Condição positiva | Pontos |
|---|---|---:|
| MM9 × MM21 | MM9 > MM21 | +1 |
| Preço × MM21 | Preço ajustado > MM21 | +1 |
| MACD | Histograma positivo | +1 |
| Retorno 20d | Retorno > 0 | +1 |

Quando uma condição não é atendida, ela soma `-1`.

O score final varia de `-4` a `+4`.

### Classificação

| Score | Viés técnico |
|---:|---|
| +3 a +4 | FORTE POSITIVO |
| +1 a +2 | POSITIVO |
| 0 | NEUTRO |
| -1 a -2 | NEGATIVO |
| -3 a -4 | FORTE NEGATIVO |

O painel usa **viés técnico** em vez de `COMPRA` ou `VENDA` porque análise técnica isolada não é suficiente para determinar uma decisão de investimento em FIIs.

---

## RSI e Bandas de Bollinger

RSI e Bollinger não fazem parte diretamente do score direcional.

Eles são usados como indicadores de **extensão do movimento**.

Exemplos:

- `RSI >= 70`: sobrecomprado;
- `RSI <= 30`: sobrevendido;
- preço acima da banda superior: movimento estendido para cima;
- preço abaixo da banda inferior: movimento estendido para baixo.

Isso evita um problema comum em scanners técnicos: interpretar automaticamente um RSI alto como venda ou um RSI baixo como compra.

Um ativo pode permanecer sobrecomprado durante uma tendência forte.

Da mesma forma, um ativo sobrevendido pode continuar caindo.

---

## Volume relativo

O volume relativo é calculado como:

```text
volume do último pregão
------------------------
média dos 20 pregões anteriores
```

Exemplo:

```text
Vol. rel. = 1,50
```

significa que o volume atual equivale a aproximadamente 150% da média recente.

O volume **não determina a direção do sinal**.

Ele é usado para aumentar ou reduzir a confiança no movimento identificado pelos demais indicadores.

---

## Confiança do sinal

O painel classifica a confiança como:

- `ALTA`
- `MODERADA`
- `BAIXA`

A confiança considera principalmente:

- força do score;
- coerência dos retornos de 20 e 60 pregões;
- volume relativo.

Um movimento direcional forte acompanhado de volume acima da média recebe maior confiança.

---

## RSI

O RSI utiliza suavização exponencial baseada no método de Wilder.

Também são tratados explicitamente os casos extremos:

```text
somente altas        -> RSI 100
somente quedas       -> RSI 0
sem variação         -> RSI 50
```

Isso evita valores `NaN` em séries com movimentos unidirecionais.

---

## Candle do dia atual

Por padrão, o script evita utilizar um candle diário potencialmente incompleto.

Antes de **18:30 no horário de São Paulo**, se existir um candle correspondente ao dia atual, ele é removido da análise.

Isso evita comparar:

```text
volume parcial do pregão
```

com:

```text
volume de pregões completos
```

e também evita calcular RSI, MACD e médias móveis sobre um candle ainda em formação.

Para incluir explicitamente o candle atual:

```bash
python dashboard_fiis.py --include-today
```

---

## Fontes de dados

O script utiliza duas fontes.

### 1. Yahoo Finance

Fonte principal.

O ticker da B3 é convertido para o formato:

```text
TRXF11 -> TRXF11.SA
```

O Yahoo é consultado primeiro.

### 2. brapi

Utilizada como fallback quando o Yahoo não retorna dados suficientes.

Se `adjustedClose` estiver disponível, ele é utilizado como preço ajustado.

O token da brapi é opcional.

---

# Instalação

## Requisitos

Recomendado:

- Python 3.10 ou superior;
- `uv`;
- acesso à internet.

Dependências Python:

```text
yfinance
pandas
numpy
requests
matplotlib
seaborn
```

---

## Instalação recomendada com uv

Como instalações Python gerenciadas podem bloquear `pip install` diretamente no ambiente global, é recomendado usar um ambiente virtual.

### 1. Criar o ambiente

```bash
uv venv --python 3.12
```

### 2. Ativar

Linux/macOS:

```bash
source .venv/bin/activate
```

Windows PowerShell:

```powershell
.venv\Scripts\Activate.ps1
```

### 3. Instalar dependências

```bash
uv pip install yfinance pandas numpy requests matplotlib seaborn
```

### 4. Verificar

```bash
python --version
```

e:

```bash
python -c "import yfinance, pandas, numpy, requests, matplotlib, seaborn; print('OK')"
```

---

## Alternativa usando pyproject.toml

Se o projeto já utiliza `uv` com `pyproject.toml`:

```bash
uv add yfinance pandas numpy requests matplotlib seaborn
```

Depois execute:

```bash
uv run python dashboard_fiis.py
```

---

# Uso

## Execução padrão

```bash
python dashboard_fiis.py
```

ou com `uv`:

```bash
uv run python dashboard_fiis.py
```

O script:

1. baixa os dados;
2. calcula os indicadores;
3. imprime o painel no terminal;
4. cria uma pasta para a execução;
5. gera HTML;
6. gera CSV;
7. abre o HTML no navegador.

---

## Gerar correlações

```bash
python dashboard_fiis.py --corr
```

Também serão criados:

```text
fii_correlation.png
rolling_fii_correlation.png
```

---

## Mostrar gráficos na tela

```bash
python dashboard_fiis.py --corr --show
```

Sem `--show`, os gráficos são apenas salvos em arquivo.

---

## Alterar o período histórico

O padrão é:

```text
1y
```

Exemplo com dois anos:

```bash
python dashboard_fiis.py --period 2y
```

Valores aceitos:

```text
1mo
3mo
6mo
1y
2y
5y
10y
ytd
max
```

Para correlação, períodos maiores normalmente produzem resultados mais estáveis.

Exemplo:

```bash
python dashboard_fiis.py --period 2y --corr
```

---

## Não abrir o navegador

```bash
python dashboard_fiis.py --no-open
```

---

## Escolher o diretório de saída

```bash
python dashboard_fiis.py --output-dir ./reports
```

O script ainda criará uma pasta individual para a execução dentro desse diretório.

---

# Arquivos gerados

Cada execução cria uma pasta no formato:

```text
DD-MM-AAAA_HH-MM-SS/
```

Exemplo:

```text
06-10-2026_18-30-00/
├── painel_fiis.html
└── painel_fiis.csv
```

Com:

```bash
python dashboard_fiis.py --corr
```

a saída passa a ser:

```text
06-10-2026_18-30-00/
├── painel_fiis.html
├── painel_fiis.csv
├── fii_correlation.png
└── rolling_fii_correlation.png
```

---

# Correlação

A análise de correlação utiliza **retornos logarítmicos diários calculados sobre o fechamento ajustado**.

Cada ativo tem seu retorno calculado individualmente antes do alinhamento com os demais ativos.

Isso evita que um pregão ausente em determinado fundo altere indevidamente a série dos outros.

---

## Heatmap

O arquivo:

```text
fii_correlation.png
```

mostra a correlação de Pearson entre os retornos diários.

Interpretação aproximada:

```text
+1,00 -> movimentos muito semelhantes
 0,00 -> baixa relação linear
-1,00 -> movimentos opostos
```

Correlação não mede:

- qualidade do fundo;
- risco de crédito;
- qualidade dos imóveis;
- diversificação econômica;
- risco de gestão;
- sustentabilidade dos dividendos.

---

## Correlação móvel

O arquivo:

```text
rolling_fii_correlation.png
```

utiliza uma janela móvel de:

```text
60 pregões
```

Os pares são selecionados utilizando a maior correlação absoluta observada nos últimos:

```text
120 pregões
```

São exibidos no máximo:

```text
6 pares
```

A seleção automática dos pares considera os FIIs da lista `FIIS`.

---

# Contexto de mercado

Por padrão, a matriz também inclui:

```text
IBOV -> ^BVSP
```

Configurado em:

```python
CONTEXTO_YAHOO = {
    "IBOV": "^BVSP",
}
```

Outras referências podem ser adicionadas caso exista uma série confiável no provedor utilizado.

---

# Token da brapi

O token é opcional.

Sem token, o Yahoo Finance continua sendo utilizado normalmente e a brapi pode funcionar conforme os limites públicos disponíveis.

## Linux/macOS

```bash
export BRAPI_TOKEN="seu_token"
```

## Windows PowerShell

```powershell
$env:BRAPI_TOKEN="seu_token"
```

O token é enviado utilizando:

```http
Authorization: Bearer SEU_TOKEN
```

Evite colocar tokens diretamente no código ou versioná-los no Git.

---

# Personalizando os ativos

Os FIIs são definidos em:

```python
FIIS = [
    "TRXF11",
    "CPTS11",
    ...
]
```

Os FI-Infra ficam separados:

```python
FI_INFRA = [
    "JURO11",
    "KDIF11",
]
```

Para adicionar um novo FII:

```python
FIIS = [
    "TRXF11",
    "XPML11",
    "NOVO11",
]
```

Não adicione o sufixo `.SA`.

O script adiciona automaticamente esse sufixo ao consultar o Yahoo Finance.

---

# Personalizando os parâmetros

Principais configurações:

```python
PERIODO_DADOS = "1y"

JANELA_ROLLING = 60

JANELA_SELECAO_PARES = 120

MAX_PARES_ROLLING = 6

MIN_OBSERVACOES = 60

HORARIO_CANDLE_SEGURO = time(18, 30)
```

---

# Como interpretar o painel

Exemplo hipotético:

```text
Ativo:            XPML11
Ret. 20d:         +4,20%
Ret. 60d:         +7,10%
RSI14:            68
MM9 > MM21:       sim
Preço > MM21:     sim
MACD:             positivo
Vol. rel.:        1,35
Score:            +4
Extensão:         normal
Confiança:        ALTA
Viés técnico:     FORTE POSITIVO
```

Isso significa que:

- a tendência de curto prazo é positiva;
- o preço permanece acima da média relevante;
- o momentum está positivo;
- o retorno recente confirma a tendência;
- o volume está acima da média;
- ainda não há necessariamente uma condição extrema de RSI/Bollinger.

Isso **não significa automaticamente que o ativo está barato ou que deve ser comprado**.

---

# Análise fundamentalista

O painel é técnico.

Para FIIs, o resultado deve ser combinado com análise fundamentalista.

## FIIs de tijolo

Avaliar, entre outros:

- P/VP;
- dividend yield;
- vacância física;
- vacância financeira;
- qualidade dos imóveis;
- localização;
- concentração por imóvel;
- concentração por locatário;
- prazo dos contratos;
- revisional;
- vencimentos;
- alavancagem;
- emissões;
- qualidade da gestão.

## FIIs de papel

Avaliar:

- P/VP;
- dividend yield sustentável;
- indexadores;
- spread;
- duration;
- LTV;
- garantias;
- rating;
- inadimplência;
- concentração por devedor;
- concentração por operação;
- exposição a IPCA e CDI.

## FoFs e multiestratégia

Avaliar:

- desconto ou prêmio patrimonial;
- alocação;
- concentração;
- resultado recorrente;
- ganho de capital;
- giro da carteira;
- taxa de administração;
- qualidade da gestão.

## FI-Infra

Avaliar separadamente:

- yield;
- duration;
- indexadores;
- spread de crédito;
- rating;
- concentração por emissor;
- risco de crédito;
- estrutura das debêntures;
- sensibilidade à curva de juros.

---

# Troubleshooting

## `ModuleNotFoundError`

Exemplo:

```text
ModuleNotFoundError: No module named 'yfinance'
```

Ative o ambiente:

```bash
source .venv/bin/activate
```

e instale:

```bash
uv pip install yfinance pandas numpy requests matplotlib seaborn
```

---

## `externally-managed-environment`

Se aparecer:

```text
error: externally-managed-environment
```

não instale os pacotes diretamente no Python global.

Crie um ambiente virtual:

```bash
uv venv --python 3.12
source .venv/bin/activate
uv pip install yfinance pandas numpy requests matplotlib seaborn
```

Evite usar:

```bash
pip install --break-system-packages
```

para este projeto.

---

## Um ativo aparece como `FALHOU`

Verifique:

1. conexão com a internet;
2. disponibilidade do ticker no Yahoo Finance;
3. versão do `yfinance`;
4. disponibilidade da brapi;
5. `BRAPI_TOKEN`, se utilizado;
6. VPN;
7. proxy;
8. firewall ou rede corporativa.

Atualização dentro do ambiente virtual:

```bash
uv pip install --upgrade yfinance
```

---

## Yahoo retorna dados incompletos

Teste diretamente:

```bash
python -c "import yfinance as yf; print(yf.download('TRXF11.SA', period='1y', progress=False).tail())"
```

---

## Não quero abrir o HTML automaticamente

Use:

```bash
python dashboard_fiis.py --no-open
```

---

# Estrutura sugerida do projeto

```text
dashboard-fiis/
├── dashboard_fiis.py
├── README.md
├── .gitignore
├── pyproject.toml
└── reports/
```

Exemplo de `.gitignore`:

```gitignore
.venv/
__pycache__/
*.pyc

# relatórios
reports/
[0-9][0-9]-[0-9][0-9]-[0-9][0-9][0-9][0-9]_*/
```

---

# Limitações

Este projeto possui algumas limitações importantes:

- depende de fontes públicas de mercado;
- Yahoo Finance e brapi podem alterar formato ou disponibilidade dos dados;
- análise técnica não estima valor justo;
- não analisa automaticamente os relatórios gerenciais dos FIIs;
- não calcula P/VP;
- não calcula dividend yield projetado;
- não analisa vacância;
- não analisa risco de crédito;
- não analisa emissões;
- não analisa qualidade da gestão;
- não considera custos, impostos ou posição individual do investidor;
- correlação histórica não garante comportamento futuro.

---

# Próximas evoluções

Possíveis extensões do projeto:

- adicionar IFIX como benchmark;
- adicionar CDI, IPCA e curva de juros;
- separar dashboards por segmento;
- calcular P/VP;
- importar dividendos;
- calcular dividend yield histórico e forward;
- incorporar dados fundamentalistas;
- criar score específico para FIIs de tijolo;
- criar score específico para FIIs de papel;
- criar score específico para FI-Infra;
- adicionar volatilidade;
- adicionar drawdown;
- calcular Sharpe e Sortino;
- criar ranking de risco;
- gerar histórico diário do dashboard;
- enviar alertas;
- gerar relatório consolidado da carteira.

---

# Aviso

Este projeto foi criado para fins de estudo e acompanhamento de mercado.

Os dados podem conter atrasos, erros ou indisponibilidades.

Nenhum indicador, score, classificação ou gráfico produzido pelo script representa recomendação de compra, venda ou manutenção de qualquer ativo.

Antes de investir em FIIs ou FI-Infra, faça sua própria análise e considere os riscos envolvidos.
