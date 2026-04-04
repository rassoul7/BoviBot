"""
BoviBot — Backend FastAPI Complet
Auth + CRUD complet + LLM + PL/SQL
Projet L3 — ESP/UCAD
"""

from fastapi import FastAPI, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
import mysql.connector
import os, re, json, httpx, hashlib, secrets
from dotenv import load_dotenv 

load_dotenv()

app = FastAPI(title="BoviBot API", version="2.0.0")

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ── Config ─────────────────────────────────────────────────
DB_CONFIG = {
    "host":     os.getenv("DB_HOST", "localhost"),
    "user":     os.getenv("DB_USER", "bovibot"),
    "password": os.getenv("DB_PASSWORD", "bovibot123"),
    "database": os.getenv("DB_NAME", "bovibot"),
}
LLM_API_KEY  = os.getenv("OPENAI_API_KEY", "")
LLM_MODEL    = os.getenv("LLM_MODEL", "llama-3.3-70b-versatile")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.groq.com/openai/v1")

# ── DB helpers ─────────────────────────────────────────────
def get_db():
    return mysql.connector.connect(**DB_CONFIG)

def qry(sql: str, params=None):
    conn = get_db()
    cur  = conn.cursor(dictionary=True)
    try:
        cur.execute(sql, params or ())
        return cur.fetchall()
    finally:
        cur.close(); conn.close()

def exe(sql: str, params=None):
    conn = get_db()
    cur  = conn.cursor()
    try:
        cur.execute(sql, params or ())
        conn.commit()
        return cur.lastrowid
    finally:
        cur.close(); conn.close()

# ── Auth helpers ────────────────────────────────────────────
def hash_pwd(pwd: str) -> str:
    return hashlib.sha256(pwd.encode()).hexdigest()

def make_token() -> str:
    return secrets.token_hex(32)

def get_user(authorization: str = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Non authentifié")
    token = authorization.split(" ")[1]
    rows = qry("SELECT u.* FROM tokens t JOIN utilisateurs u ON t.user_id=u.id WHERE t.token=%s", (token,))
    if not rows:
        raise HTTPException(401, "Token invalide ou expiré")
    return rows[0]

def opt_user(authorization: str = Header(None)):
    try:
        return get_user(authorization)
    except:
        return None

# ── LLM ────────────────────────────────────────────────────
DB_SCHEMA = """
Tables MySQL :
races(id, nom, origine, poids_adulte_moyen_kg)
animaux(id, numero_tag, nom, race_id, sexe[M/F], date_naissance, poids_actuel, statut[actif/vendu/mort/quarantaine], mere_id, pere_id)
pesees(id, animal_id, poids_kg, date_pesee, agent)
sante(id, animal_id, type[vaccination/traitement/examen/chirurgie], description, date_acte, veterinaire, medicament, cout, prochain_rdv)
reproduction(id, mere_id, pere_id, date_saillie, date_velage_prevue, date_velage_reelle, statut[en_gestation/vele/avortement/echec])
alimentation(id, animal_id, type_aliment, quantite_kg, date_alimentation, cout_unitaire_kg)
ventes(id, animal_id, acheteur, telephone_acheteur, date_vente, poids_vente_kg, prix_fcfa)
alertes(id, animal_id, type, message, niveau[info/warning/critical], date_creation, traitee)
Fonctions : fn_age_en_mois(animal_id), fn_gmq(animal_id)
Procédures : sp_enregistrer_pesee(animal_id, poids_kg, date, agent), sp_declarer_vente(animal_id, acheteur, telephone, prix_fcfa, poids_vente_kg, date_vente)
"""

SYSTEM_PROMPT = f"""Tu es BoviBot, assistant IA d'un élevage bovin.
{DB_SCHEMA}
Réponds TOUJOURS en JSON :
Consultation : {{"type":"query","sql":"SELECT ...","explication":"..."}}
Action pesée : {{"type":"action","action":"sp_enregistrer_pesee","params":{{"animal_id":1,"poids_kg":320.5,"date":"2026-03-27","agent":"BoviBot"}},"confirmation":"Résumé"}}
Action vente : {{"type":"action","action":"sp_declarer_vente","params":{{"animal_id":1,"acheteur":"Nom","telephone":"","prix_fcfa":450000,"poids_vente_kg":310.0,"date_vente":"2026-03-27"}},"confirmation":"Résumé"}}
Info : {{"type":"info","explication":"..."}}
RÈGLES : SELECT uniquement (LIMIT 100), utiliser fn_age_en_mois() et fn_gmq() si pertinent, dates YYYY-MM-DD, confirmation obligatoire avant action.
"""

async def ask_llm(question: str, history: list = []) -> dict:
    messages = [{"role":"system","content":SYSTEM_PROMPT}] + history[-6:] + [{"role":"user","content":question}]
    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"{LLM_BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {LLM_API_KEY}"},
            json={"model": LLM_MODEL, "messages": messages, "temperature": 0},
            timeout=30,
        )
        r.raise_for_status()
        content = r.json()["choices"][0]["message"]["content"]
        match = re.search(r'\{.*\}', content, re.DOTALL)
        if match: return json.loads(match.group())
        raise ValueError("Réponse LLM invalide")

