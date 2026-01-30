"""
JERA PORTFOLIO ONBOARDING v14
=============================
Com OpenFIGI funcionando (sem exchCode)
+ Base local de ISINs como fallback
"""

import streamlit as st
import pandas as pd
import pdfplumber
import re
from datetime import datetime
import io
import tempfile
from pathlib import Path
import requests

st.set_page_config(
    page_title="Jera - Portfolio Onboarding",
    page_icon="📊",
    layout="wide"
)

# =============================================================================
# CONFIG
# =============================================================================

OPENFIGI_API_KEY = "01e15cc4-a2d8-47b6-8323-746430a85b52"
OPENFIGI_URL = "https://api.openfigi.com/v3/mapping"
ISIN_GITHUB_URL = "https://raw.githubusercontent.com/Felipemqn/jera-portfolio-onboarding/main/base_isin.xlsx"

OUTPUT_COLUMNS = [
    "Data", "Trading Desk", "ProductSource", "Classificação Investimentos",
    "Ativo", "Tipo Ativo", "CNPJ", "Ticker de Negociação", "ISIN",
    "Data inicio", "Data Venc", "Rentabilidade", "PU Emissão",
    "Portfolio Company", "Class/Series", "Moeda", "Status"
]

# =============================================================================
# OPENFIGI API (CORRIGIDO - SEM EXCHCODE!)
# =============================================================================

def search_openfigi(ticker: str) -> dict:
    """Busca no OpenFIGI - SEM exchCode funciona melhor para Brasil!"""
    if not ticker:
        return None
    
    headers = {
        "Content-Type": "application/json",
        "X-OPENFIGI-APIKEY": OPENFIGI_API_KEY
    }
    
    # NÃO usar exchCode - funciona melhor assim!
    payload = [{"idType": "TICKER", "idValue": ticker.upper()}]
    
    try:
        response = requests.post(OPENFIGI_URL, headers=headers, json=payload, timeout=10)
        
        if response.status_code == 200:
            result = response.json()[0]
            if "data" in result and result["data"]:
                item = result["data"][0]
                return {
                    "figi": item.get("figi"),
                    "name": item.get("name"),
                    "ticker": item.get("ticker"),
                    "exchCode": item.get("exchCode"),
                    "securityType": item.get("securityType"),
                    "marketSector": item.get("marketSector")
                }
    except Exception as e:
        pass
    
    return None

def search_openfigi_batch(tickers: list) -> dict:
    """Busca em lote no OpenFIGI"""
    if not tickers:
        return {}
    
    headers = {
        "Content-Type": "application/json",
        "X-OPENFIGI-APIKEY": OPENFIGI_API_KEY
    }
    
    # Criar payload SEM exchCode
    payload = [{"idType": "TICKER", "idValue": t.upper()} for t in tickers if t]
    
    if not payload:
        return {}
    
    try:
        response = requests.post(OPENFIGI_URL, headers=headers, json=payload, timeout=30)
        
        if response.status_code == 200:
            results = {}
            for i, result in enumerate(response.json()):
                ticker = tickers[i]
                if "data" in result and result["data"]:
                    item = result["data"][0]
                    results[ticker] = {
                        "figi": item.get("figi"),
                        "name": item.get("name"),
                        "ticker": item.get("ticker"),
                        "exchCode": item.get("exchCode"),
                        "securityType": item.get("securityType")
                    }
            return results
    except Exception as e:
        pass
    
    return {}

# =============================================================================
# BASE LOCAL DE ISINs (FALLBACK)
# =============================================================================

@st.cache_data(ttl=300, show_spinner=False)
def load_isin_database():
    try:
        df = pd.read_excel(ISIN_GITHUB_URL)
        return df, "GitHub"
    except:
        return pd.DataFrame(columns=["Tipo", "Ticker", "Nome", "ISIN"]), "Vazio"

def find_isin_local(ticker: str = None, nome: str = None, isin_df: pd.DataFrame = None) -> str:
    """Busca ISIN na base local (fallback)"""
    if isin_df is None or isin_df.empty:
        return None
    
    if ticker:
        match = isin_df[isin_df["Ticker"].str.upper() == ticker.upper()]
        if not match.empty:
            return match.iloc[0]["ISIN"]
    
    return None

