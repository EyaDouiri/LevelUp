"""Planner clair pour le registre CSV. Lancer avec: streamlit run dashboard.py"""
from pathlib import Path

import pandas as pd
import streamlit as st

from dedup import REGISTRY_PATH, init_db

REGISTRY = REGISTRY_PATH
STATUSES = ["prepared", "draft_created", "applied", "interview", "offer", "rejected", "archived"]
LABELS = {"prepared": "A préparer", "draft_created": "Brouillon", "applied": "Envoyée",
          "interview": "Entretien", "offer": "Offre reçue", "rejected": "Refusée", "archived": "Archivée"}

st.set_page_config(page_title="LevelUp | Job planner", page_icon="L", layout="wide")
st.markdown("""
<style>
    .stApp { background: #f8f5ef; color: #202b35; }
    [data-testid="stSidebar"] { background: #203b3a; }
    [data-testid="stSidebar"] * { color: #f8f5ef; }
    h1 { color: #203b3a; font-family: Georgia, serif; font-size: 3rem; }
    h2, h3 { color: #203b3a; font-family: Georgia, serif; }
    [data-testid="stMetric"] { background: #fffdf9; border: 1px solid #e6ded1; padding: 16px; border-radius: 8px; }
    div[data-testid="stVerticalBlockBorderWrapper"] { background: #fffdf9; border-color: #e6ded1; }
</style>
""", unsafe_allow_html=True)
init_db()

st.title("LevelUp")
st.caption("Ton espace de suivi pour les stages ciblés, les brouillons et les relances.")

if not REGISTRY.exists():
    st.info("Aucune candidature. Lance d'abord `python main.py --max 5`.")
    st.stop()

applications = pd.read_csv(REGISTRY, encoding="utf-8-sig").fillna("")
if applications.empty:
    st.info("Aucune candidature enregistrée.")
    st.stop()
applications.insert(0, "row_id", range(len(applications)))
counts = applications["status"].value_counts()
metrics = st.columns(4)
metrics[0].metric("Ciblées", len(applications))
metrics[1].metric("A préparer", int(counts.get("prepared", 0)))
metrics[2].metric("Brouillons", int(counts.get("draft_created", 0)))
metrics[3].metric("Envoyées", int(counts.get("applied", 0)))

with st.sidebar:
    st.header("Vue planner")
    selected = st.multiselect("Statuts", STATUSES, format_func=lambda value: LABELS[value])
    company = st.text_input("Chercher une société")
    st.divider()
    st.caption("Les changements sont enregistrés directement dans le CSV.")

filtered = applications
if selected:
    filtered = filtered[filtered.status.isin(selected)]
if company:
    filtered = filtered[filtered.company.str.contains(company, case=False, na=False)]

st.subheader(f"Pipeline · {len(filtered)} offres")
for _, application in filtered.iterrows():
    row_id = int(application.row_id)
    with st.container(border=True):
        title_col, status_col, action_col = st.columns([5, 1.5, 1.5])
        title_col.markdown(f"### {application.title}\n{application.company}")
        new_status = status_col.selectbox("Statut", STATUSES, index=STATUSES.index(application.status) if application.status in STATUSES else 0,
                                          format_func=lambda value: LABELS[value], key=f"status-{row_id}")
        if new_status != application.status:
            applications.loc[applications.row_id == row_id, "status"] = new_status
            applications.drop(columns="row_id").to_csv(REGISTRY, index=False, encoding="utf-8-sig")
            st.rerun()
        action_col.link_button("Voir l'offre", application.url)
        if application.get("gmail_draft_id", ""):
            st.link_button("Ouvrir le brouillon Gmail", f"https://mail.google.com/mail/u/0/#drafts/{application.gmail_draft_id}")
        with st.expander("Détails de la candidature"):
            st.write(f"**Angle CV :** {application.cv_variant or 'Non renseigné'}")
            st.write(f"**Date de l'offre :** {application.offer_date or 'Inconnue'}")
            st.write(f"**Créée le :** {application.created_at}")
            st.write(application.email_body or "Corps de mail non disponible dans ce registre.")
