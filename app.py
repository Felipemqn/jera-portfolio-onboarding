"""
JERA PORTFOLIO ONBOARDING v4
============================
Com busca de CNPJ para fundos via CVM
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
# CVM FUND LOOKUP (46k+ fundos brasileiros)
# =============================================================================

@st.cache_data(ttl=3600)
def load_cvm_funds():
    """Carrega base de fundos da CVM"""
    try:
        url = "https://dados.cvm.gov.br/dados/FI/CAD/DADOS/cad_fi.csv"
        df = pd.read_csv(url, sep=";", encoding="latin1", low_memory=False)
        
        # Create lookup dict: normalized name -> CNPJ
        lookup = {}
        for _, row in df.iterrows():
            name = str(row.get("DENOM_SOCIAL", "")).upper()
            cnpj = str(row.get("CNPJ_FUNDO", ""))
            if name and cnpj:
                # Normalize name for matching
                name_norm = re.sub(r"[^A-Z0-9]", "", name)
                lookup[name_norm] = cnpj
                # Also store partial names (first 3 words)
                words = name.split()[:4]
                if len(words) >= 2:
                    partial = re.sub(r"[^A-Z0-9]", "", " ".join(words))
                    if partial not in lookup:
                        lookup[partial] = cnpj
        
        return lookup
    except Exception as e:
        st.warning(f"⚠️ Não foi possível carregar base CVM: {e}")
        return {}

def find_cnpj(fund_name: str, cvm_lookup: dict) -> str:
    """Busca CNPJ do fundo na base CVM"""
    if not cvm_lookup:
        return None
    
    # Normalize fund name
    name_norm = re.sub(r"[^A-Z0-9]", "", fund_name.upper())
    
    # Direct match
    if name_norm in cvm_lookup:
        return cvm_lookup[name_norm]
    
    # Partial match (try progressively shorter)
    words = fund_name.upper().split()
    for length in range(len(words), 1, -1):
        partial = re.sub(r"[^A-Z0-9]", "", " ".join(words[:length]))
        if partial in cvm_lookup:
            return cvm_lookup[partial]
    
    # Fuzzy match - check if name contains key parts
    for key, cnpj in cvm_lookup.items():
        if len(key) > 10 and key in name_norm:
            return cnpj
    
    return None

# =============================================================================
# BTG PDF PARSER
# =============================================================================

def parse_btg_pdf(pdf_file, cvm_lookup: dict) -> tuple:
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
                cnpj = find_cnpj(name, cvm_lookup)
                
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
    st.markdown("**Extratos BTG → Dados estruturados com CNPJ/ISIN**")
    
    # Load CVM data
    with st.spinner("📚 Carregando base de fundos CVM (46k+ fundos)..."):
        cvm_lookup = load_cvm_funds()
    
    with st.sidebar:
        st.header("📁 Upload")
        uploaded_file = st.file_uploader(
            "Arraste o PDF aqui",
            type=['pdf'],
            help="Extratos BTG Pactual"
        )
        
        st.divider()
        st.success(f"✅ {len(cvm_lookup):,} fundos CVM carregados")
        
        st.markdown("""
        **Identificadores:**
        - Fundos → **CNPJ** (via CVM)
        - Títulos Públicos → **ISIN**
        - CRI/CRA/Deb → Manual
        """)
    
    if uploaded_file is None:
        st.info("👆 Faça upload de um extrato BTG")
        
        col1, col2, col3 = st.columns(3)
        with col1:
            st.markdown("### 1️⃣ Upload")
            st.markdown("PDF do extrato")
        with col2:
            st.markdown("### 2️⃣ Extração")
            st.markdown("CNPJ + ISIN automático")
        with col3:
            st.markdown("### 3️⃣ Export")
            st.markdown("Excel para Maravi")
        return
    
    # Process
    with st.spinner("🔄 Processando extrato..."):
        assets, metadata = parse_btg_pdf(uploaded_file, cvm_lookup)
    
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
        st.metric("Total Ativos", total)
    with col2:
        st.metric("✅ Com CNPJ", com_cnpj)
    with col3:
        st.metric("✅ Com ISIN", com_isin)
    with col4:
        st.metric("⏳ Pendente", pendente)
    
    # By type
    type_stats = df.groupby("type").agg(
        Qtd=("description", "count"),
        Valor=("value", "sum"),
        Identificados=("status", lambda x: (x == "OK").sum())
    ).reset_index()
    type_stats["Valor"] = type_stats["Valor"].apply(lambda x: f"R$ {x:,.2f}")
    type_stats.columns = ["Tipo", "Qtd", "Valor Total", "Identificados"]
    st.dataframe(type_stats, use_container_width=True, hide_index=True)
    
    # Table
    st.subheader("📝 Ativos Extraídos")
    
    # Reorder columns
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
            "cnpj": st.column_config.TextColumn("CNPJ"),
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
