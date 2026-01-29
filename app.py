"""
JERA PORTFOLIO ONBOARDING
=========================
Aplicativo web completo - só abrir no navegador!
"""

import streamlit as st
import pandas as pd
import pdfplumber
import re
import json
import requests
from pathlib import Path
from datetime import datetime
import io
import tempfile

# =============================================================================
# CONFIG
# =============================================================================

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
    "NTNB_2025": "BRSTNCNTB4K4", "NTNB_2026": "BRSTNCNTB4L2", "NTNB_2027": "BRSTNCNTB4M0",
    "NTNB_2028": "BRSTNCNTB4N8", "NTNB_2029": "BRSTNCNTB4O6", "NTNB_2030": "BRSTNCNTB4P3",
    "NTNB_2032": "BRSTNCNTB4Q1", "NTNB_2035": "BRSTNCNTB4R9", "NTNB_2040": "BRSTNCNTB4S7",
    "NTNB_2045": "BRSTNCNTB4T5", "NTNB_2050": "BRSTNCNTB4U3", "NTNB_2055": "BRSTNCNTB4V1",
    "NTNF_2025": "BRSTNCNTF1R8", "NTNF_2027": "BRSTNCNTF1S6", "NTNF_2029": "BRSTNCNTF1T4",
    "NTNF_2031": "BRSTNCNTF1U2", "NTNF_2033": "BRSTNCNTF1V0", "NTNF_2035": "BRSTNCNTF1W8",
}

# =============================================================================
# ASSET CLASSIFIER
# =============================================================================

ASSET_PATTERNS = {
    "TITULO_PUBLICO": [r"LFT", r"LTN", r"NTN-?[BF]", r"TESOURO"],
    "FUNDO": [r"FI\s", r"FIC\s", r"FUNDO", r"FIA", r"FIM", r"FIRF"],
    "CDB": [r"\bCDB\b"],
    "LF": [r"\bLF\b", r"LETRA FINANCEIRA"],
    "DEBENTURE": [r"DEB[ÊE]NTURE", r"\bDEB\b"],
    "CRI": [r"\bCRI\b", r"CRI\s"],
    "CRA": [r"\bCRA\b", r"CRA\s"],
    "FII": [r"\bFII\b", r"FUNDO.*IMOB", r"[A-Z]{4}11"],
    "ACAO_BR": [r"[A-Z]{4}[34]", r"PETROBRAS", r"VALE", r"ITAU"],
    "ACAO_US": [r"APPLE", r"AMAZON", r"GOOGLE", r"MICROSOFT", r"NASDAQ"],
    "CASH": [r"CAIXA", r"DISPONIB", r"SALDO"],
}

def classify_asset(description: str) -> str:
    desc_upper = description.upper()
    for asset_type, patterns in ASSET_PATTERNS.items():
        for pattern in patterns:
            if re.search(pattern, desc_upper):
                return asset_type
    return "OUTROS"

# =============================================================================
# PDF PARSER
# =============================================================================

def parse_pdf(pdf_file) -> tuple:
    """Parse PDF and extract assets"""
    assets = []
    metadata = {"fund_name": "", "nav": None, "report_type": ""}
    
    with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as tmp:
        tmp.write(pdf_file.read())
        tmp_path = tmp.name
    
    try:
        with pdfplumber.open(tmp_path) as pdf:
            all_text = ""
            for page in pdf.pages:
                text = page.extract_text() or ""
                all_text += text + "\n"
                
                # Extract tables
                tables = page.extract_tables()
                for table in tables:
                    if not table:
                        continue
                    for row in table:
                        if not row or len(row) < 2:
                            continue
                        
                        # First non-empty cell as description
                        desc = ""
                        value = None
                        
                        for cell in row:
                            if cell and not desc:
                                desc = str(cell).strip()
                            elif cell and desc:
                                # Try to parse as number
                                try:
                                    val_str = str(cell).replace(".", "").replace(",", ".")
                                    val_str = re.sub(r"[^\d.-]", "", val_str)
                                    if val_str:
                                        value = float(val_str)
                                except:
                                    pass
                        
                        if desc and len(desc) > 3:
                            asset_type = classify_asset(desc)
                            if asset_type != "CASH":
                                assets.append({
                                    "description": desc[:100],
                                    "type": asset_type,
                                    "value": value,
                                    "isin": None,
                                    "cnpj": None,
                                    "status": "PENDENTE"
                                })
            
            # Extract metadata
            if "RELATÓRIO GERENCIAL" in all_text.upper():
                metadata["report_type"] = "Gerencial"
            else:
                metadata["report_type"] = "Carteira"
            
            # Try to find fund name
            match = re.search(r"(FI\s+MULT[^\n]+|FIC[^\n]+FUNDO[^\n]+)", all_text)
            if match:
                metadata["fund_name"] = match.group(1)[:80]
    
    finally:
        Path(tmp_path).unlink(missing_ok=True)
    
    return assets, metadata