# =============================================================================
# CVM DATA
# =============================================================================

@st.cache_data(ttl=3600, show_spinner=False)
def load_cvm_data():
    try:
        url = "https://dados.cvm.gov.br/dados/FI/CAD/DADOS/cad_fi.csv"
        df = pd.read_csv(url, sep=";", encoding="latin1", low_memory=False)
        return df
    except:
        return None

def find_cnpj_cvm(fund_name: str, cvm_df: pd.DataFrame, used_cnpjs: set = None, limit: int = 5) -> list:
    if cvm_df is None or cvm_df.empty:
        return []
    
    if used_cnpjs is None:
        used_cnpjs = set()
    
    name_upper = fund_name.upper().strip()
    name_clean = re.sub(r'\s*%.*$', '', name_upper)
    name_clean = re.sub(r'\s+\d+\.\d+.*$', '', name_clean)
    
    noise_words = {
        "FIM", "FIC", "FIDC", "FIP", "FIF", "FCFM", "FFIM", "FIQ", "FICFIM",
        "FC", "FM", "CP", "RF", "FI", "SUB", "FEE", "AB", "MU", "Z", 
        "II", "III", "I", "IV", "V", "PF", "ML", "BR", "CL", "T5", 
        "REF", "OVER", "IPCA", "PRE", "D", "LEG", "ALL", "MAR", "DEB",
        "DE", "EM", "COTAS", "FUNDO", "INVESTIMENTO", "MULTIMERCADO",
        "CREDITO", "PRIVADO", "RENDA", "FIXA"
    }
    
    words = [w for w in name_clean.split() if w not in noise_words and len(w) >= 2]
    
    if not words:
        return []
    
    candidates = cvm_df.copy()
    matched_count = 0
    
    for word in words:
        new_candidates = candidates[
            candidates["DENOM_SOCIAL"].str.upper().str.contains(word, na=False, regex=False)
        ]
        if len(new_candidates) > 0:
            candidates = new_candidates
            matched_count += 1
        if len(candidates) <= limit * 2:
            break
    
    if len(candidates) == 0:
        return []
    
    if used_cnpjs:
        candidates = candidates[~candidates["CNPJ_FUNDO"].isin(used_cnpjs)]
    
    candidates["is_ativo"] = candidates["SIT"].str.contains("FUNCIONAMENTO", na=False)
    candidates = candidates.sort_values("is_ativo", ascending=False)
    
    results = []
    for _, row in candidates.head(limit).iterrows():
        score = matched_count / len(words) if words else 0
        results.append({
            "cnpj": str(row["CNPJ_FUNDO"]),
            "nome": str(row["DENOM_SOCIAL"]),
            "situacao": str(row["SIT"]),
            "score": score,
            "ativo": "FUNCIONAMENTO" in str(row["SIT"])
        })
    
    return results

# =============================================================================
# CLASSIFICAÇÃO
# =============================================================================

