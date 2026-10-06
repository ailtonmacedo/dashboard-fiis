#!/usr/bin/env python3

"""
Dashboard técnico diário para FIIs e FI-Infra negociados na B3.

Carteira principal:
    TRXF11, CPTS11, PSEC11, VILG11, XPCI11, PVBI11,
    XPML11, RBRX11, BRCR11, VISC11, RBVA11

FI-Infra:
    JURO11, KDIF11

Objetivos desta versão:
- manter preço bruto separado da série ajustada;
- usar fechamento ajustado para retornos e indicadores técnicos;
- normalizar datas entre Yahoo Finance e brapi;
- evitar, por padrão, candle intradiário ainda incompleto;
- corrigir os casos-limite do RSI;
- separar tendência de sobrecompra/sobrevenda;
- usar volume apenas como confirmação, e não como direção;
- reutilizar o mesmo cache de dados na análise e nas correlações;
- tornar a correlação mais robusta a dias sem negociação.

Uso:
    python dashboard_fiis.py
    python dashboard_fiis.py --corr
    python dashboard_fiis.py --corr --show
    python dashboard_fiis.py --include-today
    python dashboard_fiis.py --period 2y

Variável opcional:
    BRAPI_TOKEN

Dependências:
    yfinance
    pandas
    numpy
    requests
    matplotlib
    seaborn

Aviso:
    Ferramenta educativa. Não constitui recomendação de investimento.
"""

from __future__ import annotations

import argparse
import math
import os
import sys
import webbrowser
from collections.abc import Iterable
from datetime import time
from numbers import Real
from pathlib import Path

import numpy as np
import pandas as pd
import requests
import yfinance as yf

# =============================================================================
# Configuração
# =============================================================================

FIIS = [
    "TRXF11",
    "CPTS11",
    "PSEC11",
    "VILG11",
    "XPCI11",
    "PVBI11",
    "XPML11",
    "RBRX11",
    "BRCR11",
    "VISC11",
    "RBVA11",
]

FI_INFRA = [
    "JURO11",
    "KDIF11",
]

TICKERS = FI_INFRA + FIIS

TIPO_ATIVO = {
    **{ticker: "FI-Infra" for ticker in FI_INFRA},
    **{ticker: "FII" for ticker in FIIS},
}

# Contexto de mercado. O IFIX nem sempre possui série estável em todos
# os provedores gratuitos, então o script mantém o IBOV como referência
# garantida e permite adicionar outros símbolos Yahoo aqui.
CONTEXTO_YAHOO = {
    "IBOV": "^BVSP",
}

BRAPI_TOKEN = os.environ.get("BRAPI_TOKEN", "").strip()

PERIODO_DADOS = "1y"
JANELA_ROLLING = 60
JANELA_SELECAO_PARES = 120
MAX_PARES_ROLLING = 6
MIN_OBSERVACOES = 60

# Antes deste horário, um candle da data atual é tratado como potencialmente
# incompleto. Ajuste se sua rotina de execução for diferente.
HORARIO_CANDLE_SEGURO = time(18, 30)

# Cache único por execução.
_CACHE_DADOS: dict[str, tuple[pd.DataFrame, str]] = {}


# =============================================================================
# Utilidades
# =============================================================================

def criar_diretorio_execucao(base_dir: str = ".") -> str:
    """Cria um diretório DD-MM-AAAA_HH-MM-SS para esta execução."""
    nome = pd.Timestamp.now(tz="America/Sao_Paulo").strftime("%d-%m-%Y_%H-%M-%S")
    caminho = os.path.abspath(os.path.join(base_dir, nome))
    os.makedirs(caminho, exist_ok=True)
    return caminho


def checar_versao_yfinance() -> None:
    versao = getattr(yf, "__version__", "desconhecida")
    print(f"yfinance {versao}")


def _eh_numero(valor) -> bool:
    return (
        isinstance(valor, Real)
        and not isinstance(valor, bool)
        and math.isfinite(float(valor))
    )


def _fmt_num(valor, casas: int = 2, fallback: str = "-"):
    if pd.isna(valor):
        return fallback
    try:
        return round(float(valor), casas)
    except (TypeError, ValueError):
        return fallback


def fmt_pct(valor) -> str:
    if _eh_numero(valor):
        return f"{float(valor):+.2f}%"
    return "-"


def cor_retorno(valor) -> str:
    if not _eh_numero(valor):
        return "#6b6a63"
    if float(valor) > 0:
        return "#27500A"
    if float(valor) < 0:
        return "#791F1F"
    return "#444441"


def normalizar_indice_datas(df: pd.DataFrame) -> pd.DataFrame:
    """
    Converte o índice para datas normalizadas sem timezone.

    Isso evita desalinhamentos como:
        2026-10-05 00:00:00
        2026-10-05 03:00:00
    representando o mesmo pregão.
    """
    if df is None or df.empty:
        return pd.DataFrame()

    out = df.copy()
    idx = pd.to_datetime(out.index, errors="coerce")

    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_convert("America/Sao_Paulo").tz_localize(None)

    out.index = idx.normalize()
    out = out[~out.index.isna()]
    out = out[~out.index.duplicated(keep="last")]
    return out.sort_index()


