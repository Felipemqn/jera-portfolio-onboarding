"""
JERA PORTFOLIO ONBOARDING v2
============================
Parser otimizado para extratos BTG
"""

import streamlit as st
import pandas as pd
import pdfplumber
import re
import requests
from datetime import datetime
import io
import tempfile
from pathlib import Path

st.set_page_config(
    page_title="Jera - Portfolio Onboarding",
    page_icon="📊",
    layout="wide"
)

OPENFIGI_API_KEY = "01e15cc4-a2d8-47b6-8323-746430a85b52"

# =============================================================================
# TITULO PUBLICO ISIN TABLE
# =============================================================================

TITULO_ISIN = {
    "LFT_2025": "BRSTNCLF1RR4", "LFT_2026": "BRSTNCLF1RS2", "LFT_2027": "BRSTNCLF1RT0",
    "LFT_2028": "BRSTNCLF1RU8", "LFT_2029": "BRSTNCLF1RV6", "LFT_2030": "BRSTNCLF1RW4",
    "LTN_2025": "BRSTNCLTN7P0", "LTN_2026": "BRSTNCLTN7Q8", "LTN_2027": "BRSTNCLTN7R6",
    "LTN_2028": "BRSTNCLTN7S4", "LTN_2029": "BRSTNCLTN7T2", "LTN_2030": "BRSTNCLTN7U0",
    "LTN_2032": "BRSTNCLTN7V8",
    "NTNB_2025": "BRSTNCNTB4K4", "NTNB_2026": "BRSTNCNTB4L2", "NTNB_2027": "BRSTNCNTB4M0",
    "NTNB_2028": "BRSTNCNTB4N8", "NTNB_2029": "BRSTNCNTB4O6", "NTNB_2030": "BRSTNCNTB4P3",
    "NTNB_2032": "BRSTNCNTB4Q1", "NTNB_2035": "BRSTNCNTB4R9", "NTNB_2040": "BRSTNCNTB4S7",
    "NTNB_2045": "BRSTNCNTB4T5", "NTNB_2050": "BRSTNCNTB4U3", "NTNB_2055": "BRSTNCNTB4V1",
    "NTNF_2025": "BRSTNCNTF1R8", "NTNF_2027": "BRSTNCNTF1S6", "NTNF_2029": "BRSTNCNTF1T4",
    "NTNF_2031": "BRSTNCNTF1U2", "NTNF_2033": "BRSTNCNTF1V0", "NTNF_2035": "BRSTNCNTF1W8",
}

# =============================================================================
# BTG PDF PARSER (Optimized)
# =============================================================================

def parse_btg_pdf(pdf_file) -> tuple:
    """Parser otimizado para extratos BTG"""
    assets = []
    metadata = {"fund_name": "", "nav": None, "report_type": "BTG Carteira"}
    
    with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as tmp:
        tmp.write(pdf_file.read())
        tmp_path = tmp.name
    
    try:
        with pdfplumber.open(tmp_path) as pdf:
            all_text = ""
            for page in pdf.pages:
                text = page.extract_text() or ""
                all_text += text + "\n"
            
            # Extract fund name
            match = re.search(r"(FI\s+MULT[^\n]+)", all_text)
            if match:
                metadata["fund_name"] = match.group(1).strip()
            
            # Extract patrimonio
            match = re.search(r"Patrimônio[:\s]+([\d.,]+)", all_text)
            if match:
                try:
                    metadata["nav"] = float(match.group(1).replace(".", "").replace(",", "."))
                except:
                    pass
            
            # Parse each page for tables
            current_section = ""
            
            for page in pdf.pages:
                text = page.extract_text() or ""
                
                # Detect section
                if "Portfolio Investido" in text or "Carteira" in text:
                    current_section = "FUNDOS"
                if "Títulos Públicos" in text:
                    current_section = "TITULOS_PUBLICOS"
                if "Títulos Privados" in text:
                    current_section = "TITULOS_PRIVADOS"
                
                tables = page.extract_tables()
                
                for table in tables:
                    if not table:
                        continue
                    
                    for row in table:
                        if not row or len(row) < 3:
                            continue
                        
                        # Skip header rows
                        first_cell = str(row[0] or "").strip()
                        if first_cell in ["Ativo", "Data", "%PL", "Liquidez", ""]:
                            continue
                        if "Rentabilidades" in first_cell:
                            continue
                        
                        # Try to parse as asset row
                        asset = parse_asset_row(row, current_section)
                        if asset:
                            assets.append(asset)
            
            # Also try line-by-line parsing for funds
            assets.extend(parse_funds_from_text(all_text))
            
            # Remove duplicates based on description
            seen = set()
            unique_assets = []
            for a in assets:
                key = a["description"][:50]
                if key not in seen:
                    seen.add(key)
                    unique_assets.append(a)
            
            assets = unique_assets
    
    finally:
        Path(tmp_path).unlink(missing_ok=True)
    
    return assets, metadata


