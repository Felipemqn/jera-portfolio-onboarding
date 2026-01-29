"""
JERA PORTFOLIO ONBOARDING v5
============================
Com busca CNPJ melhorada
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
# TITULO PUBLICO ISIN TABLE
# =============================================================================

TITULO_ISIN = {
    "LFT_2028": "BRSTNCLF1RU8", "LFT_2029": "BRSTNCLF1RV6", "LFT_2030": "BRSTNCLF1RW4",
    "LTN_2030": "BRSTNCLTN7U0", "LTN_2032": "BRSTNCLTN7V8",
    "NTNB_2030": "BRSTNCNTB4P3", "NTNB_2032": "BRSTNCNTB4Q1", "NTNB_2035": "BRSTNCNTB4R9",
    "NTNB_2040": "BRSTNCNTB4S7", "NTNB_2045": "BRSTNCNTB4T5", "NTNB_2050": "BRSTNCNTB4U3",
    "NTNF_2029": "BRSTNCNTF1T4", "NTNF_2031": "BRSTNCNTF1U2", "NTNF_2033": "BRSTNCNTF1V0",
}

def get_isin(tipo: str, vencimento: str) -> str:
    match = re.search(r"(\d{4})$", vencimento)
    if match:
        year = match.group(1)
        tipo_norm = tipo.upper().replace("-", "").replace(" ", "")
        if "LFT" in tipo_norm:
            key = f"LFT_{year}"
        elif "LTN" in tipo_norm:
            key = f"LTN_{year}"
        elif "NTNB" in tipo_norm:
            key = f"NTNB_{year}"
        elif "NTNF" in tipo_norm:
            key = f"NTNF_{year}"
        else:
            return None
        return TITULO_ISIN.get(key)
    return None

# =============================================================================
# CVM FUND LOOKUP (Improved)
# =============================================================================

@st.cache_data(ttl=3600, show_spinner=False)
def load_cvm_data():
    """Carrega DataFrame completo da CVM"""
    try:
        url = "https://dados.cvm.gov.br/dados/FI/CAD/DADOS/cad_fi.csv"
        df = pd.read_csv(url, sep=";", encoding="latin1", low_memory=False)
        return df
    except Exception as e:
        st.error(f"Erro ao carregar CVM: {e}")
        return None

def find_cnpj(fund_name: str, cvm_df: pd.DataFrame) -> str:
    """Busca CNPJ usando palavras-chave (ignora siglas de tipo de fundo)"""
    if cvm_df is None or cvm_df.empty:
        return None
    
    name_upper = fund_name.upper()
    
    # Palavras a ignorar (tipos de fundo, sufixos comuns)
    skip_words = {
        "FIM", "FIC", "FIDC", "FIP", "FIF", "FCFM", "FFIM", "FIQ", "FICFIM", 
        "FC", "FM", "CP", "RF", "FI", "SUB", "FEE", "AB", "MU", "Z", "II", "III",
        "I", "IV", "V", "PF", "ML", "BR", "CL", "T5", "REF", "OVER", "IPCA",
        "PRE", "D", "LEG", "ALL", "MAR", "DEB"
    }
    
    # Extrair palavras-chave relevantes
    words = [w for w in name_upper.split() if w not in skip_words and len(w) > 1]
    
    if not words:
        return None
    
    # Buscar progressivamente refinando
    candidates = cvm_df.copy()
    
    for word in words:
        new_candidates = candidates[
            candidates["DENOM_SOCIAL"].str.upper().str.contains(word, na=False, regex=False)
        ]
        if len(new_candidates) > 0:
            candidates = new_candidates
        if len(candidates) == 1:
            break
    
    if len(candidates) > 0:
        # Priorizar fundos em funcionamento normal
        ativos = candidates[candidates["SIT"].str.contains("FUNCIONAMENTO", na=False)]
        if len(ativos) > 0:
            return str(ativos.iloc[0]["CNPJ_FUNDO"])
        return str(candidates.iloc[0]["CNPJ_FUNDO"])
    
    return None

# =============================================================================
# BTG PDF PARSER
# =============================================================================

def parse_btg_pdf(pdf_file, cvm_df: pd.DataFrame) -> tuple:
    """Parser para extratos BTG"""
    assets = []
    metadata = {"fund_name": "", "nav": None}
    
    with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as tmp:
        tmp.write(pdf_file.read())
        tmp_path = tmp.name
    
    try:
        with pdfplumber.open(tmp_path) as pdf:
            all_text = ""
            for page in pdf.pages:
                all_text += (page.extract_text() or "") + "\n"
            
            # Extract fund name
            match = re.search(r"(FI\s+MULT[^\n]+)", all_text)
            if match:
                metadata["fund_name"] = match.group(1).strip()
            
            # Extract patrimonio
            match = re.search(r"Patrimônio\s+([\d.,]+)", all_text)
            if match:
                try:
                    metadata["nav"] = float(match.group(1).replace(".", "").replace(",", "."))
                except:
                    pass
            
            # === PARSE FUNDOS ===
            fund_pattern = r"(\d+)-\s*([A-Z][A-Z0-9\s]+?(?:FI[MCAD]?|FIC|FIDC|FIP|FIF|FCFM|FFIM|FC FM|FIM CP|FICFIM|FIC FIM|FIQ|FI RF)[A-Z\s]*?)\s+(\d+\.\d{2})\s+([\d,]+\.\d{2})"
            
            for match in re.finditer(fund_pattern, all_text):
                name = match.group(2).strip()
                pct_pl = float(match.group(3))
                value = float(match.group(4).replace(",", ""))
                
                # Find CNPJ
                cnpj = find_cnpj(name, cvm_df)
                
                assets.append({
                    "description": name,
                    "type": "FUNDO",
                    "pct_pl": pct_pl,
                    "value": value,
                    "vencimento": None,
                    "isin": None,
                    "cnpj": cnpj,
                    "status": "OK" if cnpj else "PENDENTE"
                })
            
            # === PARSE TITULOS PUBLICOS ===
            titulo_pattern = r"(\d+)-\s*(LFT|LTN|NTNB|NTN-?B)[A-Z\s]*\s+(\d+\.\d{2})\s+([\d,]+\.\d{2})\s+[\d,]+\s+[\d.,]+\s+(\d{2}/\d{2}/\d{4})"
            
            for match in re.finditer(titulo_pattern, all_text):
                tipo = match.group(2).upper().replace("-", "")
                pct_pl = float(match.group(3))
                value = float(match.group(4).replace(",", ""))
                vencimento = match.group(5)
                
                isin = get_isin(tipo, vencimento)
                
                assets.append({
                    "description": f"{tipo} {vencimento}",
                    "type": "TITULO_PUBLICO",
                    "pct_pl": pct_pl,
                    "value": value,
                    "vencimento": vencimento,
                    "isin": isin,
                    "cnpj": None,
                    "status": "OK" if isin else "REVISAR"
                })
            
            # === PARSE TITULOS PRIVADOS ===
            privado_pattern = r"(\d+)-\s*(CRI|CRA|GYRA\d*|PRLK\d*)[A-Z0-9\s]*\s+(\d+\.\d{2})\s+([\d,]+\.\d{2})"
            
            for match in re.finditer(privado_pattern, all_text):
                name = match.group(2)
                pct_pl = float(match.group(3))
                value = float(match.group(4).replace(",", ""))
                
                if "CRI" in name.upper():
                    tipo = "CRI"
                elif "CRA" in name.upper():
                    tipo = "CRA"
                else:
                    tipo = "DEBENTURE"
                
                assets.append({
                    "description": name,
                    "type": tipo,
                    "pct_pl": pct_pl,
                    "value": value,
                    "vencimento": None,
                    "isin": None,
                    "cnpj": None,
                    "status": "PENDENTE"
                })
            
            # Remove duplicates
            seen = set()
            unique_assets = []
            for a in assets:
                key = f"{a['description']}_{a['value']}"
                if key not in seen:
                    seen.add(key)
                    unique_assets.append(a)
            
            assets = unique_assets
    
    finally:
        Path(tmp_path).unlink(missing_ok=True)
    
    return assets, metadata

# =============================================================================
# STREAMLIT UI
# =============================================================================

def main():
    st.title("📊 Jera Portfolio Onboarding")
    st.markdown("**Extratos BTG → Dados com CNPJ/ISIN automáticos**")
    
    # Load CVM data
    with st.spinner("📚 Carregando base CVM..."):
        cvm_df = load_cvm_data()
    
    with st.sidebar:
        st.header("📁 Upload")
        uploaded_file = st.file_uploader(
            "Arraste o PDF aqui",
            type=['pdf'],
            help="Extratos BTG Pactual"
        )
        
        st.divider()
        if cvm_df is not None:
            st.success(f"✅ {len(cvm_df):,} fundos CVM")
        
        st.markdown("""
        **Identificadores:**
        - Fundos → **CNPJ** (CVM)
        - Títulos → **ISIN**
        """)
    
    if uploaded_file is None:
        st.info("👆 Faça upload de um extrato BTG")
        return
    
    # Process
    with st.spinner("🔄 Processando..."):
        assets, metadata = parse_btg_pdf(uploaded_file, cvm_df)
    
    if not assets:
        st.error("❌ Não foi possível extrair ativos")
        return
    
    # Success
    st.success(f"✅ **{metadata.get('fund_name', 'Portfolio')}** - {len(assets)} ativos")
    
    if metadata.get("nav"):
        st.metric("Patrimônio", f"R$ {metadata['nav']:,.2f}")
    
    # DataFrame
    df = pd.DataFrame(assets)
    
    # Stats
    st.subheader("📊 Resumo")
    
    col1, col2, col3, col4 = st.columns(4)
    
    total = len(df)
    com_cnpj = df["cnpj"].notna().sum()
    com_isin = df["isin"].notna().sum()
    pendente = total - com_cnpj - com_isin
    
    with col1:
        st.metric("Total", total)
    with col2:
        st.metric("✅ CNPJ", int(com_cnpj))
    with col3:
        st.metric("✅ ISIN", int(com_isin))
    with col4:
        st.metric("⏳ Pendente", int(pendente))
    
    # By type
    type_stats = df.groupby("type").agg(
        Qtd=("description", "count"),
        Valor=("value", "sum"),
        Identificados=("status", lambda x: (x == "OK").sum())
    ).reset_index()
    type_stats["Valor"] = type_stats["Valor"].apply(lambda x: f"R$ {x:,.2f}")
    st.dataframe(type_stats, use_container_width=True, hide_index=True)
    
    # Table
    st.subheader("📝 Ativos")
    
    cols = ["description", "type", "pct_pl", "value", "vencimento", "cnpj", "isin", "status"]
    df_display = df[[c for c in cols if c in df.columns]]
    
    edited_df = st.data_editor(
        df_display,
        use_container_width=True,
        column_config={
            "description": st.column_config.TextColumn("Descrição", width="large"),
            "type": st.column_config.SelectboxColumn("Tipo", options=["FUNDO", "TITULO_PUBLICO", "CRI", "CRA", "DEBENTURE", "CDB", "LF", "FII", "FIDC", "FIP", "OUTROS"]),
            "pct_pl": st.column_config.NumberColumn("%PL", format="%.2f"),
            "value": st.column_config.NumberColumn("Valor (R$)", format="%.2f"),
            "vencimento": st.column_config.TextColumn("Vencimento"),
            "cnpj": st.column_config.TextColumn("CNPJ", width="medium"),
            "isin": st.column_config.TextColumn("ISIN"),
            "status": st.column_config.SelectboxColumn("Status", options=["OK", "REVISAR", "PENDENTE"]),
        },
        hide_index=True,
    )
    
    # Export
    st.subheader("💾 Exportar")
    
    col1, col2 = st.columns(2)
    
    with col1:
        buffer = io.BytesIO()
        edited_df.to_excel(buffer, index=False, sheet_name="Ativos")
        buffer.seek(0)
        st.download_button(
            "📥 Baixar Excel",
            data=buffer,
            file_name=f"portfolio_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
    
    with col2:
        st.download_button(
            "📥 Baixar JSON",
            data=edited_df.to_json(orient="records", force_ascii=False, indent=2),
            file_name=f"portfolio_{datetime.now().strftime('%Y%m%d_%H%M')}.json",
            mime="application/json"
        )

if __name__ == "__main__":
    main()