def remover_candle_possivelmente_incompleto(
    df: pd.DataFrame,
    include_today: bool = False,
) -> pd.DataFrame:
    """
    Remove o candle da data atual quando ele ainda pode estar incompleto.

    Depois de HORARIO_CANDLE_SEGURO, o candle do próprio dia é mantido.
    Use --include-today para nunca aplicar esse filtro.
    """
    if include_today or df.empty:
        return df

    agora = pd.Timestamp.now(tz="America/Sao_Paulo")
    hoje = agora.tz_localize(None).normalize()

    if agora.time() >= HORARIO_CANDLE_SEGURO:
        return df

    if df.index.max() == hoje:
        return df.loc[df.index < hoje].copy()

    return df


def _extrair_coluna(
    df: pd.DataFrame,
    nomes: Iterable[str],
) -> pd.Series:
    """
    Extrai uma coluna de forma robusta, inclusive quando o yfinance
    devolve MultiIndex para um único ticker.
    """
    if df is None or df.empty:
        return pd.Series(dtype=float)

    nomes = list(nomes)

    if not isinstance(df.columns, pd.MultiIndex):
        for nome in nomes:
            if nome in df.columns:
                return pd.to_numeric(df[nome], errors="coerce")

    for col in df.columns:
        partes = col if isinstance(col, tuple) else (col,)
        if any(str(parte) in nomes for parte in partes):
            serie = df[col]
            if isinstance(serie, pd.DataFrame):
                if serie.shape[1] == 0:
                    continue
                serie = serie.iloc[:, 0]
            return pd.to_numeric(serie, errors="coerce")

    return pd.Series(dtype=float)


def padronizar_ohlcv(
    df: pd.DataFrame,
    include_today: bool = False,
) -> pd.DataFrame:
    """
    Converte qualquer resposta Yahoo para:
        Close     -> fechamento bruto
        AdjClose  -> fechamento ajustado
        Volume
    """
    if df is None or df.empty:
        return pd.DataFrame(columns=["Close", "AdjClose", "Volume"])

    close = _extrair_coluna(df, ["Close", "close"])
    adj = _extrair_coluna(
        df,
        ["Adj Close", "AdjClose", "adjustedClose", "adjclose"],
    )
    volume = _extrair_coluna(df, ["Volume", "volume"])

    if close.empty and adj.empty:
        return pd.DataFrame(columns=["Close", "AdjClose", "Volume"])

    if close.empty:
        close = adj.copy()

    if adj.empty:
        adj = close.copy()

    out = pd.DataFrame(index=df.index)
    out["Close"] = close
    out["AdjClose"] = adj
    out["Volume"] = volume

    out = normalizar_indice_datas(out)
    out = remover_candle_possivelmente_incompleto(
        out,
        include_today=include_today,
    )

    out["Close"] = pd.to_numeric(out["Close"], errors="coerce")
    out["AdjClose"] = pd.to_numeric(out["AdjClose"], errors="coerce")
    out["Volume"] = pd.to_numeric(out["Volume"], errors="coerce")

    return out.dropna(subset=["AdjClose"])


# =============================================================================
# Download de dados
# =============================================================================

def baixar_yahoo_simbolo(
    simbolo: str,
    period: str,
    include_today: bool = False,
) -> pd.DataFrame:
    """
    Baixa dados do Yahoo mantendo Close e Adj Close separados.
    auto_adjust=False é intencional.
    """
    try:
        df = yf.download(
            simbolo,
            period=period,
            interval="1d",
            progress=False,
            auto_adjust=False,
            actions=False,
            threads=False,
        )
        pad = padronizar_ohlcv(df, include_today=include_today)
        if len(pad) >= 30:
            return pad
    except Exception:
        pass

    try:
        df = yf.Ticker(simbolo).history(
            period=period,
            interval="1d",
            auto_adjust=False,
            actions=False,
        )
        pad = padronizar_ohlcv(df, include_today=include_today)
        if len(pad) >= 30:
            return pad
    except Exception:
        pass

    return pd.DataFrame(columns=["Close", "AdjClose", "Volume"])


def baixar_yahoo(
    ticker: str,
    period: str,
    include_today: bool = False,
) -> pd.DataFrame:
    return baixar_yahoo_simbolo(
        f"{ticker}.SA",
        period=period,
        include_today=include_today,
    )


def _brapi_headers() -> dict:
    headers = {
        "Accept": "application/json",
        "User-Agent": "dashboard-fiis/2.0",
    }
    if BRAPI_TOKEN:
        headers["Authorization"] = f"Bearer {BRAPI_TOKEN}"
    return headers