def parse_asset_row(row, section: str) -> dict:
    """Parse a single row from BTG table"""
    try:
        # Get first meaningful cell as description
        desc = ""
        value = None
        pct_pl = None
        vencimento = None
        
        for i, cell in enumerate(row):
            cell_str = str(cell or "").strip()
            
            # Skip empty or number-only first cells
            if i == 0:
                # Remove leading number like "1-", "2-"
                cell_str = re.sub(r"^\d+-\s*", "", cell_str)
                if cell_str and not re.match(r"^[\d.,%-]+$", cell_str):
                    desc = cell_str
                continue
            
            if not desc and cell_str and not re.match(r"^[\d.,%-]+$", cell_str):
                desc = cell_str
                continue
            
            # Try to find %PL (usually small number like 3.54)
            if cell_str and re.match(r"^\d{1,2}\.\d{2}$", cell_str):
                try:
                    pct_pl = float(cell_str)
                except:
                    pass
                continue
            
            # Try to find financial value (large number)
            if cell_str and re.match(r"^[\d.,]+$", cell_str):
                try:
                    val = float(cell_str.replace(".", "").replace(",", "."))
                    if val > 1000:  # Likely a financial value
                        value = val
                except:
                    pass
            
            # Try to find date (vencimento)
            if cell_str and re.match(r"^\d{2}/\d{2}/\d{4}$", cell_str):
                vencimento = cell_str
        
        if not desc or len(desc) < 3:
            return None
        
        # Skip non-asset rows
        skip_patterns = ["DESPESA", "TAXA", "Total", "Patrimônio", "Duration", "Consultar"]
        if any(p in desc.upper() for p in skip_patterns):
            return None
        
        # Classify asset
        asset_type = classify_asset(desc, section)
        
        # Get ISIN for titulos publicos
        isin = None
        status = "PENDENTE"
        
        if asset_type == "TITULO_PUBLICO":
            isin_result = get_titulo_isin(desc, vencimento)
            isin = isin_result.get("isin")
            status = isin_result.get("status", "PENDENTE")
        
        return {
            "description": desc[:100],
            "type": asset_type,
            "value": value,
            "pct_pl": pct_pl,
            "vencimento": vencimento,
            "isin": isin,
            "cnpj": None,
            "status": status
        }
    
    except Exception as e:
        return None


def parse_funds_from_text(text: str) -> list:
    """Parse funds from text using regex patterns"""
    assets = []
    
    # Pattern for fund lines like: "1- BLC III FIM CP 3.54 10,814,730.24"
    pattern = r"(\d+)-\s+([A-Z][A-Z0-9\s]+(?:FI[MCAD]?|FIC|FIDC|FIP|FIF|FCFM|FFIM)[A-Z\s]*)\s+([\d.]+)\s+([\d.,]+)"
    
    for match in re.finditer(pattern, text):
        try:
            desc = match.group(2).strip()
            pct_pl = float(match.group(3))
            value = float(match.group(4).replace(".", "").replace(",", "."))
            
            # Skip if already found
            assets.append({
                "description": desc[:100],
                "type": "FUNDO",
                "value": value,
                "pct_pl": pct_pl,
                "vencimento": None,
                "isin": None,
                "cnpj": None,
                "status": "PENDENTE"
            })
        except:
            continue
    
    # Pattern for titulos: "LFT REF 0.44 1,348,361.05"
    titulo_pattern = r"(LFT|LTN|NTNB|NTNF)[A-Z\s]*([\d.]+)\s+([\d.,]+)\s+[\d.,]+\s+[\d.,]+\s+(\d{2}/\d{2}/\d{4})"
    
    for match in re.finditer(titulo_pattern, text):
        try:
            tipo = match.group(1)
            pct_pl = float(match.group(2))
            value = float(match.group(3).replace(".", "").replace(",", "."))
            vencimento = match.group(4)
            
            desc = f"{tipo} {vencimento}"
            isin_result = get_titulo_isin(desc, vencimento)
            
            assets.append({
                "description": desc,
                "type": "TITULO_PUBLICO",
                "value": value,
                "pct_pl": pct_pl,
                "vencimento": vencimento,
                "isin": isin_result.get("isin"),
                "cnpj": None,
                "status": isin_result.get("status", "PENDENTE")
            })
        except:
            continue
    
    return assets