# =============================================================================
# ENRICHMENT
# =============================================================================

def enrich_titulo(desc: str) -> dict:
    """Enrich titulo publico with ISIN"""
    desc_upper = desc.upper()
    
    # Detect type
    tipo = None
    if "LFT" in desc_upper or "TESOURO SELIC" in desc_upper:
        tipo = "LFT"
    elif "LTN" in desc_upper or "TESOURO PREFIXADO" in desc_upper:
        tipo = "LTN"
    elif "NTN-B" in desc_upper or "NTNB" in desc_upper or "TESOURO IPCA" in desc_upper:
        tipo = "NTNB"
    elif "NTN-F" in desc_upper or "NTNF" in desc_upper:
        tipo = "NTNF"
    
    if not tipo:
        return {"isin": None, "status": "REVISAR"}
    
    # Find year
    year_match = re.search(r"20(\d{2})", desc)
    if year_match:
        year = f"20{year_match.group(1)}"
        key = f"{tipo}_{year}"
        if key in TITULO_ISIN:
            return {"isin": TITULO_ISIN[key], "status": "OK"}
    
    return {"isin": None, "status": "REVISAR"}

def search_openfigi(query: str) -> dict:
    """Search OpenFIGI for ISIN"""
    try:
        headers = {
            "Content-Type": "application/json",
            "X-OPENFIGI-APIKEY": OPENFIGI_API_KEY
        }
        
        # Try as ticker first
        data = [{"idType": "TICKER", "idValue": query, "exchCode": "BZ"}]
        response = requests.post(
            "https://api.openfigi.com/v3/mapping",
            headers=headers,
            json=data,
            timeout=5
        )
        
        if response.status_code == 200:
            result = response.json()
            if result and result[0].get("data"):
                r = result[0]["data"][0]
                return {
                    "found": True,
                    "name": r.get("name"),
                    "figi": r.get("figi"),
                    "ticker": r.get("ticker")
                }
    except:
        pass
    
    return {"found": False}

# =============================================================================
# STREAMLIT UI
# =============================================================================