def classify_asset(nome: str) -> tuple:
    nome_upper = nome.upper()
    nome_clean = re.sub(r'\s*%.*$', '', nome_upper)
    
    tipo_ativo = "Fundo"
    
    if any(x in nome_clean for x in ["LFT", "LTN", "NTN", "TESOURO"]) and not any(x in nome_clean for x in ["FI ", "FIM", "FIC"]):
        tipo_ativo = "Tit Publico"
    elif re.search(r'\bCRI\b', nome_clean):
        tipo_ativo = "CRI"
    elif re.search(r'\bCRA\b', nome_clean):
        tipo_ativo = "CRA"
    elif re.search(r'\bDEB\b', nome_clean) or "DEBENTURE" in nome_clean:
        tipo_ativo = "Debenture"
    elif re.search(r'[A-Z]{4}11\b', nome_clean):
        tipo_ativo = "FII"
    elif any(x in nome_clean for x in ["LCI", "LCA", "CDB"]):
        tipo_ativo = "RF Bancario"
    
    classificacao = "Retorno Absoluto Brasil"
    
    if any(x in nome_clean for x in ["SELIC", "LFT", "LIQUIDEZ", "CAIXA", "CASH"]):
        classificacao = "Liquidez CDI"
    elif "CDI" in nome_clean and tipo_ativo not in ["Tit Publico"]:
        classificacao = "Liquidez CDI"
    elif any(x in nome_clean for x in ["LTN"]) and tipo_ativo == "Tit Publico":
        classificacao = "Renda Fixa Brasil Pré-Fixado"
    elif any(x in nome_clean for x in ["PRE", "PREFIXADO"]) and tipo_ativo in ["CRI", "Debenture"]:
        classificacao = "Renda Fixa Brasil Pré-Fixado"
    elif any(x in nome_clean for x in ["NTN", "IPCA", "INFLACAO", "IMA-B"]):
        classificacao = "Renda Fixa Brasil Inflação"
    elif any(x in nome_clean for x in ["CRED", "FIDC", "HIGH YIELD"]) or tipo_ativo in ["CRI", "CRA", "Debenture"]:
        classificacao = "Renda Fixa Brasil Crédito Pós-Fixado"
    elif any(x in nome_clean for x in ["ACAO", "ACOES", "EQUITY", "LONG", "FIA", "IBOV"]):
        classificacao = "Renda Variável Brasil"
    elif any(x in nome_clean for x in ["IMOB", "REAL ESTATE"]) or tipo_ativo == "FII":
        classificacao = "Real Estate Brasil"
    elif any(x in nome_clean for x in ["FIP", "PRIVATE EQUITY", "VENTURE", "TRATOR", "TERRAS"]):
        classificacao = "Private Equity Brasil"
    
    return tipo_ativo, classificacao

def extract_ticker(nome: str) -> str:
    nome_upper = nome.upper()
    fii_match = re.search(r'\b([A-Z]{4}11)\b', nome_upper)
    if fii_match:
        return fii_match.group(1)
    deb_match = re.search(r'\b([A-Z]{4}\d{2})\b', nome_upper)
    if deb_match:
        return deb_match.group(1)
    return None

def extract_vencimento(nome: str) -> str:
    match = re.search(r'(\d{2}/\d{2}/\d{4})', nome)
    if match:
        return match.group(1)
    return None

# =============================================================================
# BTG PDF PARSER
# =============================================================================

