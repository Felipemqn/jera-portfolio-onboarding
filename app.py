"""
JERA PORTFOLIO ONBOARDING v10
=============================
Com consulta individual de ativos
"""

import streamlit as st
import pandas as pd
import pdfplumber
import re
from datetime import datetime
import io
import tempfile
from pathlib import Path

st.set_page_config(
    page_title="Jera - Portfolio Onboarding",
    page_icon="📊",
    layout="wide"
)

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

@st.cache_data(ttl=86400, show_spinner=False)
def load_tesouro_data():
    try:
        url = "https://www.tesourodireto.com.br/json/br/com/b3/tesourodireto/service/api/treasurybondsinfo.json"
        import urllib.request
        import json
        with urllib.request.urlopen(url, timeout=10) as response:
            data = json.loads(response.read().decode())
            return data.get("response", {}).get("TrsrBdTradgList", [])
    except:
        return None

def find_cnpj_cvm(fund_name: str, cvm_df: pd.DataFrame, used_cnpjs: set = None, limit: int = 5) -> list:
    """
    Busca CNPJ na base da CVM
    Retorna lista de matches [(cnpj, nome_completo, situacao, score), ...]
    """
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
    
    # Filtrar já usados
    if used_cnpjs:
        candidates = candidates[~candidates["CNPJ_FUNDO"].isin(used_cnpjs)]
    
    # Ordenar: ativos primeiro
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

def find_isin_tesouro(tipo: str, vencimento: str, tesouro_data: list) -> str:
    if not tesouro_data:
        return None
    
    tipo_norm = tipo.upper().replace("-", "").replace(" ", "")
    
    venc_match = re.search(r"(\d{2})/(\d{2})/(\d{4})", vencimento)
    if not venc_match:
        return None
    
    venc_date = f"{venc_match.group(3)}-{venc_match.group(2)}-{venc_match.group(1)}"
    
    tipo_map = {
        "LFT": "Tesouro Selic",
        "LTN": "Tesouro Prefixado",
        "NTNB": "Tesouro IPCA+",
        "NTNF": "Tesouro Prefixado com Juros"
    }
    
    nome_tesouro = None
    for key, val in tipo_map.items():
        if key in tipo_norm:
            nome_tesouro = val
            break
    
    if not nome_tesouro:
        return None
    
    for titulo in tesouro_data:
        if nome_tesouro.lower() in titulo.get("TrsrBd", {}).get("nm", "").lower():
            titulo_venc = titulo.get("TrsrBd", {}).get("mtrtyDt", "")
            if venc_date in titulo_venc:
                return titulo.get("TrsrBd", {}).get("isinCd")
    
    return None

# =============================================================================
# CLASSIFICAÇÃO DINÂMICA
# =============================================================================

def classify_asset(nome: str) -> tuple:
    nome_upper = nome.upper()
    
    # Tipo de ativo
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
    
    # Classificação
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