def baixar_brapi(
    ticker: str,
    period: str,
    include_today: bool = False,
) -> pd.DataFrame:
    """
    Fallback brapi.

    Mantém o endpoint de quote por compatibilidade com contas que não possuem
    acesso ao endpoint histórico Pro, mas prioriza adjustedClose quando o campo
    estiver presente.
    """
    url = f"https://brapi.dev/api/quote/{ticker}"
    params = {
        "range": period,
        "interval": "1d",
    }

    try:
        resposta = requests.get(
            url,
            params=params,
            headers=_brapi_headers(),
            timeout=20,
        )
        resposta.raise_for_status()
        payload = resposta.json()
    except Exception:
        return pd.DataFrame(columns=["Close", "AdjClose", "Volume"])

    resultados = payload.get("results", [])
    if not resultados:
        return pd.DataFrame(columns=["Close", "AdjClose", "Volume"])

    dados = resultados[0].get("historicalDataPrice", [])
    if not dados:
        return pd.DataFrame(columns=["Close", "AdjClose", "Volume"])

    bruto = pd.DataFrame(dados)

    if "date" not in bruto.columns:
        return pd.DataFrame(columns=["Close", "AdjClose", "Volume"])

    datas = pd.to_datetime(
        bruto["date"],
        unit="s",
        utc=True,
        errors="coerce",
    )

    datas = datas.dt.tz_convert("America/Sao_Paulo").dt.tz_localize(None)

    bruto.index = datas.dt.normalize()
    bruto = bruto[~bruto.index.isna()]
    bruto = bruto[~bruto.index.duplicated(keep="last")]
    bruto = bruto.sort_index()

    if "close" not in bruto.columns:
        return pd.DataFrame(columns=["Close", "AdjClose", "Volume"])

    close = pd.to_numeric(bruto["close"], errors="coerce")

    if "adjustedClose" in bruto.columns:
        adj = pd.to_numeric(bruto["adjustedClose"], errors="coerce")
    else:
        adj = close.copy()

    if "volume" in bruto.columns:
        volume = pd.to_numeric(bruto["volume"], errors="coerce")
    else:
        volume = pd.Series(index=bruto.index, dtype=float)

    out = pd.DataFrame(
        {
            "Close": close,
            "AdjClose": adj,
            "Volume": volume,
        },
        index=bruto.index,
    )

    out["AdjClose"] = out["AdjClose"].fillna(out["Close"])

    out = remover_candle_possivelmente_incompleto(
        out,
        include_today=include_today,
    )

    return out.dropna(subset=["AdjClose"]).sort_index()


def obter_dados(
    ticker: str,
    period: str,
    include_today: bool = False,
) -> tuple[pd.DataFrame, str]:
    """
    Yahoo -> brapi -> falha.

    Usa cache por ticker/período/modo intraday para impedir que o mesmo ativo
    seja baixado duas vezes na mesma execução.
    """
    chave = f"{ticker}|{period}|{int(include_today)}"

    if chave in _CACHE_DADOS:
        df, fonte = _CACHE_DADOS[chave]
        return df.copy(), fonte

    df = baixar_yahoo(
        ticker,
        period=period,
        include_today=include_today,
    )

    if len(df) >= 30:
        _CACHE_DADOS[chave] = (df.copy(), "Yahoo")
        return df, "Yahoo"

    df = baixar_brapi(
        ticker,
        period=period,
        include_today=include_today,
    )

    if len(df) >= 30:
        _CACHE_DADOS[chave] = (df.copy(), "brapi")
        return df, "brapi"

    vazio = pd.DataFrame(columns=["Close", "AdjClose", "Volume"])
    _CACHE_DADOS[chave] = (vazio.copy(), "FALHOU")
    return vazio, "FALHOU"


# =============================================================================
# Indicadores
# =============================================================================

def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """
    RSI de Wilder usando suavização exponencial.

    Casos-limite:
    - só ganhos -> RSI 100
    - só perdas -> RSI 0
    - sem variação -> RSI 50
    """
    close = pd.to_numeric(close, errors="coerce").dropna()
    delta = close.diff()

    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period,
    ).mean()

    avg_loss = loss.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period,
    ).mean()

    rs = avg_gain / avg_loss
    out = 100 - (100 / (1 + rs))

    out = out.mask((avg_loss == 0) & (avg_gain > 0), 100.0)
    out = out.mask((avg_gain == 0) & (avg_loss > 0), 0.0)
    out = out.mask((avg_gain == 0) & (avg_loss == 0), 50.0)

    return out


def retorno_periodo(close: pd.Series, dias: int) -> float:
    close = pd.to_numeric(close, errors="coerce").dropna()

    if len(close) <= dias:
        return float("nan")

    anterior = float(close.iloc[-(dias + 1)])
    atual = float(close.iloc[-1])

    if anterior == 0:
        return float("nan")

    return (atual / anterior - 1) * 100


def classificacao_bollinger(
    preco: float,
    banda_inf: float,
    banda_sup: float,
) -> str:
    if pd.isna(banda_inf) or pd.isna(banda_sup):
        return "-"
    if preco <= banda_inf:
        return "abaixo/inferior"
    if preco >= banda_sup:
        return "acima/superior"
    return "dentro"


def classificacao_extensao(
    rsi14: float,
    preco: float,
    banda_inf: float,
    banda_sup: float,
) -> str:
    sinais = []

    if pd.notna(rsi14):
        if rsi14 >= 70:
            sinais.append("RSI sobrecomprado")
        elif rsi14 <= 30:
            sinais.append("RSI sobrevendido")

    if pd.notna(banda_sup) and preco >= banda_sup:
        sinais.append("Bollinger superior")
    elif pd.notna(banda_inf) and preco <= banda_inf:
        sinais.append("Bollinger inferior")

    return " + ".join(sinais) if sinais else "normal"


