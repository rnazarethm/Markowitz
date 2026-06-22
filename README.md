# Fronteira Eficiente · Markowitz

Ferramenta web para análise de portfólio com base em Markowitz, hospedada no GitHub Pages com atualização automática mensal via GitHub Actions.

## Ativos

| Ativo | Proxy | Fonte |
|---|---|---|
| Ações Globais | VT (Vanguard Total World) convertido para BRL | Yahoo Finance |
| Ações Brasil | BOVA11 | Yahoo Finance |
| IPCA+ | IMAB11 | Yahoo Finance + IMA-B ANBIMA (histórico) |
| Pós-fixado | CDI acumulado mensal | SGS/BCB (série 11) |

## Estrutura

```
/
├─ index.html                    ← App web (Chart.js, zero dependências externas)
├─ update.py                     ← Script de atualização de dados
├─ data/
│   ├─ returns.csv               ← Retornos mensais (Ago/2010 → presente)
│   └─ frontier.json             ← Resultados pré-calculados (fronteira, pesos, stats)
└─ .github/workflows/
    └─ update.yml                ← Cron job: dia 2 de cada mês
```

## Como usar localmente

```bash
# Instalar dependências
pip install yfinance pandas numpy scipy requests

# Atualizar dados manualmente
python update.py
```

Depois abra `index.html` diretamente no navegador.

## Hospedar no GitHub Pages

1. Crie um repositório público no GitHub.
2. Faça upload de todos os arquivos.
3. Em **Settings → Pages**, selecione `Deploy from branch: main / root`.
4. Acesse `https://<usuario>.github.io/<repositorio>/`.

O GitHub Actions roda automaticamente no dia 2 de cada mês.

## Metodologia

- **Período**: Ago/2010 → presente (mínimo 191 meses)
- **VT_BRL**: `(1 + VT_USD) × (1 + USD/BRL) − 1`
- **IMAB11**: índice IMA-B ANBIMA até Mai/2019; ETF IMAB11 de Jun/2019 em diante
- **CDI**: retorno mensal acumulado a partir da taxa diária (SGS série 11)
- **Otimização**: mínima variância com restrição de retorno-alvo (SLSQP); sem venda a descoberto
- **Taxa livre de risco**: CDI médio do período de estimação
