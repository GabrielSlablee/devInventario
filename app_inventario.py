# app_inventario.py
# =========================
# Inventário por Serial — FedEx
# SQLite local OU Supabase (PostgreSQL) no Cloud
# =========================
import os
import re
import base64
import io
import csv
from datetime import datetime, date
from pathlib import Path

import streamlit as st
import pandas as pd

# Detecta se está no Streamlit Cloud
IS_CLOUD = os.getenv("HOME") == "/home/appuser"  # heurística confiável

# ===== CONFIG INICIAL =====
st.set_page_config(page_title="Inventário por Serial - FedEx", page_icon="📦", layout="wide")

# ====== CSS (mantido exatamente como você fez) ======
CUSTOM_CSS = """
<style>
:root{
  --fedex-purple:#4D148C;
  --fedex-orange:#FF6600;
  --bg:#f7f7fb;
  --card:#ffffff;
  --line:#ececf1;
  --text:#222;
  --muted:#6b7280;
}

[data-testid="stAppViewContainer"] > .main {
  background: linear-gradient(180deg, #ffffff 0%, var(--bg) 70%);
}
section[data-testid="stSidebar"]{ padding-top: 10px !important; }

.appbar{
  position: sticky; top:0; z-index: 999;
  background: var(--card);
  border: 1px solid var(--line);
  box-shadow: 0 4px 12px rgba(0,0,0,.05);
  border-radius: 14px;
  padding: 10px 14px;
  margin-bottom: 14px;
}
.appbar-title{ font-size: 20px; font-weight: 800; color: var(--text); margin: 0; }
.appbar-sub{ font-size: 12px; color: var(--muted); margin: 2px 0 0 0; }
.user-badge{
  margin-left:auto;
  display:flex; align-items:center; gap:10px;
  background:#fafafa; border:1px solid var(--line);
  padding:8px 10px; border-radius:12px; font-size:13px;
}

.card{
  background: var(--card);
  border: 1px solid var(--line);
  box-shadow: 0 6px 16px rgba(0,0,0,0.06);
  border-radius: 16px;
  padding: 18px 18px;
  margin-top: 10px;
}

.kpi-row{
  display: flex; gap: 12px; margin: 6px 0 12px;
  flex-wrap: nowrap; overflow-x: auto; padding-bottom: 6px;
}
.kpi-ind{
  min-width: 220px; background: var(--card); border:1px solid var(--line);
  border-left: 6px solid var(--accent, var(--fedex-purple));
  border-radius: 14px; padding: 10px 14px;
  box-shadow: 0 6px 16px rgba(0,0,0,0.06);
}
.kpi-ind h4{
  font-size: 12px; color: var(--muted); margin: 0 0 4px 0;
  text-transform: uppercase; letter-spacing: .4px;
}
.kpi-ind .v{
  font-size: 26px; font-weight: 800; color: var(--text); line-height: 1.1;
}
.kpi-ind .hint{
  font-size: 12px; color: var(--muted); margin-top: 2px;
}
.kpi-ind.total   { --accent: var(--fedex-purple); }
.kpi-ind.ok      { --accent: #16a34a; }   /* verde */
.kpi-ind.pend    { --accent: #f59e0b; }   /* amarelo */
.kpi-ind.conf    { --accent: #dc2626; }   /* vermelho */
.kpi-ind.canc    { --accent: #6b7280; }   /* cinza */

.stButton > button{
  border-radius: 10px; padding:.55rem .95rem; font-weight:600;
}
.stButton > button[kind="primary"]{ background: var(--fedex-purple); }
.stButton > button:hover{ filter:brightness(.98); }
input, textarea, select { border-radius: 10px !important; }

.sidebar-top { font-weight: 700; margin-top: 4px; margin-bottom: 10px; }
</style>
"""
st.markdown(CUSTOM_CSS, unsafe_allow_html=True)

# ====== Caminhos ======
LOGO_PATH = Path(__file__).parent / "fedex-seeklogo.png"