def main():
    st.title("📊 Jera Portfolio Onboarding")
    st.markdown("**Transforme extratos PDF em dados estruturados para o Maravi**")
    
    # Sidebar
    with st.sidebar:
        st.header("📁 Upload")
        uploaded_file = st.file_uploader(
            "Arraste o PDF aqui",
            type=['pdf'],
            help="Extratos BTG, Bradesco, Santander..."
        )
        
        st.divider()
        st.header("⚙️ Configurações")
        auto_enrich = st.checkbox("Enriquecer automaticamente", value=True)
        
        st.divider()
        st.markdown("""
        **APIs Integradas:**
        - ✅ OpenFIGI (ações)
        - ✅ Títulos Públicos
        - 🔄 CVM (fundos)
        """)
    
    # Main content
    if uploaded_file is None:
        st.info("👆 Faça upload de um extrato PDF para começar")
        
        # Demo
        st.subheader("Como funciona:")
        col1, col2, col3 = st.columns(3)
        with col1:
            st.markdown("### 1️⃣ Upload")
            st.markdown("Arraste o PDF do extrato")
        with col2:
            st.markdown("### 2️⃣ Processamento")
            st.markdown("IA classifica e enriquece")
        with col3:
            st.markdown("### 3️⃣ Export")
            st.markdown("Baixe Excel para Maravi")
        
        return
    
    # Process PDF
    with st.spinner("🔄 Processando PDF..."):
        assets, metadata = parse_pdf(uploaded_file)
    
    if not assets:
        st.error("❌ Não foi possível extrair ativos do PDF")
        return
    
    # Show metadata
    st.success(f"✅ Encontrados **{len(assets)}** ativos")
    
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Total de Ativos", len(assets))
    with col2:
        st.metric("Tipo de Relatório", metadata.get("report_type", "N/A"))
    with col3:
        types = pd.Series([a["type"] for a in assets]).value_counts()
        st.metric("Tipos Diferentes", len(types))
    
    # Enrich assets
    if auto_enrich:
        with st.spinner("🔍 Enriquecendo dados..."):
            for asset in assets:
                if asset["type"] == "TITULO_PUBLICO":
                    result = enrich_titulo(asset["description"])
                    asset["isin"] = result.get("isin")
                    asset["status"] = result.get("status", "PENDENTE")
                elif asset["type"] in ["ACAO_BR", "FII"]:
                    # Try to extract ticker
                    ticker_match = re.search(r"([A-Z]{4})[34]|([A-Z]{4})11", asset["description"].upper())
                    if ticker_match:
                        ticker = ticker_match.group(1) or ticker_match.group(2)
                        result = search_openfigi(ticker + ("4" if asset["type"] == "ACAO_BR" else "11"))
                        if result.get("found"):
                            asset["status"] = "OK"
                            asset["enriched_name"] = result.get("name")
    
    # Convert to DataFrame
    df = pd.DataFrame(assets)
    
    # Stats by type
    st.subheader("📊 Resumo por Tipo")
    type_stats = df.groupby("type").agg({
        "description": "count",
        "value": "sum"
    }).rename(columns={"description": "Quantidade", "value": "Valor Total"})
    type_stats["Valor Total"] = type_stats["Valor Total"].apply(lambda x: f"R$ {x:,.2f}" if pd.notna(x) else "-")
    st.dataframe(type_stats, use_container_width=True)
    
    # Status summary
    st.subheader("📈 Status do Enriquecimento")
    col1, col2, col3 = st.columns(3)
    
    ok_count = len(df[df["status"] == "OK"])
    review_count = len(df[df["status"] == "REVISAR"])
    pending_count = len(df[df["status"] == "PENDENTE"])
    
    with col1:
        st.metric("✅ OK", ok_count, f"{100*ok_count/len(df):.0f}%")
    with col2:
        st.metric("⚠️ Revisar", review_count)
    with col3:
        st.metric("⏳ Pendente", pending_count)
    
    # Editable table
    st.subheader("📝 Ativos Extraídos")
    st.markdown("*Edite os valores conforme necessário*")
    
    edited_df = st.data_editor(
        df,
        use_container_width=True,
        num_rows="dynamic",
        column_config={
            "description": st.column_config.TextColumn("Descrição", width="large"),
            "type": st.column_config.SelectboxColumn(
                "Tipo",
                options=["TITULO_PUBLICO", "FUNDO", "CDB", "LF", "DEBENTURE", "CRI", "CRA", "FII", "ACAO_BR", "ACAO_US", "OUTROS"],
                width="medium"
            ),
            "value": st.column_config.NumberColumn("Valor", format="R$ %.2f"),
            "isin": st.column_config.TextColumn("ISIN"),
            "cnpj": st.column_config.TextColumn("CNPJ"),
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
        # Excel export
        buffer = io.BytesIO()
        edited_df.to_excel(buffer, index=False, sheet_name="Ativos")
        buffer.seek(0)
        
        st.download_button(
            label="📥 Baixar Excel",
            data=buffer,
            file_name=f"portfolio_onboarding_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
    
    with col2:
        # JSON export
        json_str = edited_df.to_json(orient="records", force_ascii=False, indent=2)
        
        st.download_button(
            label="📥 Baixar JSON",
            data=json_str,
            file_name=f"portfolio_onboarding_{datetime.now().strftime('%Y%m%d_%H%M')}.json",
            mime="application/json"
        )
    
    # Manual search
    st.divider()
    st.subheader("🔍 Busca Manual")
    
    search_query = st.text_input("Buscar ativo (ticker, ISIN, nome)")
    if search_query:
        with st.spinner("Buscando..."):
            result = search_openfigi(search_query)
            if result.get("found"):
                st.success(f"✅ Encontrado: **{result.get('name')}**")
                st.json(result)
            else:
                st.warning("Não encontrado no OpenFIGI")


if __name__ == "__main__":
    main()