def classificar_vies(score: int) -> str:
    if score >= 3:
        return "FORTE POSITIVO"
    if score >= 1:
        return "POSITIVO"
    if score <= -3:
        return "FORTE NEGATIVO"
    if score <= -1:
        return "NEGATIVO"
    return "NEUTRO"


def classificar_confianca(
    score: int,
    vol_rel: float,
    ret20: float,
    ret60: float,
) -> str:
    """Volume aumenta confiança, mas não define a direção."""
    direcao_coerente = False

    if pd.notna(ret20) and pd.notna(ret60):
        direcao_coerente = (
            (score > 0 and ret20 > 0 and ret60 > 0)
            or (score < 0 and ret20 < 0 and ret60 < 0)
        )

    volume_confirma = pd.notna(vol_rel) and vol_rel >= 1.20

    if abs(score) >= 3 and direcao_coerente and volume_confirma:
        return "ALTA"

    if abs(score) >= 2 and (direcao_coerente or volume_confirma):
        return "MODERADA"

    return "BAIXA"


def analisar(
    ticker: str,
    period: str,
    include_today: bool = False,
) -> dict:
    df, fonte = obter_dados(
        ticker,
        period=period,
        include_today=include_today,
    )

    if df.empty or len(df) < 30:
        return {
            "Ativo": ticker,
            "Tipo": TIPO_ATIVO.get(ticker, "-"),
            "Fonte": "FALHOU",
            "Viés técnico": "sem dados",
        }

    serie = pd.to_numeric(df["AdjClose"], errors="coerce").dropna()
    preco_bruto = pd.to_numeric(df["Close"], errors="coerce").dropna()
    volume = pd.to_numeric(df["Volume"], errors="coerce").dropna()

    if len(serie) < 30 or preco_bruto.empty:
        return {
            "Ativo": ticker,
            "Tipo": TIPO_ATIVO.get(ticker, "-"),
            "Fonte": "FALHOU",
            "Viés técnico": "sem dados",
        }

    mm9 = serie.rolling(9, min_periods=9).mean()
    mm21 = serie.rolling(21, min_periods=21).mean()

    ema12 = serie.ewm(span=12, adjust=False).mean()
    ema26 = serie.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    sinal_macd = macd.ewm(span=9, adjust=False).mean()
    macd_hist = macd - sinal_macd

    rsi14_series = rsi(serie, 14)
    rsi14 = rsi14_series.iloc[-1] if not rsi14_series.empty else float("nan")

    mm20 = serie.rolling(20, min_periods=20).mean()
    desvio20 = serie.rolling(20, min_periods=20).std()
    banda_sup = (mm20 + 2 * desvio20).iloc[-1]
    banda_inf = (mm20 - 2 * desvio20).iloc[-1]

    preco_tecnico = float(serie.iloc[-1])
    preco = float(preco_bruto.iloc[-1])

    vol_rel = float("nan")
    if len(volume) >= 21:
        media_anterior = volume.iloc[-21:-1].mean()
        if pd.notna(media_anterior) and media_anterior > 0:
            vol_rel = float(volume.iloc[-1] / media_anterior)

    ret20 = retorno_periodo(serie, 20)
    ret60 = retorno_periodo(serie, 60)

    # Score direcional.
    # RSI e Bollinger não somam pontos: eles medem extensão.
    score = 0

    mm9_acima = (
        pd.notna(mm9.iloc[-1])
        and pd.notna(mm21.iloc[-1])
        and mm9.iloc[-1] > mm21.iloc[-1]
    )

    preco_acima_mm21 = (
        pd.notna(mm21.iloc[-1])
        and preco_tecnico > mm21.iloc[-1]
    )

    macd_positivo = (
        pd.notna(macd_hist.iloc[-1])
        and macd_hist.iloc[-1] > 0
    )

    score += 1 if mm9_acima else -1
    score += 1 if preco_acima_mm21 else -1
    score += 1 if macd_positivo else -1

    if pd.notna(ret20):
        score += 1 if ret20 > 0 else -1

    vies = classificar_vies(score)
    confianca = classificar_confianca(
        score,
        vol_rel,
        ret20,
        ret60,
    )

    extensao = classificacao_extensao(
        rsi14,
        preco_tecnico,
        banda_inf,
        banda_sup,
    )

    return {
        "Ativo": ticker,
        "Tipo": TIPO_ATIVO.get(ticker, "-"),
        "Data": df.index.max().strftime("%d/%m/%Y"),
        "Fonte": fonte,
        "Preço": _fmt_num(preco, 2),
        "Ret. 20d %": _fmt_num(ret20, 2),
        "Ret. 60d %": _fmt_num(ret60, 2),
        "RSI14": _fmt_num(rsi14, 1),
        "MM9>MM21": "sim" if mm9_acima else "não",
        "Preço>MM21": "sim" if preco_acima_mm21 else "não",
        "MACD": "positivo" if macd_positivo else "negativo",
        "Bollinger": classificacao_bollinger(
            preco_tecnico,
            banda_inf,
            banda_sup,
        ),
        "Vol. rel.": _fmt_num(vol_rel, 2),
        "Score tendência": int(score),
        "Viés técnico": vies,
        "Confiança": confianca,
        "Extensão": extensao,
    }