# ====== App Bar ======
def render_header():
    logo_html = ""
    if LOGO_PATH.exists():
        b64 = base64.b64encode(LOGO_PATH.read_bytes()).decode()
        logo_html = f'<img src="data:image/png;base64,{b64}" width="120" />'
    st.markdown('<div class="appbar">', unsafe_allow_html=True)
    c1, c2, c3 = st.columns([0.12, 0.68, 0.20])
    with c1:
        if logo_html:
            st.markdown(logo_html, unsafe_allow_html=True)
    with c2:
        st.markdown('<p class="appbar-title">Inventário por Número de Série — FedEx</p>', unsafe_allow_html=True)
        st.markdown('<p class="appbar-sub">FedEx • Registro de leituras, divergências e exportações</p>', unsafe_allow_html=True)
    with c3:
        op = (st.session_state.get("operador") or "-").strip() or "-"
        is_admin = "Admin" if st.session_state.get("is_admin") else "Leitor"
        st.markdown(f"""
            <div class="user-badge">
              <span>👤 {op}</span>
              <span style="font-size:12px;color:#555;">{is_admin}</span>
            </div>
        """, unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)

# =========================
# INTERFACE DE BANCO DE DADOS ABSTRATA
# =========================
class DatabaseInterface:
    """Interface comum para SQLite e PostgreSQL"""
    def mestre_contem(self, serial: str) -> bool:
        raise NotImplementedError

    def inserir_mestre_bulk(self, seriais: list[str]) -> tuple[int, int]:
        raise NotImplementedError

    def registrar_scan(self, serial: str, status: str, operador: str = "", local: str = "", mensagem: str = ""):
        raise NotImplementedError

    def atualizar_status_scan(self, id_scan: int, novo_status: str, mensagem: str = ""):
        raise NotImplementedError

    def ultimo_registro_divergente_pendente(self):
        raise NotImplementedError

    def contadores_globais(self):
        raise NotImplementedError

    def dataframe_ultimas(self, n=50, operador=None, local=None, dt_ini=None, dt_fim=None):
        raise NotImplementedError

# =========================
# IMPLEMENTAÇÃO POSTGRESQL (Supabase)
# =========================
class SupabaseDB(DatabaseInterface):
    def __init__(self):
        import psycopg2
        from psycopg2.extras import RealDictCursor
        if "supabase" not in st.secrets:
            st.error("❌ Secrets do Supabase não configurados no Streamlit Cloud.")
            st.stop()
        self.conn_params = {
            "host": st.secrets["supabase"]["host"],
            "port": st.secrets["supabase"]["port"],
            "database": st.secrets["supabase"]["database"],
            "user": st.secrets["supabase"]["user"],
            "password": st.secrets["supabase"]["password"],
            "sslmode": "require"
        }
        self._init_db()

    def _get_conn(self):
        import psycopg2
        return psycopg2.connect(**self.conn_params)

    def _init_db(self):
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS mestre_seriais (
                        serial TEXT PRIMARY KEY
                    );
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS scans (
                        id BIGSERIAL PRIMARY KEY,
                        serial TEXT NOT NULL,
                        status TEXT NOT NULL,
                        operador TEXT,
                        local TEXT,
                        mensagem TEXT,
                        lido_em TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    );
                """)
                cur.execute("CREATE INDEX IF NOT EXISTS idx_scans_serial ON scans(serial);")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_scans_status ON scans(status);")
            conn.commit()

    def mestre_contem(self, serial: str) -> bool:
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM mestre_seriais WHERE serial = %s", (serial,))
                return cur.fetchone() is not None

    def inserir_mestre_bulk(self, seriais: list[str]) -> tuple[int, int]:
        ok = dup = 0
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                for s in seriais:
                    s = s.strip()
                    if not s: continue
                    try:
                        cur.execute("INSERT INTO mestre_seriais(serial) VALUES (%s)", (s,))
                        ok += 1
                    except:
                        dup += 1
            conn.commit()
        return ok, dup

    def registrar_scan(self, serial: str, status: str, operador: str = "", local: str = "", mensagem: str = ""):
        ts = datetime.now().isoformat()
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO scans (serial, status, operador, local, mensagem, lido_em)
                    VALUES (%s, %s, %s, %s, %s, %s)
                """, (serial, status, operador, local, mensagem, ts))
            conn.commit()

    def atualizar_status_scan(self, id_scan: int, novo_status: str, mensagem: str = ""):
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE scans
                    SET status = %s, mensagem = COALESCE(NULLIF(%s, ''), mensagem)
                    WHERE id = %s
                """, (novo_status, mensagem, id_scan))
            conn.commit()

    def ultimo_registro_divergente_pendente(self):
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT id, serial, to_char(lido_em, 'YYYY-MM-DD HH24:MI:SS'), operador, local, mensagem
                    FROM scans
                    WHERE status = 'DIVERGENCIA'
                    ORDER BY id DESC
                    LIMIT 1
                """)
                return cur.fetchone()

    def contadores_globais(self):
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM scans"); total = cur.fetchone()[0]
                cur.execute("SELECT COUNT(*) FROM scans WHERE status='OK'"); ok = cur.fetchone()[0]
                cur.execute("SELECT COUNT(*) FROM scans WHERE status='DIVERGENCIA'"); pend = cur.fetchone()[0]
                cur.execute("SELECT COUNT(*) FROM scans WHERE status='DIVERGENCIA_CONFIRMADA'"); conf = cur.fetchone()[0]
                cur.execute("SELECT COUNT(*) FROM scans WHERE status='CANCELADA'"); canc = cur.fetchone()[0]
                return total, ok, pend, conf, canc

    def dataframe_ultimas(self, n=50, operador=None, local=None, dt_ini=None, dt_fim=None):
        q = "SELECT id, to_char(lido_em, 'YYYY-MM-DD HH24:MI:SS') as lido_em, serial, status, operador, local, mensagem FROM scans WHERE 1=1"
        params = []
        if operador:
            q += " AND operador = %s"; params.append(operador)
        if local:
            q += " AND local = %s"; params.append(local)
        if dt_ini:
            q += " AND lido_em::date >= %s"; params.append(dt_ini)
        if dt_fim:
            q += " AND lido_em::date <= %s"; params.append(dt_fim)
        q += " ORDER BY id DESC"
        if n:
            q += " LIMIT %s"; params.append(n)

        return pd.read_sql_query(q, self._get_conn(), params=params)