def parse_btg_pdf(pdf_file, cvm_df: pd.DataFrame, isin_df: pd.DataFrame, use_openfigi: bool = True) -> tuple:
    assets = []
    metadata = {"fund_name": "", "nav": None}
    used_cnpjs = set()
    
    with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as tmp:
        tmp.write(pdf_file.read())
        tmp_path = tmp.name
    
    try:
        with pdfplumber.open(tmp_path) as pdf:
            all_text = ""
            for page in pdf.pages:
                all_text += (page.extract_text() or "") + "\n"
            
            match = re.search(r"(FI\s+MULT[^\n]+)", all_text)
            if match:
                metadata["fund_name"] = match.group(1).strip()
            
            match = re.search(r"Patrimônio\s+([\d.,]+)", all_text)
            if match:
                try:
                    metadata["nav"] = float(match.group(1).replace(".", "").replace(",", "."))
                except:
                    pass
            
            # TÍTULOS PÚBLICOS com vencimento
            titulo_pattern = r"(\d+)-\s*(LFT|LTN|NTNB|NTN-?B)[A-Z\s]*\s+(\d+\.\d{2})\s+([\d,]+\.\d{2})\s+[\d,]+\s+[\d.,]+\s+(\d{2}/\d{2}/\d{4})"
            
            for match in re.finditer(titulo_pattern, all_text):
                tipo = match.group(2).upper().replace("-", "")
                pct_pl = float(match.group(3))
                value = float(match.group(4).replace(",", ""))
                vencimento = match.group(5)
                
                nome = f"{tipo} {vencimento}"
                
                assets.append({
                    "nome": nome,
                    "tipo_ativo": "Tit Publico",
                    "classificacao": "Renda Fixa Brasil Inflação" if "NTN" in tipo else ("Liquidez CDI" if "LFT" in tipo else "Renda Fixa Brasil Pré-Fixado"),
                    "pct_pl": pct_pl,
                    "value": value,
                    "cnpj": None,
                    "ticker": tipo,
                    "isin": None,
                    "vencimento": vencimento,
                    "status": "Verificar"
                })
            
            # FUNDOS e outros ativos
            asset_pattern = r"(\d+)-\s*([A-Z][A-Z0-9\s\-\+\%\,\.]+?)\s+(\d+\.\d{2})\s+([\d,]+\.\d{2})"
            
            for match in re.finditer(asset_pattern, all_text):
                nome = match.group(2).strip()
                pct_pl = float(match.group(3))
                value = float(match.group(4).replace(",", ""))
                
                if any(a["nome"] in nome or nome in a["nome"] for a in assets if a["tipo_ativo"] == "Tit Publico"):
                    continue
                
                if len(nome) < 3 or pct_pl > 100:
                    continue
                
                tipo_ativo, classificacao = classify_asset(nome)
                
                cnpj = None
                score = 0
                if tipo_ativo == "Fundo":
                    matches = find_cnpj_cvm(nome, cvm_df, used_cnpjs, limit=1)
                    if matches:
                        cnpj = matches[0]["cnpj"]
                        score = matches[0]["score"]
                        used_cnpjs.add(cnpj)
                
                ticker = extract_ticker(nome)
                vencimento = extract_vencimento(nome)
                
                # Status inicial
                if tipo_ativo == "Fundo":
                    status = "OK" if cnpj and score >= 0.5 else ("Verificar" if cnpj else "Pendente")
                elif ticker:
                    status = "Verificar"
                else:
                    status = "Pendente"
                
                assets.append({
                    "nome": nome,
                    "tipo_ativo": tipo_ativo,
                    "classificacao": classificacao,
                    "pct_pl": pct_pl,
                    "value": value,
                    "cnpj": cnpj,
                    "ticker": ticker,
                    "isin": None,
                    "vencimento": vencimento,
                    "status": status
                })
            
            # Remover duplicados
            seen = set()
            unique = []
            for a in assets:
                key = f"{a['nome']}_{a['value']}"
                if key not in seen:
                    seen.add(key)
                    unique.append(a)
            assets = unique
            
            # ENRIQUECER COM OPENFIGI
            if use_openfigi:
                tickers_to_search = [a["ticker"] for a in assets if a["ticker"] and a["tipo_ativo"] in ["FII", "Debenture", "Ação"]]
                
                if tickers_to_search:
                    figi_results = search_openfigi_batch(tickers_to_search)
                    
                    for asset in assets:
                        ticker = asset.get("ticker")
                        if ticker and ticker in figi_results:
                            figi_data = figi_results[ticker]
                            asset["figi"] = figi_data.get("figi")
                            asset["figi_name"] = figi_data.get("name")
                            if asset["status"] == "Verificar":
                                asset["status"] = "OK"
            
            # FALLBACK: Base local de ISINs
            for asset in assets:
                if not asset.get("isin"):
                    ticker = asset.get("ticker")
                    if ticker:
                        local_isin = find_isin_local(ticker=ticker, isin_df=isin_df)
                        if local_isin:
                            asset["isin"] = local_isin
    
    finally:
        Path(tmp_path).unlink(missing_ok=True)
    
    return assets, metadata

def convert_to_output_format(assets: list, metadata: dict, data_ref: str, trading_desk: str) -> pd.DataFrame:
    rows = []
    product_source = metadata.get("fund_name", "")
    
    for asset in assets:
        row = {
            "Data": data_ref,
            "Trading Desk": trading_desk,
            "ProductSource": product_source,
            "Classificação Investimentos": asset["classificacao"],
            "Ativo": asset["nome"],
            "Tipo Ativo": asset["tipo_ativo"],
            "CNPJ": asset.get("cnpj"),
            "Ticker de Negociação": asset.get("ticker"),
            "ISIN": asset.get("isin"),
            "Data inicio": None,
            "Data Venc": asset.get("vencimento"),
            "Rentabilidade": None,
            "PU Emissão": None,
            "Portfolio Company": None,
            "Class/Series": None,
            "Moeda": "BRL",
            "Status": asset.get("status", "Pendente")
        }
        rows.append(row)
    
    return pd.DataFrame(rows, columns=OUTPUT_COLUMNS)