def classify_asset(desc: str, section: str = "") -> str:
    """Classify asset type based on description"""
    desc_upper = desc.upper()
    
    # Titulos Publicos
    if any(t in desc_upper for t in ["LFT", "LTN", "NTN", "TESOURO"]):
        return "TITULO_PUBLICO"
    
    # CRI/CRA
    if "CRI" in desc_upper and "CRIT" not in desc_upper:
        return "CRI"
    if "CRA" in desc_upper:
        return "CRA"
    
    # Debentures
    if any(t in desc_upper for t in ["DEB", "DEBENTURE"]):
        return "DEBENTURE"
    
    # FII
    if "FII" in desc_upper or re.search(r"[A-Z]{4}11", desc_upper):
        return "FII"
    
    # FIDC
    if "FIDC" in desc_upper:
        return "FIDC"
    
    # FIP
    if "FIP" in desc_upper:
        return "FIP"
    
    # Fundos (various types)
    if any(t in desc_upper for t in ["FIM", "FIC", "FUNDO", "FIA", "FIRF", "FCFM", "FFIM", "FIF"]):
        return "FUNDO"
    
    # CDB
    if "CDB" in desc_upper:
        return "CDB"
    
    # LF
    if "LF " in desc_upper or desc_upper.startswith("LF"):
        return "LF"
    
    # Based on section
    if section == "TITULOS_PUBLICOS":
        return "TITULO_PUBLICO"
    if section == "TITULOS_PRIVADOS":
        return "TITULO_PRIVADO"
    if section == "FUNDOS":
        return "FUNDO"
    
    return "OUTROS"


def get_titulo_isin(desc: str, vencimento: str = None) -> dict:
    """Get ISIN for titulo publico"""
    desc_upper = desc.upper()
    
    # Detect type
    tipo = None
    if "LFT" in desc_upper:
        tipo = "LFT"
    elif "LTN" in desc_upper:
        tipo = "LTN"
    elif "NTNB" in desc_upper or "NTN-B" in desc_upper or "IPCA" in desc_upper:
        tipo = "NTNB"
    elif "NTNF" in desc_upper or "NTN-F" in desc_upper:
        tipo = "NTNF"
    
    if not tipo:
        return {"isin": None, "status": "REVISAR"}
    
    # Extract year from vencimento or description
    year = None
    
    if vencimento:
        match = re.search(r"(\d{4})$", vencimento)
        if match:
            year = match.group(1)
    
    if not year:
        match = re.search(r"20(\d{2})", desc)
        if match:
            year = f"20{match.group(1)}"
    
    if year:
        key = f"{tipo}_{year}"
        if key in TITULO_ISIN:
            return {"isin": TITULO_ISIN[key], "status": "OK"}
    
    return {"isin": None, "status": "REVISAR"}


# =============================================================================
# STREAMLIT UI
# =============================================================================