# =========================
# IMPLEMENTAÇÃO SQLITE (local)
# =========================
class SQLiteDB(DatabaseInterface):
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init_db()

    def _get_conn(self):
        import sqlite3
        from contextlib import closing
        conn = sqlite3.connect(self.db_path, check_same_thread=False, isolation_level=None)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA busy_timeout=5000;")
        return closing(conn)

    def _init_db(self):
        with self._get_conn() as conn:
            cur = conn.cursor()
            cur.execute("""
            CREATE TABLE IF NOT EXISTS mestre_seriais (
                serial TEXT PRIMARY KEY
            );
            """)
            cur.execute("""
            CREATE TABLE IF NOT EXISTS scans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                serial TEXT NOT NULL,
                status TEXT NOT NULL,
                operador TEXT,
                local TEXT,
                mensagem TEXT,
                lido_em TEXT NOT NULL
            );
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_scans_serial ON scans(serial);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_scans_status ON scans(status);")

    def mestre_contem(self, serial: str) -> bool:
        with self._get_conn() as conn:
            return conn.execute("SELECT 1 FROM mestre_seriais WHERE serial=?", (serial,)).fetchone() is not None

    def inserir_mestre_bulk(self, seriais: list[str]) -> tuple[int, int]:
        ok = dup = 0
        with self._get_conn() as conn:
            cur = conn.cursor()
            for s in seriais:
                s = s.strip()
                if not s: continue
                try:
                    cur.execute("INSERT INTO mestre_seriais(serial) VALUES(?)", (s,))
                    ok += 1
                except:
                    dup += 1
        return ok, dup

    def registrar_scan(self, serial: str, status: str, operador: str = "", local: str = "", mensagem: str = ""):
        ts = datetime.now().isoformat(timespec="seconds")
        with self._get_conn() as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO scans (serial, status, operador, local, mensagem, lido_em)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (serial, status, operador, local, mensagem, ts))

    def atualizar_status_scan(self, id_scan: int, novo_status: str, mensagem: str = ""):
        with self._get_conn() as conn:
            cur = conn.cursor()
            cur.execute("""
                UPDATE scans
                SET status=?, mensagem=COALESCE(NULLIF(?,''), mensagem)
                WHERE id=?
            """, (novo_status, mensagem, id_scan))

    def ultimo_registro_divergente_pendente(self):
        with self._get_conn() as conn:
            return conn.execute("""
                SELECT id, serial, lido_em, operador, local, mensagem
                FROM scans
                WHERE status='DIVERGENCIA'
                ORDER BY id DESC
                LIMIT 1
            """).fetchone()

    def contadores_globais(self):
        with self._get_conn() as conn:
            total = conn.execute("SELECT COUNT(*) FROM scans").fetchone()[0]
            ok = conn.execute("SELECT COUNT(*) FROM scans WHERE status='OK'").fetchone()[0]
            pend = conn.execute("SELECT COUNT(*) FROM scans WHERE status='DIVERGENCIA'").fetchone()[0]
            conf = conn.execute("SELECT COUNT(*) FROM scans WHERE status='DIVERGENCIA_CONFIRMADA'").fetchone()[0]
            canc = conn.execute("SELECT COUNT(*) FROM scans WHERE status='CANCELADA'").fetchone()[0]
        return total, ok, pend, conf, canc

    def dataframe_ultimas(self, n=50, operador=None, local=None, dt_ini=None, dt_fim=None):
        q = """
            SELECT id, lido_em, serial, status, operador, local, mensagem
            FROM scans
            WHERE 1=1
        """
        params = []
        if operador:
            q += " AND (operador = ?)"; params.append(operador)
        if local:
            q += " AND (local = ?)"; params.append(local)
        if dt_ini:
            q += " AND date(substr(lido_em,1,10)) >= ?"; params.append(dt_ini.isoformat())
        if dt_fim:
            q += " AND date(substr(lido_em,1,10)) <= ?"; params.append(dt_fim.isoformat())
        q += " ORDER BY id DESC"
        if n:
            q += " LIMIT ?"; params.append(n)

        return pd.read_sql_query(q, self._get_conn().connection, params=params)

# =========================
# CRIA INSTÂNCIA DO BANCO
# =========================
if "db" not in st.session_state:
    if IS_CLOUD:
        st.session_state.db = SupabaseDB()
    else:
        DB_PATH = os.path.abspath("inventario.db")
        st.session_state.db = SQLiteDB(DB_PATH)

db = st.session_state.db

# =========================
# FUNÇÕES AUXILIARES
# =========================
def pct(part, total):
    return f"{(part/total*100):.1f}%" if total else "0.0%"

ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "1234")

# =========================
# SIDEBAR
# =========================
with st.sidebar:
    if LOGO_PATH.exists():
        st.image(str(LOGO_PATH), width=120)
    st.markdown('<div class="sidebar-top">FedEx • Inventário Clientes</div>', unsafe_allow_html=True)

    operador = st.text_input("Operador (obrigatório)", value=st.session_state.get("operador", ""))
    local = st.text_input("Local/Setor (opcional)", value=st.session_state.get("local", ""))

    c1, c2 = st.columns(2)
    with c1:
        if st.button("Salvar sessão", use_container_width=True):
            st.session_state["operador"] = operador
            st.session_state["local"] = local
            if operador.strip():
                st.success("Sessão salva.")
            else:
                st.error("Informe o Operador.")
    with c2:
        if st.button("Limpar", use_container_width=True):
            st.session_state["operador"] = ""
            st.session_state["local"] = ""
            st.rerun()

    st.divider()
    st.subheader("Login de Administrador")
    colA, colB = st.columns([2,1])
    with colA:
        pwd = st.text_input("Senha do admin", type="password")
    with colB:
        if st.button("Entrar", use_container_width=True):
            if pwd == ADMIN_PASSWORD:
                st.session_state["is_admin"] = True
                st.success("Você está em modo Admin.")
            else:
                st.error("Senha inválida.")
    if st.session_state.get("is_admin"):
        if st.button("Sair do Admin", use_container_width=True, type="secondary"):
            st.session_state["is_admin"] = False
            st.info("Saiu do modo Admin.")

# =========================
# HEADER + KPIs
# =========================
render_header()

total, okc, pend, conf, canc = db.contadores_globais()
st.markdown(
    f"""
    <div class="kpi-row">
      <div class="kpi-ind total">
        <h4>Leituras Totais</h4>
        <div class="v">{total}</div>
        <div class="hint">📦 registros</div>
      </div>
      <div class="kpi-ind ok">
        <h4>OK</h4>
        <div class="v">{okc}</div>
        <div class="hint">✅ {pct(okc, total)} do total</div>
      </div>
      <div class="kpi-ind pend">
        <h4>Divergências pendentes</h4>
        <div class="v">{pend}</div>
        <div class="hint">⚠️ {pct(pend, total)} do total</div>
      </div>
      <div class="kpi-ind conf">
        <h4>Divergências confirmadas</h4>
        <div class="v">{conf}</div>
        <div class="hint">🛑 {pct(conf, total)} do total</div>
      </div>
      <div class="kpi-ind canc">
        <h4>Canceladas</h4>
        <div class="v">{canc}</div>
        <div class="hint">🗑️ {pct(canc, total)} do total</div>
      </div>
    </div>
    """,
    unsafe_allow_html=True
)

# =========================
# MODO LEITOR
# =========================
st.markdown('<div class="card">', unsafe_allow_html=True)
st.subheader("👷 Modo Leitor")

def processar_leitura():
    serial = (st.session_state.get("entrada_serial") or "").strip()
    if not serial:
        return
    op = (st.session_state.get("operador") or "").strip()
    lc = st.session_state.get("local", "")

    if not op:
        st.session_state["ultimo_resultado"] = ("ERRO", serial, "Informe o Operador.")
        st.session_state["entrada_serial"] = ""
        return

    if db.mestre_contem(serial):
        db.registrar_scan(serial, "OK", op, lc)
        st.session_state["ultimo_resultado"] = ("OK", serial, datetime.now().isoformat(timespec="seconds"))
    else:
        db.registrar_scan(serial, "DIVERGENCIA", op, lc, "Aguardando confirmação")
        st.session_state["ultimo_resultado"] = ("DIVERGENCIA", serial, datetime.now().isoformat(timespec="seconds"))
    st.session_state["entrada_serial"] = ""

st.text_input(
    "Serial",
    key="entrada_serial",
    on_change=processar_leitura,
    placeholder="Use o leitor ou digite e pressione Enter…",
)

if "ultimo_resultado" in st.session_state:
    status, sr, ts = st.session_state["ultimo_resultado"]
    if status == "OK":
        st.success(f"✅ OK • {sr} • {ts}")
    elif status == "DIVERGENCIA":
        st.warning(f"⚠️ Divergência • {sr} • {ts}")
    else:
        st.error(f"🚫 {sr} — {ts}")

st.markdown('</div>', unsafe_allow_html=True)

# =========================
# CONFIRMAÇÃO DE DIVERGÊNCIA
# =========================
st.markdown('<div class="card">', unsafe_allow_html=True)
st.subheader("Confirmação de Divergências")
pendente = db.ultimo_registro_divergente_pendente()
if pendente:
    id_scan, serial_d, lido_em, op_d, loc_d, msg_d = pendente
    with st.container(border=True):
        st.write(f"**ID:** {id_scan} | **Serial:** `{serial_d}` | **Lido em:** {lido_em}")
        st.write(f"**Operador:** {op_d or '-'} | **Local:** {loc_d or '-'}")
        obs = st.text_input("Observação (opcional)", key=f"obs_{id_scan}")
        b1, b2, _ = st.columns([1,1,6])
        if b1.button("✅ Confirmar divergência", key=f"conf_{id_scan}"):
            db.atualizar_status_scan(id_scan, "DIVERGENCIA_CONFIRMADA", obs)
            st.success("Divergência confirmada.")
            st.rerun()
        if b2.button("❌ Cancelar leitura", key=f"canc_{id_scan}"):
            db.atualizar_status_scan(id_scan, "CANCELADA", obs or "Cancelada pelo operador")
            st.info("Leitura cancelada.")
            st.rerun()
else:
    st.info("Nenhuma divergência pendente.")
st.markdown('</div>', unsafe_allow_html=True)

# =========================
# EXPORTAÇÃO DO LEITOR
# =========================
st.markdown('<div class="card">', unsafe_allow_html=True)
st.subheader("📤 Exportar minhas leituras")
c1, c2, c3 = st.columns(3)
with c1:
    dt_ini = st.date_input("Data inicial", value=date.today())
with c2:
    dt_fim = st.date_input("Data final", value=date.today())
with c3:
    limite = st.number_input("Limite de linhas (0 = sem limite)", min_value=0, value=1000)

op_atual = (st.session_state.get("operador") or "").strip()
lc_atual = st.session_state.get("local", "")

if st.button("Gerar CSV (minhas leituras)"):
    if not op_atual:
        st.error("Informe o Operador na barra lateral.")
    else:
        n = None if limite == 0 else int(limite)
        df_user = db.dataframe_ultimas(n=n, operador=op_atual, local=lc_atual or None, dt_ini=dt_ini, dt_fim=dt_fim)
        if df_user.empty:
            st.warning("Nenhum registro encontrado.")
        else:
            csv_bytes = df_user.to_csv(index=False).encode("utf-8-sig")
            st.download_button("⬇️ Baixar CSV", csv_bytes, file_name=f"leituras_{op_atual}_{dt_ini}_{dt_fim}.csv", mime="text/csv", use_container_width=True)
st.markdown('</div>', unsafe_allow_html=True)

# =========================
# MODO ADMIN
# =========================
if st.session_state.get("is_admin"):
    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.header("🛠️ Modo Admin")

    st.subheader("Base Mestre — Importar/Atualizar")
    up_txt = st.file_uploader("TXT (um serial por linha)", type=["txt"])
    up_csv = st.file_uploader("CSV (coluna serial ou 1ª coluna)", type=["csv"])

    if st.button("Importar base mestre", type="primary"):
        seriais = []
        if up_txt:
            seriais.extend(up_txt.read().decode("utf-8").splitlines())
        if up_csv:
            df = pd.read_csv(up_csv)
            col0 = df.columns[0]
            seriais.extend(df[col0].astype(str).str.strip().tolist())

        if seriais:
            ok, dup = db.inserir_mestre_bulk(seriais)
            st.success(f"Base atualizada. Inseridos: {ok}, já existiam: {dup}")
        else:
            st.warning("Nenhum arquivo carregado.")
    st.markdown('</div>', unsafe_allow_html=True)

    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.subheader("📦 Exportar leituras (geral)")
    g1, g2, g3, g4 = st.columns(4)
    with g1:
        dt_ini_g = st.date_input("Data inicial", date.today(), key="dt_ini_g")
    with g2:
        dt_fim_g = st.date_input("Data final", date.today(), key="dt_fim_g")
    with g3:
        operador_g = st.text_input("Operador (opcional)")
    with g4:
        local_g = st.text_input("Local (opcional)")

    if st.button("Gerar CSV Geral"):
        df_g = db.dataframe_ultimas(None, operador_g or None, local_g or None, dt_ini_g, dt_fim_g)
        if df_g.empty:
            st.warning("Sem registros.")
        else:
            csv_g = df_g.to_csv(index=False).encode("utf-8-sig")
            st.download_button("⬇️ Baixar CSV Geral", csv_g, file_name=f"leituras_geral_{dt_ini_g}_{dt_fim_g}.csv", use_container_width=True)
    st.markdown('</div>', unsafe_allow_html=True)

# =========================
# VISUALIZAÇÃO RÁPIDA
# =========================
st.markdown('<div class="card">', unsafe_allow_html=True)
st.subheader("Últimas leituras (50)")
def color_row(r):
    if r["status"] == "OK":
        return ["background-color: #e8ffe8"] * len(r)
    if r["status"] == "DIVERGENCIA":
        return ["background-color: #fff3cd"] * len(r)
    if r["status"] == "DIVERGENCIA_CONFIRMADA":
        return ["background-color: #ffe5e5"] * len(r)
    if r["status"] == "CANCELADA":
        return ["background-color: #f0f0f0"] * len(r)
    return [""] * len(r)

df_preview = db.dataframe_ultimas(50)
if df_preview.empty:
    st.info("Nenhuma leitura registrada ainda.")
else:
    st.dataframe(df_preview.style.apply(color_row, axis=1), use_container_width=True, hide_index=True)
st.markdown('</div>', unsafe_allow_html=True)