# =============================================================================
# STREAMLIT UI
# =============================================================================

def main():
    st.title("📊 Jera Portfolio Onboarding")
    
    # Carregar dados
    with st.spinner("📚 Carregando bases..."):
        cvm_df = load_cvm_data()
        isin_df, isin_source = load_isin_database()
    
    # Tabs
    tab1, tab2, tab3 = st.tabs(["📄 Upload PDF", "🔍 Consulta Individual", "📋 Base de ISINs"])
    
    # =================================================================
    # TAB 1: UPLOAD PDF
    # =================================================================
    with tab1:
        st.markdown("**Processa extrato BTG**")
        
        col_upload, col_config = st.columns([2, 1])
        
        with col_config:
            st.markdown("**Configurações:**")
            data_ref = st.date_input("Data Referência", value=datetime.now(), key="pdf_data")
            trading_desk = st.text_input("Trading Desk", value="", key="pdf_desk")
            use_openfigi = st.checkbox("🌐 Usar OpenFIGI", value=True, help="Buscar FIGIs para FIIs e debêntures")
        
        with col_upload:
            uploaded_file = st.file_uploader("Arraste o PDF aqui", type=['pdf'])
        
        if uploaded_file:
            with st.spinner("🔄 Processando PDF..."):
                assets, metadata = parse_btg_pdf(uploaded_file, cvm_df, isin_df, use_openfigi)
            
            if not assets:
                st.error("❌ Não foi possível extrair ativos")
                return
            
            if not trading_desk and metadata.get("fund_name"):
                trading_desk = metadata["fund_name"]
            
            st.success(f"✅ **{len(assets)} ativos** extraídos")
            
            # Stats OpenFIGI
            if use_openfigi:
                figi_count = sum(1 for a in assets if a.get("figi"))
                if figi_count > 0:
                    st.info(f"🌐 OpenFIGI: {figi_count} ativos identificados")
            
            df_output = convert_to_output_format(
                assets, metadata,
                data_ref.strftime("%Y-%m-%d"),
                trading_desk
            )
            
            # Stats
            col1, col2, col3, col4 = st.columns(4)
            col1.metric("Total", len(df_output))
            col2.metric("✅ OK", len(df_output[df_output["Status"] == "OK"]))
            col3.metric("🟡 Verificar", len(df_output[df_output["Status"] == "Verificar"]))
            col4.metric("🔴 Pendente", len(df_output[df_output["Status"] == "Pendente"]))
            
            # Tabela editável
            st.subheader("📝 Ativos (editável)")
            edited_df = st.data_editor(
                df_output, 
                use_container_width=True, 
                hide_index=True, 
                num_rows="dynamic",
                column_config={
                    "ISIN": st.column_config.TextColumn("ISIN", help="Preencha manualmente se necessário"),
                    "Status": st.column_config.SelectboxColumn("Status", options=["OK", "Verificar", "Pendente"]),
                }
            )
            
            # Export
            st.subheader("💾 Exportar")
            col1, col2 = st.columns(2)
            with col1:
                buffer = io.BytesIO()
                with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
                    edited_df.to_excel(writer, sheet_name='Ativos Locais', index=False)
                buffer.seek(0)
                st.download_button("📥 Baixar Excel", data=buffer,
                    file_name=f"Cadastro_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            with col2:
                st.download_button("📥 Baixar JSON",
                    data=edited_df.to_json(orient="records", force_ascii=False, indent=2),
                    file_name=f"cadastro_{datetime.now().strftime('%Y%m%d_%H%M')}.json",
                    mime="application/json")
    
    # =================================================================
    # TAB 2: CONSULTA INDIVIDUAL
    # =================================================================
    with tab2:
        st.markdown("**Consulta um ativo específico**")
        
        nome_ativo = st.text_input(
            "Nome ou Ticker do Ativo",
            placeholder="Ex: SPX CAPITAL PLUS FIQ, PRLK11, HGLG11, PETR4...",
            key="consulta_nome"
        )
        
        if nome_ativo:
            st.divider()
            
            tipo_ativo, classificacao = classify_asset(nome_ativo)
            ticker = extract_ticker(nome_ativo) or nome_ativo.upper().strip()
            
            col1, col2, col3 = st.columns(3)
            col1.metric("Tipo Ativo", tipo_ativo)
            col2.metric("Classificação", classificacao)
            col3.metric("Ticker", ticker)
            
            st.divider()
            
            # Busca OpenFIGI
            st.markdown("### 🌐 OpenFIGI")
            with st.spinner("Buscando no OpenFIGI..."):
                figi_result = search_openfigi(ticker)
            
            if figi_result:
                st.success("✅ Encontrado no OpenFIGI!")
                
                col1, col2 = st.columns(2)
                with col1:
                    st.markdown(f"**FIGI:** `{figi_result.get('figi')}`")
                    st.markdown(f"**Nome:** {figi_result.get('name')}")
                with col2:
                    st.markdown(f"**Ticker:** {figi_result.get('ticker')}")
                    st.markdown(f"**Exchange:** {figi_result.get('exchCode')}")
                    st.markdown(f"**Tipo:** {figi_result.get('securityType')}")
            else:
                st.warning("⚠️ Não encontrado no OpenFIGI")
            
            # Busca base local
            st.markdown("### 📋 Base Local")
            local_isin = find_isin_local(ticker=ticker, isin_df=isin_df)
            if local_isin:
                st.success(f"✅ ISIN encontrado: `{local_isin}`")
            else:
                st.info("ℹ️ Não encontrado na base local")
            
            # Busca CVM para fundos
            if tipo_ativo == "Fundo":
                st.markdown("### 🏦 CVM (CNPJ)")
                
                if cvm_df is not None:
                    with st.spinner("Buscando na CVM..."):
                        matches = find_cnpj_cvm(nome_ativo, cvm_df, limit=5)
                    
                    if matches:
                        st.success(f"✅ {len(matches)} resultado(s)")
                        for i, match in enumerate(matches):
                            status_icon = "🟢" if match["ativo"] else "🔴"
                            score_pct = int(match["score"] * 100)
                            with st.expander(f"{status_icon} {match['cnpj']} ({score_pct}%)", expanded=(i==0)):
                                st.markdown(f"**Nome:** {match['nome']}")
                                st.code(match['cnpj'])
                    else:
                        st.warning("⚠️ Não encontrado na CVM")
    
    # =================================================================
    # TAB 3: BASE DE ISINs
    # =================================================================
    with tab3:
        st.markdown("### 📋 Base Local de ISINs (Fallback)")
        
        st.info(f"""
        **Fonte:** {isin_source} | **Registros:** {len(isin_df)}
        
        Esta base é usada como fallback quando o OpenFIGI não encontra o ativo.
        """)
        
        if not isin_df.empty:
            tipos = ["Todos"] + list(isin_df["Tipo"].unique())
            tipo_filtro = st.selectbox("Filtrar:", tipos)
            
            if tipo_filtro != "Todos":
                df_show = isin_df[isin_df["Tipo"] == tipo_filtro]
            else:
                df_show = isin_df
            
            st.dataframe(df_show, use_container_width=True, hide_index=True)
        
        # Download
        if not isin_df.empty:
            buffer = io.BytesIO()
            with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
                isin_df.to_excel(writer, sheet_name='ISINs', index=False)
            buffer.seek(0)
            st.download_button("📥 Baixar base_isin.xlsx", data=buffer, file_name="base_isin.xlsx")
    
    # Sidebar
    with st.sidebar:
        st.markdown("### 📊 Status")
        
        if cvm_df is not None:
            st.success(f"✅ CVM: {len(cvm_df):,} fundos")
        else:
            st.error("❌ CVM indisponível")
        
        # Testar OpenFIGI
        test = search_openfigi("PETR4")
        if test:
            st.success("✅ OpenFIGI: conectado")
        else:
            st.warning("⚠️ OpenFIGI: verificar")
        
        if not isin_df.empty:
            st.success(f"✅ ISINs: {len(isin_df)} ({isin_source})")
        
        st.divider()
        st.markdown("""
        ### 🔍 Fontes de dados
        
        1. **OpenFIGI** - FIIs, ações, debêntures
        2. **CVM** - CNPJ de fundos
        3. **Base local** - ISINs (fallback)
        """)

if __name__ == "__main__":
    main()