def call_proc(name: str, params: dict):
    conn = get_db(); cur = conn.cursor()
    try:
        if name == "sp_enregistrer_pesee":
            cur.callproc("sp_enregistrer_pesee", [params["animal_id"], params["poids_kg"], params["date"], params.get("agent","BoviBot")])
        elif name == "sp_declarer_vente":
            cur.callproc("sp_declarer_vente", [params["animal_id"], params["acheteur"], params.get("telephone",""), params["prix_fcfa"], params.get("poids_vente_kg",0), params["date_vente"]])
        conn.commit()
        return {"success": True}
    finally:
        cur.close(); conn.close()

# ══════════════════════════════════════════════════════════════
# AUTH
# ══════════════════════════════════════════════════════════════
class RegisterIn(BaseModel):
    username:     str
    mot_de_passe: str
    nom:          Optional[str] = None
    prenom:       Optional[str] = None
    nom_elevage:  Optional[str] = None
    telephone:    Optional[str] = None
    localite:     Optional[str] = None

class LoginIn(BaseModel):
    username:     str
    mot_de_passe: str

class ProfileIn(BaseModel):
    nom: Optional[str] = None; prenom: Optional[str] = None
    nom_elevage: Optional[str] = None; telephone: Optional[str] = None
    localite: Optional[str] = None

@app.post("/api/auth/register")
def register(body: RegisterIn):
    existing = qry("SELECT id FROM utilisateurs WHERE username=%s", (body.username,))
    if existing:
        raise HTTPException(400, "Nom d'utilisateur déjà utilisé")
    user_id = exe(
        """INSERT INTO utilisateurs
           (username, mot_de_passe, nom, prenom, nom_elevage, telephone, localite)
           VALUES (%s,%s,%s,%s,%s,%s,%s)""",
        (body.username, hash_pwd(body.mot_de_passe),
         body.nom, body.prenom, body.nom_elevage,
         body.telephone, body.localite)
    )
    token = make_token()
    exe("INSERT INTO tokens (token, user_id) VALUES (%s,%s)", (token, user_id))
    user = qry("SELECT id,username,nom,prenom,nom_elevage,telephone,localite,role FROM utilisateurs WHERE id=%s", (user_id,))[0]
    return {"token": token, "user": user}

@app.post("/api/auth/login")
def login(body: LoginIn):
    # ← le bug était ici : queryait body.email qui n'existait pas
    rows = qry(
        "SELECT * FROM utilisateurs WHERE username=%s AND mot_de_passe=%s",
        (body.username, hash_pwd(body.mot_de_passe))
    )
    if not rows:
        raise HTTPException(401, "Nom d'utilisateur ou mot de passe incorrect")
    user = rows[0]
    token = make_token()
    exe("INSERT INTO tokens (token, user_id) VALUES (%s,%s)", (token, user["id"]))
    return {"token": token, "user": {k:v for k,v in user.items() if k != "mot_de_passe"}}

@app.get("/api/auth/me")
def me(authorization: str = Header(None)):
    user = get_user(authorization)
    return {k: v for k, v in user.items() if k != "mot_de_passe"}

@app.put("/api/auth/profile")
def update_profile(body: ProfileIn, authorization: str = Header(None)):
    user = get_user(authorization)
    fields = {k: v for k, v in body.dict().items() if v is not None}
    if not fields: return {"message": "Rien à modifier"}
    set_clause = ", ".join(f"{k}=%s" for k in fields)
    exe(f"UPDATE utilisateurs SET {set_clause} WHERE id=%s", (*fields.values(), user["id"]))
    return {"message": "Profil mis à jour"}

@app.post("/api/auth/logout")
def logout(authorization: str = Header(None)):
    if authorization and authorization.startswith("Bearer "):
        token = authorization.split(" ")[1]
        exe("DELETE FROM tokens WHERE token=%s", (token,))
    return {"message": "Déconnecté"}