# =============================================================================
# HTML
# =============================================================================

def cor_vies(vies: str) -> tuple[str, str]:
    mapa = {
        "FORTE POSITIVO": ("#DDEFD2", "#1F4B0B"),
        "POSITIVO": ("#EAF3DE", "#27500A"),
        "NEUTRO": ("#F1EFE8", "#444441"),
        "NEGATIVO": ("#FCEBEB", "#791F1F"),
        "FORTE NEGATIVO": ("#F7D6D6", "#611515"),
        "sem dados": ("#F1EFE8", "#888780"),
    }
    return mapa.get(vies, ("#F1EFE8", "#444441"))


def cor_confianca(confianca: str) -> str:
    return {
        "ALTA": "#185FA5",
        "MODERADA": "#8A5A00",
        "BAIXA": "#6B6A63",
    }.get(confianca, "#6B6A63")


def gerar_html(
    resultados,
    caminho: str = "painel_fiis.html",
) -> str:
    data = pd.Timestamp.now(tz="America/Sao_Paulo").strftime("%d/%m/%Y")
    linhas = []

    for r in resultados:
        vies = r.get("Viés técnico", "sem dados")
        bg, fg = cor_vies(vies)

        if "Preço" not in r:
            linhas.append(
                "<tr>"
                f'<td class="tk">{r["Ativo"]}</td>'
                f'<td><span class="tipo">{r.get("Tipo", "-")}</span></td>'
                '<td colspan="13" class="sem-dados">sem dados — ver checklist no terminal</td>'
                f'<td><span class="pill" style="background:{bg};color:{fg};">{vies}</span></td>'
                "</tr>"
            )
            continue

        ret20 = r["Ret. 20d %"]
        ret60 = r["Ret. 60d %"]
        rsi_valor = r["RSI14"]
        vol_rel = r["Vol. rel."]
        confianca = r["Confiança"]

        if _eh_numero(rsi_valor) and float(rsi_valor) >= 70:
            rsi_cor = "#791F1F"
        elif _eh_numero(rsi_valor) and float(rsi_valor) <= 30:
            rsi_cor = "#27500A"
        else:
            rsi_cor = "#444441"

        volume_peso = (
            "600"
            if _eh_numero(vol_rel) and float(vol_rel) >= 1.20
            else "400"
        )

        linhas.append(
            "<tr>"
            f'<td class="tk">{r["Ativo"]}</td>'
            f'<td><span class="tipo">{r["Tipo"]}</span></td>'
            f'<td>{r["Data"]}</td>'
            f'<td>R$ {float(r["Preço"]):.2f}</td>'
            f'<td style="color:{cor_retorno(ret20)};">{fmt_pct(ret20)}</td>'
            f'<td style="color:{cor_retorno(ret60)};">{fmt_pct(ret60)}</td>'
            f'<td style="color:{rsi_cor};font-weight:500;">{rsi_valor}</td>'
            f'<td>{"↑" if r["MM9>MM21"] == "sim" else "↓"} {r["MM9>MM21"]}</td>'
            f'<td>{"↑" if r["Preço>MM21"] == "sim" else "↓"} {r["Preço>MM21"]}</td>'
            f'<td>{r["MACD"]}</td>'
            f'<td>{r["Bollinger"]}</td>'
            f'<td style="font-weight:{volume_peso};">{vol_rel}</td>'
            f'<td style="text-align:center;">{r["Score tendência"]:+d}</td>'
            f'<td>{r["Extensão"]}</td>'
            f'<td style="color:{cor_confianca(confianca)};font-weight:600;">{confianca}</td>'
            f'<td><span class="pill" style="background:{bg};color:{fg};">{vies}</span></td>'
            "</tr>"
        )

    html = f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Painel FIIs — {data}</title>