def parse_btg_pdf(pdf_file, cvm_df: pd.DataFrame, tesouro_data: list) -> tuple:
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
                
                isin = None
                vencimento = extract_vencimento(nome)
                if tipo_ativo == "Tit Publico" and vencimento:
                    isin = find_isin_tesouro(nome, vencimento, tesouro_data)
                
                ticker = extract_ticker(nome)
                
                if tipo_ativo == "Fundo":
                    status = "OK" if cnpj and score >= 0.5 else ("Verificar" if cnpj else "Pendente")
                elif tipo_ativo == "Tit Publico":
                    status = "OK" if isin else "Verificar"
                else:
                    status = "Pendente" if not ticker else "Verificar"
                
                assets.append({
                    "nome": nome,
                    "tipo_ativo": tipo_ativo,
                    "classificacao": classificacao,
                    "pct_pl": pct_pl,
                    "value": value,
                    "cnpj": cnpj,
                    "ticker": ticker,
                    "isin": isin,
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
        tesouro_data = load_tesouro_data()
    
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
        
        with col_upload:
            uploaded_file = st.file_uploader("Arraste o PDF aqui", type=['pdf'])
        
        if uploaded_file:
            with st.spinner("🔄 Processando PDF..."):
                assets, metadata = parse_btg_pdf(uploaded_file, cvm_df, tesouro_data)
            
            if not assets:
                st.error("❌ Não foi possível extrair ativos")
                return
            
            if not trading_desk and metadata.get("fund_name"):
                trading_desk = metadata["fund_name"]
            
            st.success(f"✅ **{len(assets)} ativos** extraídos de {metadata.get('fund_name', 'PDF')}")
            
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
        
        nome_ativo = st.text_input(
            "Nome do Ativo",
            placeholder="Ex: SPX CAPITAL PLUS FIQ, NTNB 15/08/2050, RBRY11...",
            key="consulta_nome"
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
            
            # Buscar dados específicos baseado no tipo
            if tipo_ativo == "Fundo":
                st.markdown("### 🏦 Busca na CVM")
                
                if cvm_df is not None:
                    with st.spinner("Buscando..."):
                        matches = find_cnpj_cvm(nome_ativo, cvm_df, limit=10)
                    
                    if matches:
                        st.success(f"✅ {len(matches)} resultado(s) encontrado(s)")
                        
                        # Mostrar resultados
                        for i, match in enumerate(matches):
                            status_icon = "🟢" if match["ativo"] else "🔴"
                            score_pct = int(match["score"] * 100)
                            
                            with st.expander(f"{status_icon} {match['cnpj']} (Match: {score_pct}%)", expanded=(i==0)):
                                st.markdown(f"**Nome completo:** {match['nome']}")
                                st.markdown(f"**Situação:** {match['situacao']}")
                                st.markdown(f"**CNPJ:** `{match['cnpj']}`")
                                
                                # Botão para copiar
                                st.code(match['cnpj'], language=None)
                    else:
                        st.warning("⚠️ Nenhum fundo encontrado na CVM")
                        st.info("Tente ajustar o nome do fundo (remover sufixos como FIM, FIC, etc.)")
                else:
                    st.error("❌ Base CVM não disponível")
            
            elif tipo_ativo == "Tit Publico":
                st.markdown("### 📜 Busca no Tesouro Direto")
                
                if vencimento:
                    if tesouro_data:
                        with st.spinner("Buscando..."):
                            isin = find_isin_tesouro(nome_ativo, vencimento, tesouro_data)
                        
                        if isin:
                            st.success(f"✅ ISIN encontrado!")
                            st.metric("ISIN", isin)
                            st.code(isin, language=None)
                        else:
                            st.warning("⚠️ ISIN não encontrado no Tesouro Direto")
                            st.info("Verifique se o tipo e vencimento estão corretos")
                    else:
                        st.error("❌ Base do Tesouro não disponível")
                else:
                    st.warning("⚠️ Informe o vencimento no formato DD/MM/YYYY")
                    st.info("Ex: NTNB 15/08/2050, LFT 01/09/2028")
            
            elif tipo_ativo in ["FII", "Debenture"]:
                st.markdown("### 📈 Informações")
                
                if ticker:
                    st.success(f"✅ Ticker identificado: **{ticker}**")
                    st.code(ticker, language=None)
                    
                    # Link B3
                    st.markdown(f"🔗 [Buscar na B3](https://www.b3.com.br/pt_br/produtos-e-servicos/negociacao/renda-variavel/fundos-de-investimento-imobiliarios-fii.htm)")
                else:
                    st.warning("⚠️ Ticker não identificado automaticamente")
                    st.info("Informe o ticker no formato XXXX11 (FII) ou XXXX99 (Debênture)")
            
            else:
                st.info(f"ℹ️ Tipo **{tipo_ativo}** - consulta manual necessária")
            
            # Resumo final
            st.divider()
            st.markdown("### 📋 Resumo")
            
            result_df = pd.DataFrame([{
                "Ativo": nome_ativo,
                "Tipo Ativo": tipo_ativo,
                "Classificação": classificacao,
                "CNPJ": matches[0]["cnpj"] if tipo_ativo == "Fundo" and matches else None,
                "Ticker": ticker,
                "ISIN": isin if tipo_ativo == "Tit Publico" and vencimento and tesouro_data else None,
                "Vencimento": vencimento
            }])
            
            st.dataframe(result_df, use_container_width=True, hide_index=True)
    
    # Sidebar info
    with st.sidebar:
        st.markdown("### 📊 Bases de Dados")
        if cvm_df is not None:
            st.success(f"CVM: {len(cvm_df):,} fundos")
        else:
            st.error("CVM: indisponível")
        
        if tesouro_data:
            st.success(f"Tesouro: {len(tesouro_data)} títulos")
        else:
            st.warning("Tesouro: indisponível")
        
        st.divider()
        st.markdown("""
        **Modos de uso:**
        1. **Upload PDF** - Processa extrato completo
        2. **Consulta Individual** - Busca 1 ativo
        """)

if __name__ == "__main__":
    main()