# ══════════════════════════════════════════════════════════════
# RACES
# ══════════════════════════════════════════════════════════════
@app.get("/api/races")
def get_races():
    return qry("SELECT * FROM races ORDER BY nom")

# ══════════════════════════════════════════════════════════════
# ANIMAUX — CRUD COMPLET
# ══════════════════════════════════════════════════════════════
class AnimalIn(BaseModel):
    numero_tag: str; sexe: str; date_naissance: str
    nom: Optional[str] = None; race_id: Optional[int] = None
    poids_actuel: Optional[float] = None; statut: Optional[str] = "actif"
    mere_id: Optional[int] = None; pere_id: Optional[int] = None
    notes: Optional[str] = None

@app.get("/api/animaux")
def get_animaux(authorization: str = Header(None)):
    user = get_user(authorization)
    return qry("""
        SELECT a.*, r.nom as race,
               fn_age_en_mois(a.id) as age_mois,
               fn_gmq(a.id) as gmq_kg_jour,
               m.numero_tag as mere_tag, p.numero_tag as pere_tag
        FROM animaux a
        LEFT JOIN races r ON a.race_id = r.id
        LEFT JOIN animaux m ON a.mere_id = m.id
        LEFT JOIN animaux p ON a.pere_id = p.id
        WHERE (a.user_id = %s OR a.user_id IS NULL)
        ORDER BY a.numero_tag
    """, (user["id"],))

@app.get("/api/animaux/{animal_id}")
def get_animal(animal_id: int, authorization: str = Header(None)):
    get_user(authorization)
    rows = qry("""
        SELECT a.*, r.nom as race,
               fn_age_en_mois(a.id) as age_mois, fn_gmq(a.id) as gmq_kg_jour,
               m.numero_tag as mere_tag, p.numero_tag as pere_tag
        FROM animaux a
        LEFT JOIN races r ON a.race_id = r.id
        LEFT JOIN animaux m ON a.mere_id = m.id
        LEFT JOIN animaux p ON a.pere_id = p.id
        WHERE a.id = %s
    """, (animal_id,))
    if not rows: raise HTTPException(404, "Animal non trouvé")
    animal = rows[0]
    animal["pesees"]  = qry("SELECT * FROM pesees WHERE animal_id=%s ORDER BY date_pesee DESC LIMIT 10", (animal_id,))
    animal["sante"]   = qry("SELECT * FROM sante WHERE animal_id=%s ORDER BY date_acte DESC", (animal_id,))
    animal["alimentation"] = qry("SELECT * FROM alimentation WHERE animal_id=%s ORDER BY date_alimentation DESC LIMIT 10", (animal_id,))
    return animal

@app.post("/api/animaux")
def create_animal(body: AnimalIn, authorization: str = Header(None)):
    user = get_user(authorization)
    existing = qry("SELECT id FROM animaux WHERE numero_tag=%s", (body.numero_tag,))
    if existing: raise HTTPException(400, f"Le tag {body.numero_tag} existe déjà")
    aid = exe("""
        INSERT INTO animaux (numero_tag, nom, race_id, sexe, date_naissance,
                             poids_actuel, statut, mere_id, pere_id, notes, user_id)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
    """, (body.numero_tag, body.nom, body.race_id, body.sexe, body.date_naissance,
          body.poids_actuel, body.statut or "actif", body.mere_id, body.pere_id, body.notes, user["id"]))
    return {"id": aid, "message": f"Animal {body.numero_tag} créé avec succès"}

@app.put("/api/animaux/{animal_id}")
def update_animal(animal_id: int, body: AnimalIn, authorization: str = Header(None)):
    get_user(authorization)
    exe("""
        UPDATE animaux SET numero_tag=%s, nom=%s, race_id=%s, sexe=%s,
               date_naissance=%s, poids_actuel=%s, statut=%s,
               mere_id=%s, pere_id=%s, notes=%s
        WHERE id=%s
    """, (body.numero_tag, body.nom, body.race_id, body.sexe, body.date_naissance,
          body.poids_actuel, body.statut, body.mere_id, body.pere_id, body.notes, animal_id))
    return {"message": "Animal mis à jour"}

@app.delete("/api/animaux/{animal_id}")
def delete_animal(animal_id: int, authorization: str = Header(None)):
    get_user(authorization)
    exe("DELETE FROM animaux WHERE id=%s", (animal_id,))
    return {"message": "Animal supprimé"}

# ══════════════════════════════════════════════════════════════
# SANTÉ
# ══════════════════════════════════════════════════════════════
class SanteIn(BaseModel):
    animal_id: int; type: str; description: str; date_acte: str
    veterinaire: Optional[str] = None; medicament: Optional[str] = None
    cout: Optional[float] = 0; prochain_rdv: Optional[str] = None

