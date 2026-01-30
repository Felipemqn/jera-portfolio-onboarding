"""
JERA PORTFOLIO ONBOARDING v7
============================
Com mapeamento manual completo de CNPJs
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

# =============================================================================
# MAPEAMENTO MANUAL COMPLETO DE CNPJs
# Baseado na lista corrigida do Felipe
# =============================================================================

MANUAL_CNPJ = {
    # Fundos com match exato (nome completo)
    "BLC III FIM CP": "28.153.046/0001-60",
    "CS ALL MAR AB FICFIM": "07.766.151/0001-02",
    "CS CERES I DEB FIM": "30.507.983/0001-18",
    "CSHG A VISTA MU FCFM": "31.525.248/0001-08",
    "CSHG ALL KAP Z FC FM": "25.036.554/0001-70",
    "CSHG ALLOC LEG FCFIM": "42.596.861/0001-24",  # LEGACY
    "CSHG ALLOCATION FIM": "01.626.802/0001-74",
    "CSHG JIVE D FIC FIM": "21.556.491/0001-21",  # DISTRESSED
    "CSHG JIVE III FICFIM": "35.865.564/0001-71",  # III
    "G5 TERRAS FIM CP": "04.869.180/0001-01",
    "HIX SAAS I FEE FIP": "42.246.602/0001-73",
    "JBFO NITRO FIC FIDC": "30.556.626/0001-40",  # Exclusivo BTG
    "JBFO SPS FIC FIM CP": "30.077.132/0001-82",  # SPS I
    "K ZEVA FFIM": "41.853.515/0001-11",  # KAPITALO ZETA
    "LUMINA FDR BR I FIM": "04.685.125/0001-53",
    "MILAS II PF FIM CP": "36.517.750/0001-82",  # Exclusivo
    "PATRIA CRED FIDC SUB": "19.729.263/0001-64",
    "SPE SITUATIONSFCFIDC": "00.306.559/0001-44",
    "SPECTRA BRIDGE FIC": "18.686.779/0001-06",  # BRIDGE FIM
    "SPECTRA GAUDI FIM CP": "23.732.047/0001-45",  # CSHG GAUDI
    "SPECTRA LATIN EQ III": "27.292.940/0001-58",  # VIC SPECTRA LATIN
    "SPX CAPITAL PLUS FIQ": "30.609.175/0001-61",
    "TESOURO SELIC FI RF": "04.857.834/0001-79",
    "TREECORP TRATOR FIP": "13.950.067/0001-39",
    "VIC CPHY FIC FIM CP": "35.271.142/0001-78",  # VIC JBFO ADAM
    "VIC QIADRA ML II FIM": "34.081.573/0001-09",  # VIC QUADRA
    "VIC QUATA FICFIM CP": "29.734.070/0001-55",  # VIC BAHIA AM
    "VIC REC IMOB FIM CP": "30.492.962/0001-76",
    "VIC ROOT CAP FIC FIM": "18.289.982/0001-49",
    
    # Variações de nome (parciais)
    "MISSION 1.1 T5 FIP": "40.132.943/0001-92",
    "MW EUREKA FUND": "00.000.000/0001-00",  # Fundo offshore
    "OAKTREE REAL ESTATE": "00.000.000/0001-00",  # Fundo offshore
    "RF CDB": "00.000.000/0001-00",  # CDB não tem CNPJ de fundo
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
# CVM FUND LOOKUP (Fallback)
# =============================================================================

@st.cache_data(ttl=3600, show_spinner=False)
def load_cvm_data():
    try:
        url = "https://dados.cvm.gov.br/dados/FI/CAD/DADOS/cad_fi.csv"
        df = pd.read_csv(url, sep=";", encoding="latin1", low_memory=False)
        return df
    except:
        return None

def find_cnpj_cvm(fund_name: str, cvm_df: pd.DataFrame, used_cnpjs: set) -> str:
    """Busca na CVM como fallback"""
    if cvm_df is None:
        return None
    
    name_upper = fund_name.upper()
    skip_words = {"FIM", "FIC", "FIDC", "FIP", "FCFM", "FFIM", "FIQ", "FC", "FM", "CP", "RF", "FI"}
    words = [w for w in name_upper.split() if w not in skip_words and len(w) >= 3]
    
    if not words:
        return None
    
    candidates = cvm_df.copy()
    for word in words:
        new_cand = candidates[candidates["DENOM_SOCIAL"].str.upper().str.contains(word, na=False, regex=False)]
        if len(new_cand) > 0:
            candidates = new_cand
        if len(candidates) <= 3:
            break
    
    if len(candidates) > 0:
        for _, row in candidates.iterrows():
            cnpj = str(row["CNPJ_FUNDO"])
            if cnpj not in used_cnpjs:
                return cnpj
    
    return None

def find_cnpj(fund_name: str, cvm_df: pd.DataFrame, used_cnpjs: set) -> tuple:
    """Busca CNPJ: primeiro manual, depois CVM"""
    name_clean = fund_name.strip().upper()
    
    # 1. Busca exata no mapeamento manual
    if fund_name in MANUAL_CNPJ:
        cnpj = MANUAL_CNPJ[fund_name]
        if cnpj != "00.000.000/0001-00":
            return cnpj, "MANUAL"
        else:
            return None, "OFFSHORE"
    
    # 2. Busca parcial no mapeamento manual
    for key, cnpj in MANUAL_CNPJ.items():
        if key in name_clean or name_clean in key:
            if cnpj != "00.000.000/0001-00":
                return cnpj, "MANUAL"
    
    # 3. Fallback para CVM
    cnpj = find_cnpj_cvm(fund_name, cvm_df, used_cnpjs)
    if cnpj:
        return cnpj, "CVM"
    
    return None, None

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
            
            # FUNDOS
            fund_pattern = r"(\d+)-\s*([A-Z][A-Z0-9\s]+?(?:FI[MCAD]?|FIC|FIDC|FIP|FIF|FCFM|FFIM|FC FM|FIM CP|FICFIM|FIC FIM|FIQ|FI RF)[A-Z\s]*?)\s+(\d+\.\d{2})\s+([\d,]+\.\d{2})"
            
            for match in re.finditer(fund_pattern, all_text):
                name = match.group(2).strip()
                pct_pl = float(match.group(3))
                value = float(match.group(4).replace(",", ""))
                
                cnpj, source = find_cnpj(name, cvm_df, used_cnpjs)
                
                if cnpj:
                    used_cnpjs.add(cnpj)
                
                status = "OK" if cnpj else "PENDENTE"
                
                assets.append({
                    "description": name,
                    "type": "FUNDO",
                    "pct_pl": pct_pl,
                    "value": value,
                    "vencimento": None,
                    "isin": None,
                    "cnpj": cnpj,
                    "fonte": source,
                    "status": status
                })
            
            # TITULOS PUBLICOS
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
                    "fonte": "ISIN" if isin else None,
                    "status": "OK" if isin else "REVISAR"
                })
            
            # TITULOS PRIVADOS
            privado_pattern = r"(\d+)-\s*(CRI|CRA|GYRA\d*|PRLK\d*)[A-Z0-9\s]*\s+(\d+\.\d{2})\s+([\d,]+\.\d{2})"
            
            for match in re.finditer(privado_pattern, all_text):
                name = match.group(2)
                pct_pl = float(match.group(3))
                value = float(match.group(4).replace(",", ""))
                
                tipo = "CRI" if "CRI" in name.upper() else ("CRA" if "CRA" in name.upper() else "DEBENTURE")
                
                assets.append({
                    "description": name,
                    "type": tipo,
                    "pct_pl": pct_pl,
                    "value": value,
                    "vencimento": None,
                    "isin": None,
                    "cnpj": None,
                    "fonte": None,
                    "status": "PENDENTE"
                })
            
            # Remove duplicates
            seen = set()
            unique = []
            for a in assets:
                key = f"{a['description']}_{a['value']}"
                if key not in seen:
                    seen.add(key)
                    unique.append(a)
            assets = unique
    
    finally:
        Path(tmp_path).unlink(missing_ok=True)
    
    return assets, metadata

# =============================================================================
# STREAMLIT UI
# =============================================================================

def main():
    st.title("📊 Jera Portfolio Onboarding")
    st.markdown("**Extratos BTG → Dados com CNPJ/ISIN**")
    
    with st.spinner("📚 Carregando base CVM..."):
        cvm_df = load_cvm_data()
    
    with st.sidebar:
        st.header("📁 Upload")
        uploaded_file = st.file_uploader("Arraste o PDF aqui", type=['pdf'])
        
        st.divider()
        st.markdown(f"✅ {len(MANUAL_CNPJ)} fundos mapeados")
        if cvm_df is not None:
            st.markdown(f"✅ {len(cvm_df):,} fundos CVM")
    
    if uploaded_file is None:
        st.info("👆 Faça upload de um extrato BTG")
        return
    
    with st.spinner("🔄 Processando..."):
        assets, metadata = parse_btg_pdf(uploaded_file, cvm_df)
    
    if not assets:
        st.error("❌ Não foi possível extrair ativos")
        return
    
    st.success(f"✅ **{metadata.get('fund_name', 'Portfolio')}** - {len(assets)} ativos")
    
    if metadata.get("nav"):
        st.metric("Patrimônio", f"R$ {metadata['nav']:,.2f}")
    
    df = pd.DataFrame(assets)
    
    # Stats
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Total", len(df))
    with col2:
        st.metric("✅ OK", len(df[df["status"] == "OK"]))
    with col3:
        st.metric("🟡 Revisar", len(df[df["status"] == "REVISAR"]))
    with col4:
        st.metric("🔴 Pendente", len(df[df["status"] == "PENDENTE"]))
    
    # By type
    type_stats = df.groupby("type").agg(
        Qtd=("description", "count"),
        Valor=("value", "sum"),
        OK=("status", lambda x: (x == "OK").sum())
    ).reset_index()
    type_stats["Valor"] = type_stats["Valor"].apply(lambda x: f"R$ {x:,.2f}")
    st.dataframe(type_stats, use_container_width=True, hide_index=True)
    
    # Table
    st.subheader("📝 Ativos")
    
    cols = ["description", "type", "pct_pl", "value", "cnpj", "isin", "fonte", "status"]
    df_display = df[[c for c in cols if c in df.columns]]
    
    edited_df = st.data_editor(
        df_display,
        use_container_width=True,
        column_config={
            "description": st.column_config.TextColumn("Descrição", width="large"),
            "type": st.column_config.TextColumn("Tipo"),
            "pct_pl": st.column_config.NumberColumn("%PL", format="%.2f"),
            "value": st.column_config.NumberColumn("Valor (R$)", format="%.2f"),
            "cnpj": st.column_config.TextColumn("CNPJ", width="medium"),
            "isin": st.column_config.TextColumn("ISIN"),
            "fonte": st.column_config.TextColumn("Fonte"),
            "status": st.column_config.TextColumn("Status"),
        },
        hide_index=True,
    )
    
    # Export
    st.subheader("💾 Exportar")
    
    col1, col2 = st.columns(2)
    
    with col1:
        buffer = io.BytesIO()
        export_df = edited_df.drop(columns=["fonte"], errors="ignore")
        export_df.to_excel(buffer, index=False, sheet_name="Ativos")
        buffer.seek(0)
        st.download_button(
            "📥 Baixar Excel",
            data=buffer,
            file_name=f"portfolio_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
    
    with col2:
        export_df = edited_df.drop(columns=["fonte"], errors="ignore")
        st.download_button(
            "📥 Baixar JSON",
            data=export_df.to_json(orient="records", force_ascii=False, indent=2),
            file_name=f"portfolio_{datetime.now().strftime('%Y%m%d_%H%M')}.json",
            mime="application/json"
        )

if __name__ == "__main__":
    main()