<style>
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: #faf9f5;
    color: #1a1a18;
    margin: 0;
    padding: 20px;
  }}

  .wrap {{
    width: 100%;
    max-width: none;
    margin: 0 auto;
    box-sizing: border-box;
  }}

  h1 {{
    font-size: 22px;
    font-weight: 600;
    margin: 0 0 4px;
  }}

  .sub {{
    color: #6b6a63;
    font-size: 13px;
    margin: 0 0 20px;
  }}

  .table-wrap {{
    width: 100%;
    overflow-x: visible;
    border-radius: 12px;
    box-shadow: 0 1px 3px rgba(0,0,0,0.06);
  }}

  table {{
    width: 100%;
    min-width: 0;
    border-collapse: collapse;
    table-layout: auto;
    background: #fff;
    font-size: 11px;
  }}

  th {{
    text-align: left;
    padding: 10px 8px;
    background: #f1efe8;
    font-weight: 600;
    color: #444441;
    font-size: 9px;
    text-transform: uppercase;
    letter-spacing: 0.02em;
    white-space: nowrap;
  }}

  td {{
    padding: 10px 8px;
    border-top: 0.5px solid #eceae2;
    white-space: nowrap;
  }}

  @media (max-width: 1600px) {{
    body {{
      padding: 12px;
    }}

    table {{
      font-size: 10px;
    }}

    th {{
      font-size: 8px;
      padding: 8px 6px;
    }}

    td {{
      padding: 8px 6px;
    }}

    .pill {{
      padding: 3px 7px;
      font-size: 9px;
    }}

    .tipo {{
      font-size: 9px;
      padding: 2px 5px;
    }}
  }}

  @media (max-width: 1300px) {{
    table {{
      font-size: 9px;
    }}

    th {{
      font-size: 7.5px;
      padding: 7px 4px;
    }}

    td {{
      padding: 7px 4px;
    }}
  }}

  .tk {{
    font-weight: 700;
  }}

  .tipo {{
    font-size: 10px;
    color: #6b6a63;
    background: #f7f6f2;
    padding: 3px 7px;
    border-radius: 999px;
  }}

  .pill {{
    padding: 4px 10px;
    border-radius: 20px;
    font-size: 11px;
    font-weight: 700;
  }}

  .sem-dados {{
    color: #888780;
  }}

  .card {{
    background: #fff;
    border-radius: 12px;
    padding: 20px 24px;
    margin-top: 20px;
    box-shadow: 0 1px 3px rgba(0,0,0,0.06);
  }}

  .card h2 {{
    font-size: 14px;
    margin: 0 0 12px;
  }}

  .card p,
  .card li {{
    font-size: 12px;
    line-height: 1.55;
    color: #444441;
  }}

  .alerta {{
    background: #fff8e6;
    border: 1px solid #f0dfae;
  }}
</style>
</head>

<body>
<div class="wrap">

<h1>Painel técnico — FIIs e FI-Infra</h1>
<p class="sub">
  {data} · Yahoo Finance → brapi fallback · indicadores calculados sobre fechamento ajustado
</p>

<div class="table-wrap">
<table>
<thead>
<tr>
<th>Ativo</th>
<th>Tipo</th>
<th>Data</th>
<th>Preço</th>
<th>Ret. 20d</th>
<th>Ret. 60d</th>
<th>RSI 14</th>
<th>MM 9x21</th>
<th>Preço&gt;MM21</th>
<th>MACD</th>
<th>Bollinger</th>
<th>Vol. rel.</th>
<th>Score</th>
<th>Extensão</th>
<th>Confiança</th>
<th>Viés técnico</th>
</tr>
</thead>

<tbody>
{''.join(linhas)}
</tbody>
</table>
</div>

<div class="card">
<h2>Como o viés técnico é calculado</h2>
<p>
O score direcional usa quatro condições: MM9×MM21, preço versus MM21,
histograma do MACD e retorno de 20 pregões. Cada condição soma +1 ou −1.
RSI e Bollinger aparecem separadamente como medidores de extensão:
eles não transformam automaticamente uma tendência forte em venda nem
uma queda forte em compra.
</p>
<ul>
<li><strong>+3 a +4:</strong> forte positivo</li>
<li><strong>+1 a +2:</strong> positivo</li>
<li><strong>0:</strong> neutro</li>
<li><strong>−1 a −2:</strong> negativo</li>
<li><strong>−3 a −4:</strong> forte negativo</li>
</ul>
</div>

<div class="card">
<h2>Volume e confiança</h2>
<p>
O volume relativo compara o último pregão com a média dos 20 pregões
anteriores. Volume elevado aumenta a confiança quando confirma a direção,
mas não cria sozinho um sinal comprador ou vendedor.
</p>
</div>

<div class="card alerta">
<h2>Importante para FIIs</h2>
<p>
Este painel continua sendo técnico. Antes de qualquer decisão, cruze o
resultado com fundamentos adequados à estratégia do fundo: P/VP,
dividend yield sustentável, vacância, inadimplência, concentração,
alavancagem, duration, indexadores, qualidade de crédito, emissões e gestão.
FI-Infra deve ser analisado separadamente de FII imobiliário tradicional.
</p>
</div>

