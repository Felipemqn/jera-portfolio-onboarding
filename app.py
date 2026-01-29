"""
JERA PORTFOLIO ONBOARDING v3
============================
Parser otimizado para extratos BTG (baseado em texto)
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
    """Get ISIN from tipo and vencimento date"""
    # Extract year from vencimento (format: DD/MM/YYYY)
    match = re.search(r"(\d{4})$", vencimento)
    if match:
        year = match.group(1)
        # Normalize tipo
        tipo_norm = tipo.upper().replace("-", "").replace(" ", "")
        if "LFT" in tipo_norm:
            key = f"LFT_{year}"
        elif "LTN" in tipo_norm:
            key = f"LTN_{year}"
        elif "NTNB" in tipo_norm or "NTN B" in tipo_norm:
            key = f"NTNB_{year}"
        elif "NTNF" in tipo_norm:
            key = f"NTNF_{year}"
        else:
            return None
        
        return TITULO_ISIN.get(key)
    return None

# =============================================================================
# BTG PDF PARSER (Text-based)
# =============================================================================

def parse_btg_pdf(pdf_file) -> tuple:
    """Parser para extratos BTG baseado em texto"""
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
            # Pattern: "1- BLC III FIM CP 3.54 10,814,730.24 ..."
            fund_pattern = r"(\d+)-\s*([A-Z][A-Z0-9\s]+?(?:FI[MCAD]?|FIC|FIDC|FIP|FIF|FCFM|FFIM|FC FM|FIM CP|FICFIM|FIC FIM|FIQ)[A-Z\s]*?)\s+(\d+\.\d{2})\s+([\d,]+\.\d{2})"
            
            for match in re.finditer(fund_pattern, all_text):
                name = match.group(2).strip()
                pct_pl = float(match.group(3))
                value = float(match.group(4).replace(",", ""))
                
                assets.append({
                    "description": name,
                    "type": "FUNDO",
                    "pct_pl": pct_pl,
                    "value": value,
                    "vencimento": None,
                    "isin": None,
                    "status": "PENDENTE"
                })
            
            # === PARSE TITULOS PUBLICOS ===
            # Pattern: "1- LFT REF 0.44 1,348,361.05 75 17,978.147360 01/09/2028"
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
                    "status": "OK" if isin else "REVISAR"
                })
            
            # === PARSE TITULOS PRIVADOS ===
            # Pattern for CRI, CRA, Debentures
            privado_pattern = r"(\d+)-\s*(CRI|CRA|GYRA\d*|PRLK\d*)[A-Z0-9\s]*\s+(\d+\.\d{2})\s+([\d,]+\.\d{2})"
            
            for match in re.finditer(privado_pattern, all_text):
                name = match.group(2)
                pct_pl = float(match.group(3))
                value = float(match.group(4).replace(",", ""))
                
                # Classify type
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
    st.markdown("**Transforme extratos BTG em dados estruturados**")
    
    with st.sidebar:
        st.header("📁 Upload")
        uploaded_file = st.file_uploader(
            "Arraste o PDF aqui",
            type=['pdf'],
            help="Extratos BTG Pactual"
        )
        
        st.divider()
        st.markdown("""
        **Extrai automaticamente:**
        - ✅ Fundos (FIM, FIC, FIDC, FIP)
        - ✅ Títulos Públicos (LFT, LTN, NTN-B)
        - ✅ Títulos Privados (CRI, CRA, Deb)
        """)
    
    if uploaded_file is None:
        st.info("👆 Faça upload de um extrato BTG para começar")
        
        col1, col2, col3 = st.columns(3)
        with col1:
            st.markdown("### 1️⃣ Upload")
            st.markdown("Arraste o PDF")
        with col2:
            st.markdown("### 2️⃣ Extração")
            st.markdown("Automática com ISINs")
        with col3:
            st.markdown("### 3️⃣ Export")
            st.markdown("Excel para Maravi")
        return
    
    # Process PDF
    with st.spinner("🔄 Processando extrato BTG..."):
        assets, metadata = parse_btg_pdf(uploaded_file)
    
    if not assets:
        st.error("❌ Não foi possível extrair ativos")
        return
    
    # Show success
    fund_name = metadata.get("fund_name", "Portfolio")
    st.success(f"✅ **{fund_name}** - {len(assets)} ativos extraídos!")
    
    # Patrimonio
    if metadata.get("nav"):
        st.metric("Patrimônio Total", f"R$ {metadata['nav']:,.2f}")
    
    # Convert to DataFrame
    df = pd.DataFrame(assets)
    
    # Stats by type
    st.subheader("📊 Resumo por Tipo")
    
    type_stats = df.groupby("type").agg(
        Quantidade=("description", "count"),
        Valor=("value", "sum")
    ).reset_index()
    type_stats["Valor"] = type_stats["Valor"].apply(lambda x: f"R$ {x:,.2f}" if x > 0 else "-")
    type_stats.columns = ["Tipo", "Qtd", "Valor Total"]
    st.dataframe(type_stats, use_container_width=True, hide_index=True)
    
    # Status
    col1, col2, col3 = st.columns(3)
    ok = len(df[df["status"] == "OK"])
    rev = len(df[df["status"] == "REVISAR"])
    pend = len(df[df["status"] == "PENDENTE"])
    
    with col1:
        st.metric("✅ OK (com ISIN)", ok)
    with col2:
        st.metric("⚠️ Revisar", rev)
    with col3:
        st.metric("⏳ Pendente", pend)
    
    # Table
    st.subheader("📝 Ativos Extraídos")
    
    edited_df = st.data_editor(
        df,
        use_container_width=True,
        column_config={
            "description": st.column_config.TextColumn("Descrição", width="large"),
            "type": st.column_config.SelectboxColumn("Tipo", options=["FUNDO", "TITULO_PUBLICO", "CRI", "CRA", "DEBENTURE", "CDB", "LF", "FII", "FIDC", "FIP", "OUTROS"]),
            "pct_pl": st.column_config.NumberColumn("%PL", format="%.2f"),
            "value": st.column_config.NumberColumn("Valor (R$)", format="%.2f"),
            "vencimento": st.column_config.TextColumn("Vencimento"),
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
