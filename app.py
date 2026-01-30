"""
JERA PORTFOLIO ONBOARDING v11
=============================
Com OpenFIGI API para busca de ISIN/identificadores
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
import json

st.set_page_config(
    page_title="Jera - Portfolio Onboarding",
    page_icon="📊",
    layout="wide"
)

# =============================================================================
# APIs CONFIG
# =============================================================================

OPENFIGI_API_KEY = "01e15cc4-a2d8-47b6-8323-746430a85b52"
OPENFIGI_URL = "https://api.openfigi.com/v3/mapping"

# =============================================================================
# FORMATO DE SAÍDA
# =============================================================================

OUTPUT_COLUMNS = [
    "Data", "Trading Desk", "ProductSource", "Classificação Investimentos",
    "Ativo", "Tipo Ativo", "CNPJ", "Ticker de Negociação", "ISIN",
    "Data inicio", "Data Venc", "Rentabilidade", "PU Emissão",
    "Portfolio Company", "Class/Series", "Moeda", "Status"
]

# =============================================================================
# OPENFIGI API
# =============================================================================

def search_openfigi(query: str, id_type: str = "NAME", exchange: str = None) -> list:
    """
    Busca no OpenFIGI
    id_type: NAME, TICKER, ISIN, CUSIP, SEDOL, etc.
    """
    headers = {
        "Content-Type": "application/json",
        "X-OPENFIGI-APIKEY": OPENFIGI_API_KEY
    }
    
    payload = {"query": query, "idType": id_type}
    
    if exchange:
        payload["exchCode"] = exchange
    
    # Para ativos brasileiros
    if not exchange:
        payload["exchCode"] = "BZ"  # Brazil
    
    try:
        response = requests.post(
            OPENFIGI_URL,
            headers=headers,
            json=[payload],
            timeout=10
        )
        
        if response.status_code == 200:
            data = response.json()
            if data and len(data) > 0 and "data" in data[0]:
                return data[0]["data"]
        return []
    except Exception as e:
        st.warning(f"Erro OpenFIGI: {e}")
        return []

def search_openfigi_batch(queries: list) -> dict:
    """
    Busca em lote no OpenFIGI
    queries: lista de dicts {"query": "...", "idType": "..."}
    """
    headers = {
        "Content-Type": "application/json",
        "X-OPENFIGI-APIKEY": OPENFIGI_API_KEY
    }
    
    # Adicionar exchange Brasil para todas
    for q in queries:
        if "exchCode" not in q:
            q["exchCode"] = "BZ"
    
    try:
        response = requests.post(
            OPENFIGI_URL,
            headers=headers,
            json=queries,
            timeout=30
        )
        
        if response.status_code == 200:
            return response.json()
        return []
    except Exception as e:
        return []

# =============================================================================
# CVM DATA
# =============================================================================

@st.cache_data(ttl=3600, show_spinner=False)
def load_cvm_data():
    try:
        url = "https://dados.cvm.gov.br/dados/FI/CAD/DADOS/cad_fi.csv"
        df = pd.read_csv(url, sep=";", encoding="latin1", low_memory=False)
        return df
    except Exception as e:
        return None

def find_cnpj_cvm(fund_name: str, cvm_df: pd.DataFrame, used_cnpjs: set = None, limit: int = 5) -> list:
    """Busca CNPJ na base da CVM"""
    if cvm_df is None or cvm_df.empty:
        return []
    
    if used_cnpjs is None:
        used_cnpjs = set()
    
    name_upper = fund_name.upper().strip()
    
    noise_words = {
        "FIM", "FIC", "FIDC", "FIP", "FIF", "FCFM", "FFIM", "FIQ", "FICFIM",
        "FC", "FM", "CP", "RF", "FI", "SUB", "FEE", "AB", "MU", "Z", 
        "II", "III", "I", "IV", "V", "PF", "ML", "BR", "CL", "T5", 
        "REF", "OVER", "IPCA", "PRE", "D", "LEG", "ALL", "MAR", "DEB",
        "DE", "EM", "COTAS", "FUNDO", "INVESTIMENTO", "MULTIMERCADO",
        "CREDITO", "PRIVADO", "RENDA", "FIXA"
    }
    
    words = [w for w in name_upper.split() if w not in noise_words and len(w) >= 2]
    
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
# CLASSIFICAÇÃO DINÂMICA
# =============================================================================

def classify_asset(nome: str) -> tuple:
    nome_upper = nome.upper()
    
    tipo_ativo = "Fundo"
    
    if any(x in nome_upper for x in ["LFT", "LTN", "NTN", "TESOURO"]):
        tipo_ativo = "Tit Publico"
    elif any(x in nome_upper for x in ["CRI ", "CRI-", " CRI"]):
        tipo_ativo = "CRI"
    elif any(x in nome_upper for x in ["CRA ", "CRA-", " CRA"]):
        tipo_ativo = "CRA"
    elif any(x in nome_upper for x in ["DEB ", "DEBENTURE"]):
        tipo_ativo = "Debenture"
    elif re.search(r"[A-Z]{4}11", nome_upper):
        tipo_ativo = "FII"
    elif any(x in nome_upper for x in ["LCI", "LCA", "CDB"]):
        tipo_ativo = "RF Bancario"
    
    classificacao = "Retorno Absoluto Brasil"
    
    if any(x in nome_upper for x in ["SELIC", "LFT", "CDI", "LIQUIDEZ", "CAIXA", "CASH"]):
        classificacao = "Liquidez CDI"
    elif any(x in nome_upper for x in ["LTN", "PRE", "PREFIXADO"]):
        classificacao = "Renda Fixa Brasil Pré-Fixado"
    elif any(x in nome_upper for x in ["NTN", "IPCA", "INFLACAO", "IMA-B"]):
        classificacao = "Renda Fixa Brasil Inflação"
    elif any(x in nome_upper for x in ["CRED", "FIDC", "HIGH YIELD", "CRI", "CRA", "DEB"]):
        classificacao = "Renda Fixa Brasil Crédito Pós-Fixado"
    elif any(x in nome_upper for x in ["ACAO", "ACOES", "EQUITY", "LONG", "FIA", "IBOV"]):
        classificacao = "Renda Variável Brasil"
    elif any(x in nome_upper for x in ["IMOB", "FII", "REAL ESTATE"]) or re.search(r"[A-Z]{4}11", nome_upper):
        classificacao = "Real Estate Brasil"
    elif any(x in nome_upper for x in ["FIP", "PRIVATE EQUITY", "VENTURE"]):
        classificacao = "Private Equity Brasil"
    
    return tipo_ativo, classificacao

def extract_ticker(nome: str) -> str:
    fii_match = re.search(r"([A-Z]{4}11)", nome.upper())
    if fii_match:
        return fii_match.group(1)
    
    deb_match = re.search(r"([A-Z]{4}\d{2})", nome.upper())
    if deb_match:
        return deb_match.group(1)
    
    return None

def extract_vencimento(nome: str) -> str:
    match = re.search(r"(\d{2}/\d{2}/\d{4})", nome)
    if match:
        return match.group(1)
    return None

# =============================================================================
# BTG PDF PARSER
# =============================================================================

def parse_btg_pdf(pdf_file, cvm_df: pd.DataFrame) -> tuple:
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
            
            asset_pattern = r"(\d+)-\s*([A-Z][A-Z0-9\s\-\+\%\,\.]+?)\s+(\d+\.\d{2})\s+([\d,]+\.\d{2})"
            
            for match in re.finditer(asset_pattern, all_text):
                nome = match.group(2).strip()
                pct_pl = float(match.group(3))
                value = float(match.group(4).replace(",", ""))
                
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
                
                if tipo_ativo == "Fundo":
                    status = "OK" if cnpj and score >= 0.5 else ("Verificar" if cnpj else "Pendente")
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
            
            seen = set()
            unique = []
            for a in assets:
                key = f"{a['nome']}_{a['value']}"
                if key not in seen:
                    seen.add(key)
                    unique.append(a)
            assets = unique
    
    finally:
        Path(tmp_path).unlink(missing_ok=True)
    
    return assets, metadata

def enrich_with_openfigi(assets: list) -> list:
    """Enriquece ativos com dados do OpenFIGI"""
    # Preparar queries para títulos e FIIs
    queries = []
    query_map = {}  # índice -> asset index
    
    for i, asset in enumerate(assets):
        ticker = asset.get("ticker")
        nome = asset.get("nome", "")
        tipo = asset.get("tipo_ativo", "")
        
        if tipo == "Tit Publico":
            # Buscar por nome
            queries.append({"query": nome, "idType": "NAME"})
            query_map[len(queries)-1] = i
        elif ticker:
            # Buscar por ticker
            queries.append({"query": ticker, "idType": "TICKER"})
            query_map[len(queries)-1] = i
    
    if not queries:
        return assets
    
    # Fazer busca em lote
    results = search_openfigi_batch(queries)
    
    # Processar resultados
    for q_idx, result in enumerate(results):
        if q_idx in query_map and "data" in result and result["data"]:
            asset_idx = query_map[q_idx]
            figi_data = result["data"][0]
            
            # Extrair ISIN se disponível
            if "shareClassFIGI" in figi_data:
                assets[asset_idx]["figi"] = figi_data.get("figi")
            
            # Atualizar ticker se não tinha
            if not assets[asset_idx].get("ticker") and figi_data.get("ticker"):
                assets[asset_idx]["ticker"] = figi_data.get("ticker")
            
            # Atualizar status
            if assets[asset_idx]["status"] == "Pendente":
                assets[asset_idx]["status"] = "Verificar"
    
    return assets

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
    with st.spinner("📚 Carregando bases de dados..."):
        cvm_df = load_cvm_data()
    
    # Tabs
    tab1, tab2 = st.tabs(["📄 Upload PDF", "🔍 Consulta Individual"])
    
    # =================================================================
    # TAB 1: UPLOAD PDF
    # =================================================================
    with tab1:
        st.markdown("**Processa extrato BTG completo**")
        
        col_upload, col_config = st.columns([2, 1])
        
        with col_config:
            st.markdown("**Configurações:**")
            data_ref = st.date_input("Data Referência", value=datetime.now(), key="pdf_data")
            trading_desk = st.text_input("Trading Desk", value="", key="pdf_desk")
            use_openfigi = st.checkbox("Usar OpenFIGI", value=True, help="Buscar dados adicionais no OpenFIGI")
        
        with col_upload:
            uploaded_file = st.file_uploader("Arraste o PDF aqui", type=['pdf'])
        
        if uploaded_file:
            with st.spinner("🔄 Processando PDF..."):
                assets, metadata = parse_btg_pdf(uploaded_file, cvm_df)
            
            if not assets:
                st.error("❌ Não foi possível extrair ativos")
                return
            
            # Enriquecer com OpenFIGI
            if use_openfigi:
                with st.spinner("🔍 Buscando no OpenFIGI..."):
                    assets = enrich_with_openfigi(assets)
            
            if not trading_desk and metadata.get("fund_name"):
                trading_desk = metadata["fund_name"]
            
            st.success(f"✅ **{len(assets)} ativos** extraídos")
            
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
            
            # Tabela
            edited_df = st.data_editor(df_output, use_container_width=True, hide_index=True, num_rows="dynamic")
            
            # Export
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
        
        col1, col2 = st.columns([3, 1])
        
        with col1:
            nome_ativo = st.text_input(
                "Nome do Ativo",
                placeholder="Ex: SPX CAPITAL PLUS FIQ, NTNB 15/08/2050, RBRY11...",
                key="consulta_nome"
            )
        
        with col2:
            search_type = st.selectbox(
                "Tipo de Busca",
                ["Auto", "Nome", "Ticker", "ISIN", "CNPJ"],
                key="search_type"
            )
        
        if nome_ativo:
            st.divider()
            
            # Classificar
            tipo_ativo, classificacao = classify_asset(nome_ativo)
            ticker = extract_ticker(nome_ativo)
            vencimento = extract_vencimento(nome_ativo)
            
            # Exibir classificação
            col1, col2, col3 = st.columns(3)
            col1.metric("Tipo Ativo", tipo_ativo)
            col2.metric("Classificação", classificacao)
            if ticker:
                col3.metric("Ticker", ticker)
            elif vencimento:
                col3.metric("Vencimento", vencimento)
            
            st.divider()
            
            # ===== BUSCA CVM (Fundos) =====
            if tipo_ativo == "Fundo":
                st.markdown("### 🏦 Busca na CVM")
                
                if cvm_df is not None:
                    with st.spinner("Buscando na CVM..."):
                        matches = find_cnpj_cvm(nome_ativo, cvm_df, limit=10)
                    
                    if matches:
                        st.success(f"✅ {len(matches)} resultado(s) na CVM")
                        
                        for i, match in enumerate(matches):
                            status_icon = "🟢" if match["ativo"] else "🔴"
                            score_pct = int(match["score"] * 100)
                            
                            with st.expander(f"{status_icon} {match['cnpj']} (Match: {score_pct}%)", expanded=(i==0)):
                                st.markdown(f"**Nome:** {match['nome']}")
                                st.markdown(f"**Situação:** {match['situacao']}")
                                st.code(match['cnpj'], language=None)
                    else:
                        st.warning("⚠️ Nenhum fundo encontrado na CVM")
                else:
                    st.error("❌ Base CVM não disponível")
            
            # ===== BUSCA OPENFIGI =====
            st.markdown("### 🌐 Busca no OpenFIGI")
            
            with st.spinner("Buscando no OpenFIGI..."):
                # Determinar tipo de busca
                if search_type == "Auto":
                    if ticker:
                        id_type = "TICKER"
                        query = ticker
                    elif re.match(r"^BR[A-Z0-9]{10}$", nome_ativo.upper()):
                        id_type = "ISIN"
                        query = nome_ativo.upper()
                    else:
                        id_type = "NAME"
                        query = nome_ativo
                elif search_type == "Ticker":
                    id_type = "TICKER"
                    query = ticker or nome_ativo
                elif search_type == "ISIN":
                    id_type = "ISIN"
                    query = nome_ativo.upper()
                else:
                    id_type = "NAME"
                    query = nome_ativo
                
                figi_results = search_openfigi(query, id_type)
            
            if figi_results:
                st.success(f"✅ {len(figi_results)} resultado(s) no OpenFIGI")
                
                # Mostrar resultados em tabela
                figi_df = pd.DataFrame([{
                    "FIGI": r.get("figi", ""),
                    "Ticker": r.get("ticker", ""),
                    "Nome": r.get("name", ""),
                    "Exchange": r.get("exchCode", ""),
                    "Tipo": r.get("securityType", ""),
                    "Moeda": r.get("marketSector", "")
                } for r in figi_results[:10]])
                
                st.dataframe(figi_df, use_container_width=True, hide_index=True)
                
                # Detalhes do primeiro resultado
                if figi_results:
                    with st.expander("📋 Detalhes do primeiro resultado", expanded=True):
                        first = figi_results[0]
                        col1, col2 = st.columns(2)
                        with col1:
                            st.markdown(f"**FIGI:** `{first.get('figi', 'N/A')}`")
                            st.markdown(f"**Ticker:** `{first.get('ticker', 'N/A')}`")
                            st.markdown(f"**Nome:** {first.get('name', 'N/A')}")
                        with col2:
                            st.markdown(f"**Exchange:** {first.get('exchCode', 'N/A')}")
                            st.markdown(f"**Tipo:** {first.get('securityType', 'N/A')}")
                            st.markdown(f"**Setor:** {first.get('marketSector', 'N/A')}")
            else:
                st.info("ℹ️ Nenhum resultado no OpenFIGI")
                st.markdown("**Dicas:**")
                st.markdown("- Para títulos públicos brasileiros, tente buscar por 'LFT', 'LTN', 'NTNB'")
                st.markdown("- Para FIIs, use o ticker (ex: RBRY11)")
                st.markdown("- Para ações, use o ticker (ex: PETR4)")
            
            # ===== RESUMO =====
            st.divider()
            st.markdown("### 📋 Resumo")
            
            # Consolidar melhor resultado
            cnpj_result = None
            if tipo_ativo == "Fundo" and cvm_df is not None:
                matches = find_cnpj_cvm(nome_ativo, cvm_df, limit=1)
                if matches:
                    cnpj_result = matches[0]["cnpj"]
            
            figi_ticker = None
            figi_name = None
            if figi_results:
                figi_ticker = figi_results[0].get("ticker")
                figi_name = figi_results[0].get("name")
            
            result_df = pd.DataFrame([{
                "Ativo": nome_ativo,
                "Tipo Ativo": tipo_ativo,
                "Classificação": classificacao,
                "CNPJ (CVM)": cnpj_result,
                "Ticker (OpenFIGI)": figi_ticker or ticker,
                "Nome (OpenFIGI)": figi_name,
                "Vencimento": vencimento
            }])
            
            st.dataframe(result_df, use_container_width=True, hide_index=True)
    
    # Sidebar
    with st.sidebar:
        st.markdown("### 📊 Status das APIs")
        
        if cvm_df is not None:
            st.success(f"✅ CVM: {len(cvm_df):,} fundos")
        else:
            st.error("❌ CVM: indisponível")
        
        # Testar OpenFIGI
        test_result = search_openfigi("PETR4", "TICKER")
        if test_result:
            st.success("✅ OpenFIGI: conectado")
        else:
            st.warning("⚠️ OpenFIGI: verificar")
        
        st.divider()
        st.markdown("""
        **Fontes de dados:**
        - **CVM**: CNPJs de fundos brasileiros
        - **OpenFIGI**: Tickers, FIGIs globais
        
        **Tipos de busca OpenFIGI:**
        - `NAME` - Por nome
        - `TICKER` - Por ticker
        - `ISIN` - Por código ISIN
        """)

if __name__ == "__main__":
    main()