</div>
</body>
</html>
"""

    with open(caminho, "w", encoding="utf-8") as arquivo:
        arquivo.write(html)

    return os.path.abspath(caminho)


# =============================================================================
# Correlação
# =============================================================================

def coletar_series_retorno(
    period: str,
    include_today: bool = False,
) -> pd.DataFrame:
    """
    Calcula o retorno de cada ativo em sua própria série antes de concatenar.

    Isso evita que um dia ausente em um ativo contamine o cálculo dos demais.
    """
    series_retorno = {}

    for ticker in TICKERS:
        df, _ = obter_dados(
            ticker,
            period=period,
            include_today=include_today,
        )

        if df.empty:
            continue

        serie = pd.to_numeric(df["AdjClose"], errors="coerce").dropna()

        if len(serie) < MIN_OBSERVACOES:
            continue

        ret = np.log(serie / serie.shift(1)).dropna()
        ret.name = ticker
        series_retorno[ticker] = ret

    for nome, simbolo in CONTEXTO_YAHOO.items():
        try:
            df = baixar_yahoo_simbolo(
                simbolo,
                period=period,
                include_today=include_today,
            )

            if df.empty:
                print(f"  aviso: não consegui baixar {nome} ({simbolo})")
                continue

            serie = pd.to_numeric(df["AdjClose"], errors="coerce").dropna()
            ret = np.log(serie / serie.shift(1)).dropna()
            ret.name = nome
            series_retorno[nome] = ret
        except Exception:
            print(f"  aviso: não consegui baixar {nome} ({simbolo})")

    if not series_retorno:
        return pd.DataFrame()

    return pd.concat(series_retorno.values(), axis=1).sort_index()


def gerar_heatmap(
    retornos: pd.DataFrame,
    caminho: str = "fii_correlation.png",
    mostrar: bool = False,
) -> pd.DataFrame:
    import matplotlib

    if not mostrar:
        matplotlib.use("Agg")

    import matplotlib.pyplot as plt
    import seaborn as sns

    corr = retornos.corr(min_periods=MIN_OBSERVACOES)

    if corr.empty or corr.shape[1] < 2:
        print("  heatmap: dados insuficientes.")
        return pd.DataFrame()

    fig = plt.figure(figsize=(11.5, 9.5))

    mask = np.triu(
        np.ones_like(corr, dtype=bool),
        k=1,
    )

    sns.heatmap(
        corr,
        mask=mask,
        annot=True,
        fmt=".2f",
        cmap="RdBu_r",
        vmin=-1,
        vmax=1,
        center=0,
        square=True,
        linewidths=0.5,
        cbar_kws={
            "shrink": 0.8,
            "label": "Correlação de Pearson",
        },
        annot_kws={"size": 8},
    )

    plt.title(
        "Correlação entre retornos diários ajustados",
        fontsize=13,
        pad=14,
    )

    fig.text(
        0.5,
        0.02,
        "Correlação mede co-movimento, não qualidade, risco de crédito ou diversificação econômica.",
        ha="center",
        va="bottom",
        fontsize=8.5,
        color="#444441",
    )

    plt.tight_layout(rect=(0, 0.05, 1, 1))
    plt.savefig(caminho, dpi=150, bbox_inches="tight")

    print(f"  heatmap salvo: {os.path.abspath(caminho)}")

    if mostrar:
        plt.show()

    plt.close()
    return corr


def selecionar_pares_rolling(
    retornos: pd.DataFrame,
    max_pares: int = MAX_PARES_ROLLING,
):
    """
    Seleciona pares pela correlação absoluta mais recente, usando no máximo
    JANELA_SELECAO_PARES pregões. O par só é elegível se houver observações
    suficientes para a correlação móvel.
    """
    disponiveis = [tk for tk in FIIS if tk in retornos.columns]

    if len(disponiveis) < 2:
        return []

    candidatos = []

    for i, ativo_a in enumerate(disponiveis):
        for ativo_b in disponiveis[i + 1:]:
            par = retornos[[ativo_a, ativo_b]].dropna()

            if len(par) < JANELA_ROLLING:
                continue

            amostra = par.tail(JANELA_SELECAO_PARES)
            valor = amostra[ativo_a].corr(amostra[ativo_b])

            if pd.notna(valor):
                candidatos.append(
                    (
                        abs(float(valor)),
                        float(valor),
                        ativo_a,
                        ativo_b,
                    )
                )

    candidatos.sort(reverse=True, key=lambda item: item[0])
    return [(a, b) for _, _, a, b in candidatos[:max_pares]]


def gerar_rolling(
    retornos: pd.DataFrame,
    caminho: str = "rolling_fii_correlation.png",
    mostrar: bool = False,
) -> None:
    import matplotlib

    if not mostrar:
        matplotlib.use("Agg")

    import matplotlib.pyplot as plt

    pares = selecionar_pares_rolling(retornos)

    if not pares:
        print("  correlação móvel: nenhum par com dados suficientes.")
        return

    fig = plt.figure(figsize=(12, 7))
    linhas_plotadas = 0

    for ativo_a, ativo_b in pares:
        par = retornos[[ativo_a, ativo_b]].dropna()

        if len(par) < JANELA_ROLLING:
            continue

        roll = par[ativo_a].rolling(JANELA_ROLLING).corr(par[ativo_b])

        plt.plot(
            roll.index,
            roll,
            label=f"{ativo_a} × {ativo_b}",
            linewidth=1.5,
        )
        linhas_plotadas += 1

    if linhas_plotadas == 0:
        plt.close()
        print("  correlação móvel: nenhum par pôde ser plotado.")
        return

    plt.axhline(
        0,
        color="#888",
        linewidth=0.8,
        linestyle="--",
    )

    plt.axhspan(
        0.5,
        1,
        color="#EAF3DE",
        alpha=0.35,
        zorder=0,
    )

    plt.axhspan(
        -1,
        -0.5,
        color="#FCEBEB",
        alpha=0.35,
        zorder=0,
    )

    plt.ylim(-1, 1)

    plt.title(
        f"Correlação móvel de {JANELA_ROLLING} pregões — pares atualmente mais correlacionados",
        fontsize=13,
        pad=12,
    )

    plt.ylabel("Correlação")
    plt.legend(loc="best", fontsize=8, framealpha=0.9)
    plt.grid(alpha=0.2)

    fig.text(
        0.5,
        0.015,
        (
            f"Pares escolhidos pela maior correlação absoluta nos últimos "
            f"{JANELA_SELECAO_PARES} pregões disponíveis; "
            f"a linha usa janela móvel de {JANELA_ROLLING} pregões."
        ),
        ha="center",
        va="bottom",
        fontsize=8.5,
        color="#444441",
    )

    plt.tight_layout(rect=(0, 0.07, 1, 1))
    plt.savefig(caminho, dpi=150, bbox_inches="tight")

    print(f"  correlação móvel salva: {os.path.abspath(caminho)}")

    if mostrar:
        plt.show()

    plt.close()


def analise_correlacao(
    diretorio_saida: str,
    period: str,
    include_today: bool = False,
    mostrar: bool = False,
) -> None:
    print("\nColetando séries para correlação...")

    retornos = coletar_series_retorno(
        period=period,
        include_today=include_today,
    )

    if retornos.shape[1] < 2:
        print("  dados insuficientes para correlação.")
        return

    caminho_heatmap = os.path.join(
        diretorio_saida,
        "fii_correlation.png",
    )

    caminho_rolling = os.path.join(
        diretorio_saida,
        "rolling_fii_correlation.png",
    )

    corr = gerar_heatmap(
        retornos,
        caminho=caminho_heatmap,
        mostrar=mostrar,
    )

    gerar_rolling(
        retornos,
        caminho=caminho_rolling,
        mostrar=mostrar,
    )

    if not corr.empty:
        print("\nMatriz de correlação:")
        print(corr.round(2).to_string())


# =============================================================================
# CLI
# =============================================================================

def parse_args():
    parser = argparse.ArgumentParser(
        description="Painel técnico diário de FIIs e FI-Infra da B3.",
    )

    parser.add_argument(
        "--corr",
        action="store_true",
        help="gera heatmap e correlação móvel",
    )

    parser.add_argument(
        "--show",
        action="store_true",
        help="exibe os gráficos na tela; normalmente usado com --corr",
    )

    parser.add_argument(
        "--include-today",
        action="store_true",
        help="inclui o candle da data atual mesmo antes do horário de segurança",
    )

    parser.add_argument(
        "--period",
        default=PERIODO_DADOS,
        choices=[
            "1mo",
            "3mo",
            "6mo",
            "1y",
            "2y",
            "5y",
            "10y",
            "ytd",
            "max",
        ],
        help=f"janela histórica (padrão: {PERIODO_DADOS})",
    )

    parser.add_argument(
        "--output-dir",
        default=".",
        help="diretório-base onde a pasta da execução será criada",
    )

    parser.add_argument(
        "--no-open",
        action="store_true",
        help="não abre automaticamente o HTML no navegador",
    )

    return parser.parse_args()


def main() -> int:
    args = parse_args()

    checar_versao_yfinance()

    print("\nAnalisando ativos...")
    resultados = []

    for ticker in TICKERS:
        print(f"  {ticker}...", end=" ", flush=True)

        resultado = analisar(
            ticker,
            period=args.period,
            include_today=args.include_today,
        )

        resultados.append(resultado)
        print(resultado.get("Viés técnico", "-"))

    tabela = pd.DataFrame(resultados)

    print(
        "\nPainel técnico —",
        pd.Timestamp.now(tz="America/Sao_Paulo").strftime("%d/%m/%Y"),
    )
    print(tabela.to_string(index=False))

    diretorio_saida = criar_diretorio_execucao(args.output_dir)
    print(f"\nDiretório da execução: {diretorio_saida}")

    caminho_html = os.path.join(
        diretorio_saida,
        "painel_fiis.html",
    )

    caminho_csv = os.path.join(
        diretorio_saida,
        "painel_fiis.csv",
    )

    caminho_html = gerar_html(
        resultados,
        caminho=caminho_html,
    )

    tabela.to_csv(
        caminho_csv,
        index=False,
        encoding="utf-8-sig",
    )

    print(f"Painel visual gerado: {caminho_html}")
    print(f"CSV gerado: {caminho_csv}")

    if not args.no_open:
        try:
            webbrowser.open(Path(caminho_html).resolve().as_uri())
            print("Abrindo no navegador...")
        except Exception:
            print("Abra o arquivo HTML acima no navegador.")

    if "Fonte" in tabela.columns and (tabela["Fonte"] == "FALHOU").any():
        print(
            "\nAlguns ativos falharam. Checklist:"
            "\n 1. Confirme acesso à internet e ao Yahoo Finance."
            "\n 2. Atualize yfinance dentro do seu ambiente virtual."
            "\n 3. Opcional: configure BRAPI_TOKEN para o fallback."
            "\n 4. Teste sem VPN/rede corporativa se os provedores estiverem bloqueados."
        )

    print(
        "\nNotas metodológicas:"
        "\n - Retornos e indicadores usam fechamento ajustado quando disponível."
        "\n - O preço exibido é o fechamento bruto."
        "\n - RSI/Bollinger medem extensão e não geram compra/venda automaticamente."
        "\n - Volume aumenta a confiança, mas não define a direção."
        "\n - Ferramenta educativa — não é recomendação de investimento."
    )

    if args.corr:
        analise_correlacao(
            diretorio_saida,
            period=args.period,
            include_today=args.include_today,
            mostrar=args.show,
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