@app.get("/api/sante")
def get_sante(authorization: str = Header(None)):
    get_user(authorization)
    return qry("""
        SELECT s.*, a.numero_tag, a.nom as animal_nom
        FROM sante s JOIN animaux a ON s.animal_id = a.id
        ORDER BY s.date_acte DESC LIMIT 50
    """)

@app.post("/api/sante")
def create_sante(body: SanteIn, authorization: str = Header(None)):
    get_user(authorization)
    sid = exe("""
        INSERT INTO sante (animal_id, type, description, date_acte, veterinaire, medicament, cout, prochain_rdv)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
    """, (body.animal_id, body.type, body.description, body.date_acte,
          body.veterinaire, body.medicament, body.cout or 0,
          body.prochain_rdv if body.prochain_rdv else None))
    return {"id": sid, "message": "Acte sanitaire enregistré"}

# ══════════════════════════════════════════════════════════════
# REPRODUCTION
# ══════════════════════════════════════════════════════════════
class ReproIn(BaseModel):
    mere_id: int; pere_id: int; date_saillie: str
    date_velage_prevue: Optional[str] = None
    notes: Optional[str] = None

@app.get("/api/reproduction")
def get_repro(authorization: str = Header(None)):
    get_user(authorization)
    return qry("""
        SELECT r.*, a.numero_tag as mere_tag, p.numero_tag as pere_tag,
               DATEDIFF(r.date_velage_prevue, CURDATE()) as jours_restants
        FROM reproduction r
        JOIN animaux a ON r.mere_id = a.id
        JOIN animaux p ON r.pere_id = p.id
        ORDER BY r.date_velage_prevue ASC
    """)

@app.post("/api/reproduction")
def create_repro(body: ReproIn, authorization: str = Header(None)):
    get_user(authorization)
    rid = exe("""
        INSERT INTO reproduction (mere_id, pere_id, date_saillie, date_velage_prevue, notes)
        VALUES (%s,%s,%s,%s,%s)
    """, (body.mere_id, body.pere_id, body.date_saillie,
          body.date_velage_prevue, body.notes))
    return {"id": rid, "message": "Gestation enregistrée"}

# ══════════════════════════════════════════════════════════════
# ALIMENTATION
# ══════════════════════════════════════════════════════════════
class AlimIn(BaseModel):
    animal_id: int; type_aliment: str; quantite_kg: float
    date_alimentation: str; cout_unitaire_kg: Optional[float] = 0

@app.post("/api/alimentation")
def create_alim(body: AlimIn, authorization: str = Header(None)):
    get_user(authorization)
    aid = exe("""
        INSERT INTO alimentation (animal_id, type_aliment, quantite_kg, date_alimentation, cout_unitaire_kg)
        VALUES (%s,%s,%s,%s,%s)
    """, (body.animal_id, body.type_aliment, body.quantite_kg,
          body.date_alimentation, body.cout_unitaire_kg or 0))
    return {"id": aid, "message": "Ration enregistrée"}

# ══════════════════════════════════════════════════════════════
# PESÉES
# ══════════════════════════════════════════════════════════════
class PeseeIn(BaseModel):
    animal_id: int; poids_kg: float; date_pesee: str
    agent: Optional[str] = "BoviBot"

@app.post("/api/pesees")
def create_pesee(body: PeseeIn, authorization: str = Header(None)):
    get_user(authorization)
    conn = get_db(); cur = conn.cursor()
    try:
        cur.callproc("sp_enregistrer_pesee", [body.animal_id, body.poids_kg, body.date_pesee, body.agent])
        conn.commit()
        return {"message": "Pesée enregistrée via sp_enregistrer_pesee"}
    finally:
        cur.close(); conn.close()

# ══════════════════════════════════════════════════════════════
# VENTES
# ══════════════════════════════════════════════════════════════
class VenteIn(BaseModel):
    animal_id: int; acheteur: str; telephone: Optional[str] = ""
    prix_fcfa: float; poids_vente_kg: Optional[float] = 0; date_vente: str

@app.post("/api/ventes")
def create_vente(body: VenteIn, authorization: str = Header(None)):
    get_user(authorization)
    conn = get_db(); cur = conn.cursor()
    try:
        cur.callproc("sp_declarer_vente", [body.animal_id, body.acheteur, body.telephone or "", body.prix_fcfa, body.poids_vente_kg or 0, body.date_vente])
        conn.commit()
        return {"message": "Vente enregistrée via sp_declarer_vente"}
    finally:
        cur.close(); conn.close()