def main():
    st.title("📊 Jera Portfolio Onboarding")
    st.markdown("**Transforme extratos BTG em dados estruturados para o Maravi**")
    
    with st.sidebar:
        st.header("📁 Upload")
        uploaded_file = st.file_uploader(
            "Arraste o PDF aqui",
            type=['pdf'],
            help="Extratos BTG Pactual"
        )
        
        st.divider()
        st.markdown("""
        **Tipos Suportados:**
        - ✅ Fundos (FIM, FIC, FIDC, FIP)
        - ✅ Títulos Públicos (LFT, LTN, NTN-B)
        - ✅ Títulos Privados (CRI, CRA, Deb)
        - ✅ CDB, LF
        """)
    
    if uploaded_file is None:
        st.info("👆 Faça upload de um extrato BTG para começar")
        
        col1, col2, col3 = st.columns(3)
        with col1:
            st.markdown("### 1️⃣ Upload")
            st.markdown("Arraste o PDF")
        with col2:
            st.markdown("### 2️⃣ Processamento")
            st.markdown("Extração automática")
        with col3:
            st.markdown("### 3️⃣ Export")
            st.markdown("Excel para Maravi")
        return
    
    # Process PDF
    with st.spinner("🔄 Processando PDF BTG..."):
        assets, metadata = parse_btg_pdf(uploaded_file)
    
    if not assets:
        st.error("❌ Não foi possível extrair ativos do PDF")
        st.info("Este parser é otimizado para extratos BTG Pactual no formato 'Carteira'")
        return
    
    # Show metadata
    st.success(f"✅ **{metadata.get('fund_name', 'Portfolio')}** - {len(assets)} ativos extraídos")
    
    if metadata.get("nav"):
        st.metric("Patrimônio", f"R$ {metadata['nav']:,.2f}")
    
    # Convert to DataFrame
    df = pd.DataFrame(assets)
    
    # Stats by type
    st.subheader("📊 Resumo por Tipo")
    
    type_stats = df.groupby("type").agg(
        Quantidade=("description", "count"),
        Valor_Total=("value", "sum")
    ).reset_index()
    type_stats["Valor_Total"] = type_stats["Valor_Total"].apply(
        lambda x: f"R$ {x:,.2f}" if pd.notna(x) and x > 0 else "-"
    )
    type_stats.columns = ["Tipo", "Quantidade", "Valor Total"]
    st.dataframe(type_stats, use_container_width=True, hide_index=True)
    
    # Status summary
    col1, col2, col3 = st.columns(3)
    ok_count = len(df[df["status"] == "OK"])
    review_count = len(df[df["status"] == "REVISAR"])
    pending_count = len(df[df["status"] == "PENDENTE"])
    
    with col1:
        st.metric("✅ OK (com ISIN)", ok_count)
    with col2:
        st.metric("⚠️ Revisar", review_count)
    with col3:
        st.metric("⏳ Pendente", pending_count)
    
    # Editable table
    st.subheader("📝 Ativos Extraídos")
    
    # Reorder columns
    display_cols = ["description", "type", "value", "pct_pl", "vencimento", "isin", "status"]
    df_display = df[[c for c in display_cols if c in df.columns]]
    
    edited_df = st.data_editor(
        df_display,
        use_container_width=True,
        num_rows="dynamic",
        column_config={
            "description": st.column_config.TextColumn("Descrição", width="large"),
            "type": st.column_config.SelectboxColumn(
                "Tipo",
                options=["FUNDO", "TITULO_PUBLICO", "CDB", "LF", "DEBENTURE", "CRI", "CRA", "FII", "FIDC", "FIP", "TITULO_PRIVADO", "OUTROS"],
                width="medium"
            ),
            "value": st.column_config.NumberColumn("Valor (R$)", format="%.2f"),
            "pct_pl": st.column_config.NumberColumn("%PL", format="%.2f"),
            "vencimento": st.column_config.TextColumn("Vencimento"),
            "isin": st.column_config.TextColumn("ISIN"),
            "status": st.column_config.SelectboxColumn(
                "Status",
                options=["OK", "REVISAR", "PENDENTE"],
                width="small"
            ),
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
            label="📥 Baixar Excel",
            data=buffer,
            file_name=f"portfolio_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
    
    with col2:
        json_str = edited_df.to_json(orient="records", force_ascii=False, indent=2)
        st.download_button(
            label="📥 Baixar JSON",
            data=json_str,
            file_name=f"portfolio_{datetime.now().strftime('%Y%m%d_%H%M')}.json",
            mime="application/json"
        )


if __name__ == "__main__":
    main()