@app.get("/api/ventes")
def get_ventes(authorization: str = Header(None)):
    get_user(authorization)
    return qry("""
        SELECT v.*, a.numero_tag, a.nom as animal_nom
        FROM ventes v JOIN animaux a ON v.animal_id = a.id
        ORDER BY v.date_vente DESC
    """)

# ══════════════════════════════════════════════════════════════
# DASHBOARD + ALERTES
# ══════════════════════════════════════════════════════════════
@app.get("/api/dashboard")
def dashboard(authorization: str = Header(None)):
    opt_user(authorization)
    stats = {}
    for k, sql in {
        "total_actifs":      "SELECT COUNT(*) n FROM animaux WHERE statut='actif'",
        "femelles":          "SELECT COUNT(*) n FROM animaux WHERE statut='actif' AND sexe='F'",
        "males":             "SELECT COUNT(*) n FROM animaux WHERE statut='actif' AND sexe='M'",
        "en_gestation":      "SELECT COUNT(*) n FROM reproduction WHERE statut='en_gestation'",
        "alertes_actives":   "SELECT COUNT(*) n FROM alertes WHERE traitee=FALSE",
        "alertes_critiques": "SELECT COUNT(*) n FROM alertes WHERE traitee=FALSE AND niveau='critical'",
        "ventes_mois":       "SELECT COUNT(*) n FROM ventes WHERE MONTH(date_vente)=MONTH(NOW())",
        "ca_mois":           "SELECT COALESCE(SUM(prix_fcfa),0) n FROM ventes WHERE MONTH(date_vente)=MONTH(NOW())",
    }.items():
        stats[k] = qry(sql)[0]["n"]
    return stats

@app.get("/api/alertes")
def get_alertes(authorization: str = Header(None)):
    opt_user(authorization)
    return qry("""
        SELECT al.*, a.numero_tag as animal_tag, a.nom as animal_nom
        FROM alertes al LEFT JOIN animaux a ON al.animal_id = a.id
        WHERE al.traitee = FALSE
        ORDER BY FIELD(al.niveau,'critical','warning','info'), al.date_creation DESC
        LIMIT 50
    """)

@app.post("/api/alertes/{alert_id}/traiter")
def traiter_alerte(alert_id: int, authorization: str = Header(None)):
    opt_user(authorization)
    exe("UPDATE alertes SET traitee=TRUE WHERE id=%s", (alert_id,))
    return {"success": True}

@app.get("/api/reproduction/en-cours")
def get_gestations_encours(authorization: str = Header(None)):
    opt_user(authorization)
    return qry("""
        SELECT r.*, a.numero_tag as mere_tag, a.nom as mere_nom, p.numero_tag as pere_tag,
               DATEDIFF(r.date_velage_prevue, CURDATE()) as jours_restants
        FROM reproduction r
        JOIN animaux a ON r.mere_id = a.id JOIN animaux p ON r.pere_id = p.id
        WHERE r.statut = 'en_gestation' ORDER BY r.date_velage_prevue ASC
    """)

# ══════════════════════════════════════════════════════════════
# CHAT LLM
# ══════════════════════════════════════════════════════════════
class ChatMsg(BaseModel):
    question: str; history: list = []; confirm_action: bool = False; pending_action: dict = {}

@app.post("/api/chat")
async def chat(msg: ChatMsg, authorization: str = Header(None)):
    opt_user(authorization)
    try:
        if msg.confirm_action and msg.pending_action:
            call_proc(msg.pending_action["action"], msg.pending_action["params"])
            return {"type":"action_done","answer":"✅ Action effectuée avec succès !","data":[]}

        llm = await ask_llm(msg.question, msg.history)
        t = llm.get("type","info")

        if t == "query":
            sql = llm.get("sql","")
            if not sql: return {"type":"info","answer":llm.get("explication",""),"data":[]}
            data = qry(sql)
            return {"type":"query","answer":llm.get("explication",""),"data":data,"sql":sql,"count":len(data)}
        elif t == "action":
            return {"type":"action_pending","answer":llm.get("explication",""),
                    "confirmation":llm.get("confirmation","Confirmer ?"),
                    "pending_action":{"action":llm.get("action"),"params":llm.get("params",{})},
                    "data":[]}
        else:
            return {"type":"info","answer":llm.get("explication",""),"data":[]}
    except Exception as e:
        raise HTTPException(500, str(e))

@app.get("/health")
def health(): return {"status":"ok","version":"2.0.0"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8002, reload=True)