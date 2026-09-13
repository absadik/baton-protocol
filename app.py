import gradio as gr
import os
import time
import hashlib
import secrets
import sqlite3
import requests
import spaces
import shutil
from datetime import datetime
from apscheduler.schedulers.background import BackgroundScheduler

import google.generativeai as genai

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
FROM_EMAIL = os.environ.get("FROM_EMAIL", "onboarding@resend.dev")
DB_PATH = "baton.db"
UPLOAD_DIR = "baton_uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

def send_email(to_email, subject, html_body):
    if not RESEND_API_KEY:
        return False, "RESEND_API_KEY not set"
    try:
        r = requests.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {RESEND_API_KEY}", "Content-Type": "application/json"},
            json={"from": FROM_EMAIL, "to": [to_email], "subject": subject, "html": html_body},
            timeout=10,
        )
        return (True, "Sent") if r.status_code == 200 else (False, f"Error {r.status_code}: {r.text}")
    except Exception as e:
        return False, str(e)

def get_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        email TEXT PRIMARY KEY, password_hash TEXT, name TEXT,
        last_login TEXT, is_dead INTEGER DEFAULT 0)''')
    c.execute('''CREATE TABLE IF NOT EXISTS vault (
        id INTEGER PRIMARY KEY AUTOINCREMENT, owner_email TEXT,
        category TEXT, label TEXT, secret TEXT, created_at TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS testament (
        id INTEGER PRIMARY KEY AUTOINCREMENT, owner_email TEXT,
        title TEXT, content TEXT, updated_at TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS attachments (
        id INTEGER PRIMARY KEY AUTOINCREMENT, owner_email TEXT,
        testament_id INTEGER, file_path TEXT, file_type TEXT, original_name TEXT, uploaded_at TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS heirs (
        id INTEGER PRIMARY KEY AUTOINCREMENT, owner_email TEXT,
        heir_name TEXT, heir_relation TEXT, heir_contact TEXT,
        access_code TEXT, code_used INTEGER DEFAULT 0)''')
    c.execute('''CREATE TABLE IF NOT EXISTS family_message (
        id INTEGER PRIMARY KEY AUTOINCREMENT, owner_email TEXT,
        title TEXT, content TEXT, updated_at TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS personal_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT, owner_email TEXT,
        heir_name TEXT, content TEXT, updated_at TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS notifiers (
        id INTEGER PRIMARY KEY AUTOINCREMENT, owner_email TEXT,
        notifier_name TEXT, notifier_role TEXT, notifier_contact TEXT,
        personal_note TEXT, access_code TEXT, code_used INTEGER DEFAULT 0)''')
    c.execute('''CREATE TABLE IF NOT EXISTS warnings (
        id INTEGER PRIMARY KEY AUTOINCREMENT, owner_email TEXT,
        level TEXT, sent_at TEXT)''')
    conn.commit()
    conn.close()

init_db()

def hash_pw(pw):
    return hashlib.sha256(pw.encode()).hexdigest()

def create_user(email, password, name):
    conn = get_db()
    try:
        conn.execute("INSERT INTO users (email, password_hash, name, last_login) VALUES (?, ?, ?, ?)",
                     (email, hash_pw(password), name, datetime.now().isoformat()))
        conn.commit()
        return True, "Account created! Please log in below."
    except sqlite3.IntegrityError:
        return False, "Email already registered."
    finally:
        conn.close()

def verify_user(email, password):
    conn = get_db()
    u = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    conn.close()
    if not u or u["password_hash"] != hash_pw(password):
        return None
    return u

def touch_login(email):
    conn = get_db()
    conn.execute("UPDATE users SET last_login = ?, is_dead = 0 WHERE email = ?",
                 (datetime.now().isoformat(), email))
    conn.commit()
    conn.close()

def mark_dead(owner):
    conn = get_db()
    conn.execute("UPDATE users SET is_dead = 1 WHERE email = ?", (owner,))
    conn.commit()
    conn.close()

def check_in(email):
    conn = get_db()
    conn.execute("UPDATE users SET last_login = ?, is_dead = 0 WHERE email = ?",
                 (datetime.now().isoformat(), email))
    conn.commit()
    conn.close()

def get_status(email):
    conn = get_db()
    u = conn.execute("SELECT last_login, is_dead FROM users WHERE email = ?", (email,)).fetchone()
    conn.close()
    if not u:
        return "Not found"
    if u["is_dead"]:
        return "DECEASED (flagged)"
    days = (datetime.now() - datetime.fromisoformat(u["last_login"])).days
    if days <= 60: return f"SAFE - {days} days"
    if days <= 75: return f"WARNING 1 - {days} days"
    if days <= 85: return f"WARNING 2 - {days} days"
    if days < 90: return f"CRITICAL - {90 - days} days until inheritance"
    return f"TRIGGERED - {days} days"

def add_vault(owner, cat, label, secret):
    conn = get_db()
    conn.execute("INSERT INTO vault (owner_email, category, label, secret, created_at) VALUES (?, ?, ?, ?, ?)",
                 (owner, cat, label, secret, datetime.now().isoformat()))
    conn.commit()
    conn.close()

def get_vault(owner):
    conn = get_db()
    rows = conn.execute("SELECT category, label, secret FROM vault WHERE owner_email = ?", (owner,)).fetchall()
    conn.close()
    return [[r["category"], r["label"], r["secret"]] for r in rows]

def create_testament(owner, title, content):
    conn = get_db()
    c = conn.cursor()
    c.execute("INSERT INTO testament (owner_email, title, content, updated_at) VALUES (?, ?, ?, ?)",
              (owner, title, content, datetime.now().isoformat()))
    new_id = c.lastrowid
    conn.commit()
    conn.close()
    return new_id

def update_testament(test_id, owner, title, content):
    conn = get_db()
    conn.execute("UPDATE testament SET title = ?, content = ?, updated_at = ? WHERE id = ? AND owner_email = ?",
                 (title, content, datetime.now().isoformat(), test_id, owner))
    conn.commit()
    conn.close()

def get_all_testaments(owner):
    conn = get_db()
    rows = conn.execute("SELECT id, title, updated_at FROM testament WHERE owner_email = ? ORDER BY updated_at DESC",
                        (owner,)).fetchall()
    conn.close()
    return [[r["id"], r["title"], (r["updated_at"] or "")[:19]] for r in rows]

def get_testament_by_id(test_id, owner):
    conn = get_db()
    r = conn.execute("SELECT id, title, content FROM testament WHERE id = ? AND owner_email = ?",
                     (test_id, owner)).fetchone()
    conn.close()
    if not r:
        return None
    return {"id": r["id"], "title": r["title"], "content": r["content"]}

def delete_testament(test_id, owner):
    conn = get_db()
    conn.execute("DELETE FROM testament WHERE id = ? AND owner_email = ?", (test_id, owner))
    conn.commit()
    conn.close()

def get_testaments(owner):
    conn = get_db()
    rows = conn.execute("SELECT title, content FROM testament WHERE owner_email = ? ORDER BY updated_at DESC",
                        (owner,)).fetchall()
    conn.close()
    if not rows:
        return "No testament yet."
    parts = []
    for r in rows:
        parts.append(f"## {r['title']}\n\n{r['content']}")
    return "\n\n---\n\n".join(parts)

def save_attachment(owner, testament_id, file_path, file_type, original_name):
    if not file_path:
        return
    ext = os.path.splitext(original_name)[1] if original_name else os.path.splitext(file_path)[1]
    safe_name = f"{owner}_{testament_id}_{int(time.time())}{ext}"
    dest = os.path.join(UPLOAD_DIR, safe_name)
    try:
        shutil.copy(file_path, dest)
    except Exception as e:
        print(f"Copy error: {e}")
        return
    conn = get_db()
    conn.execute("INSERT INTO attachments (owner_email, testament_id, file_path, file_type, original_name, uploaded_at) VALUES (?, ?, ?, ?, ?, ?)",
                 (owner, testament_id, dest, file_type, original_name or safe_name, datetime.now().isoformat()))
    conn.commit()
    conn.close()

def get_attachments(owner, testament_id=None):
    conn = get_db()
    if testament_id:
        rows = conn.execute("SELECT id, file_path, file_type, original_name FROM attachments WHERE owner_email = ? AND testament_id = ?",
                            (owner, testament_id)).fetchall()
    else:
        rows = conn.execute("SELECT id, file_path, file_type, original_name FROM attachments WHERE owner_email = ?",
                            (owner,)).fetchall()
    conn.close()
    return [{"id": r["id"], "path": r["file_path"], "type": r["file_type"], "name": r["original_name"]} for r in rows]

def delete_attachment(att_id, owner):
    conn = get_db()
    row = conn.execute("SELECT file_path FROM attachments WHERE id = ? AND owner_email = ?", (att_id, owner)).fetchone()
    if row and os.path.exists(row["file_path"]):
        try:
            os.remove(row["file_path"])
        except Exception:
            pass
    conn.execute("DELETE FROM attachments WHERE id = ? AND owner_email = ?", (att_id, owner))
    conn.commit()
    conn.close()

def parse_attachment_choice(choice):
    if not choice:
        return None
    try:
        return int(choice.split(" - ")[0])
    except Exception:
        return None

def add_heir(owner, name, relation, contact):
    code = secrets.token_hex(4).upper()
    conn = get_db()
    conn.execute("INSERT INTO heirs (owner_email, heir_name, heir_relation, heir_contact, access_code) VALUES (?, ?, ?, ?, ?)",
                 (owner, name, relation, contact, code))
    conn.commit()
    conn.close()
    return code

def get_heirs(owner):
    conn = get_db()
    rows = conn.execute("SELECT heir_name, heir_relation, heir_contact, access_code, code_used FROM heirs WHERE owner_email = ?",
                        (owner,)).fetchall()
    conn.close()
    return [[r["heir_name"], r["heir_relation"] or "-", r["heir_contact"], r["access_code"],
             "Used" if r["code_used"] else "Active"] for r in rows]

def get_heir_names(owner):
    conn = get_db()
    rows = conn.execute("SELECT heir_name FROM heirs WHERE owner_email = ?", (owner,)).fetchall()
    conn.close()
    return [r["heir_name"] for r in rows]

def heir_login(code):
    conn = get_db()
    row = conn.execute("SELECT * FROM heirs WHERE access_code = ?", (code.upper(),)).fetchone()
    if not row:
        conn.close()
        return None, "Invalid code."
    if row["code_used"]:
        conn.close()
        return None, "Code already used."
    conn.execute("UPDATE heirs SET code_used = 1 WHERE id = ?", (row["id"],))
    conn.commit()
    conn.close()
    return row, "OK"

def save_family_message(owner, title, content):
    conn = get_db()
    existing = conn.execute("SELECT id FROM family_message WHERE owner_email = ? LIMIT 1", (owner,)).fetchone()
    if existing:
        conn.execute("UPDATE family_message SET title = ?, content = ?, updated_at = ? WHERE id = ?",
                     (title, content, datetime.now().isoformat(), existing["id"]))
    else:
        conn.execute("INSERT INTO family_message (owner_email, title, content, updated_at) VALUES (?, ?, ?, ?)",
                     (owner, title, content, datetime.now().isoformat()))
    conn.commit()
    conn.close()

def get_family_message(owner):
    conn = get_db()
    r = conn.execute("SELECT title, content FROM family_message WHERE owner_email = ? ORDER BY updated_at DESC LIMIT 1",
                     (owner,)).fetchone()
    conn.close()
    if not r:
        return "No family message yet."
    return f"## {r['title']}\n\n{r['content']}"

def save_personal_message(owner, heir_name, content):
    conn = get_db()
    conn.execute("INSERT INTO personal_messages (owner_email, heir_name, content, updated_at) VALUES (?, ?, ?, ?)",
                 (owner, heir_name, content, datetime.now().isoformat()))
    conn.commit()
    conn.close()

def get_all_personal_messages(owner):
    conn = get_db()
    rows = conn.execute("SELECT heir_name, content FROM personal_messages WHERE owner_email = ? ORDER BY heir_name",
                        (owner,)).fetchall()
    conn.close()
    return [[r["heir_name"], r["content"]] for r in rows]

def get_personal_message(owner, heir_name):
    conn = get_db()
    r = conn.execute("SELECT content FROM personal_messages WHERE owner_email = ? AND heir_name = ? ORDER BY updated_at DESC LIMIT 1",
                     (owner, heir_name)).fetchone()
    conn.close()
    return r["content"] if r else None

def add_notifier(owner, name, role, contact, note):
    code = secrets.token_hex(4).upper()
    conn = get_db()
    conn.execute("INSERT INTO notifiers (owner_email, notifier_name, notifier_role, notifier_contact, personal_note, access_code) VALUES (?, ?, ?, ?, ?, ?)",
                 (owner, name, role, contact, note, code))
    conn.commit()
    conn.close()
    return code

def get_notifiers(owner):
    conn = get_db()
    rows = conn.execute("SELECT notifier_name, notifier_role, notifier_contact, access_code, code_used FROM notifiers WHERE owner_email = ?",
                        (owner,)).fetchall()
    conn.close()
    return [[r["notifier_name"], r["notifier_role"], r["notifier_contact"], r["access_code"],
             "Notified" if r["code_used"] else "Active"] for r in rows]

def notifier_view(code):
    conn = get_db()
    row = conn.execute("SELECT * FROM notifiers WHERE access_code = ?", (code.upper(),)).fetchone()
    if not row:
        conn.close()
        return None, "Invalid code."
    conn.execute("UPDATE notifiers SET code_used = 1 WHERE id = ?", (row["id"],))
    conn.commit()
    owner_info = conn.execute("SELECT name FROM users WHERE email = ?", (row["owner_email"],)).fetchone()
    conn.close()
    return row, owner_info["name"] if owner_info else "Unknown"

def get_deceased_info(owner):
    conn = get_db()
    vault = conn.execute("SELECT category, label, secret FROM vault WHERE owner_email = ?", (owner,)).fetchall()
    fam = conn.execute("SELECT title, content FROM family_message WHERE owner_email = ? ORDER BY updated_at DESC LIMIT 1",
                       (owner,)).fetchall()
    atts = conn.execute("SELECT file_path, file_type, original_name FROM attachments WHERE owner_email = ?",
                        (owner,)).fetchall()
    conn.close()
    return vault, fam, [{"path": a["file_path"], "type": a["file_type"], "name": a["original_name"]} for a in atts]

def get_dashboard_stats(email):
    if not email:
        return {"vault": 0, "testaments": 0, "heirs": 0, "notifiers": 0, "attachments": 0}
    conn = get_db()
    v = conn.execute("SELECT COUNT(*) as c FROM vault WHERE owner_email = ?", (email,)).fetchone()["c"]
    t = conn.execute("SELECT COUNT(*) as c FROM testament WHERE owner_email = ?", (email,)).fetchone()["c"]
    h = conn.execute("SELECT COUNT(*) as c FROM heirs WHERE owner_email = ?", (email,)).fetchone()["c"]
    n = conn.execute("SELECT COUNT(*) as c FROM notifiers WHERE owner_email = ?", (email,)).fetchone()["c"]
    a = conn.execute("SELECT COUNT(*) as c FROM attachments WHERE owner_email = ?", (email,)).fetchone()["c"]
    conn.close()
    return {"vault": v, "testaments": t, "heirs": h, "notifiers": n, "attachments": a}

def build_dashboard_html(name, email, status, stats):
    return f"""
    <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 900px; margin: 0 auto; padding: 10px;">
        <div style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 25px; border-radius: 15px; margin-bottom: 20px;">
            <div style="display: flex; align-items: center; gap: 15px;">
                <div style="width: 60px; height: 60px; background: rgba(255,255,255,0.25); border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 28px; font-weight: bold;">
                    {name[0].upper() if name else "?"}
                </div>
                <div>
                    <h2 style="margin: 0; font-size: 22px;">👋 Welcome, {name}!</h2>
                    <p style="margin: 5px 0 0 0; opacity: 0.9; font-size: 14px;">{email}</p>
                </div>
            </div>
            <div style="background: rgba(255,255,255,0.2); padding: 10px 15px; border-radius: 10px; margin-top: 15px; font-size: 14px;">
                <b>Status:</b> {status}
            </div>
        </div>
        
        <h3 style="color: #333; margin: 20px 0 15px 0;">📊 Your Legacy at a Glance</h3>
        <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 12px; margin-bottom: 25px;">
            <div style="background: #fff; border: 1px solid #e5e7eb; border-radius: 12px; padding: 18px; text-align: center;">
                <div style="font-size: 32px;">🔐</div>
                <div style="font-size: 26px; font-weight: bold; color: #667eea; margin: 5px 0;">{stats['vault']}</div>
                <div style="font-size: 13px; color: #666;">Secrets in Vault</div>
            </div>
            <div style="background: #fff; border: 1px solid #e5e7eb; border-radius: 12px; padding: 18px; text-align: center;">
                <div style="font-size: 32px;">📜</div>
                <div style="font-size: 26px; font-weight: bold; color: #764ba2; margin: 5px 0;">{stats['testaments']}</div>
                <div style="font-size: 13px; color: #666;">Testaments</div>
            </div>
            <div style="background: #fff; border: 1px solid #e5e7eb; border-radius: 12px; padding: 18px; text-align: center;">
                <div style="font-size: 32px;">👥</div>
                <div style="font-size: 26px; font-weight: bold; color: #10b981; margin: 5px 0;">{stats['heirs']}</div>
                <div style="font-size: 13px; color: #666;">Heirs</div>
            </div>
            <div style="background: #fff; border: 1px solid #e5e7eb; border-radius: 12px; padding: 18px; text-align: center;">
                <div style="font-size: 32px;">🤝</div>
                <div style="font-size: 26px; font-weight: bold; color: #f59e0b; margin: 5px 0;">{stats['notifiers']}</div>
                <div style="font-size: 13px; color: #666;">Notifiers</div>
            </div>
            <div style="background: #fff; border: 1px solid #e5e7eb; border-radius: 12px; padding: 18px; text-align: center;">
                <div style="font-size: 32px;">📎</div>
                <div style="font-size: 26px; font-weight: bold; color: #ef4444; margin: 5px 0;">{stats['attachments']}</div>
                <div style="font-size: 13px; color: #666;">Attachments</div>
            </div>
        </div>
        
        <h3 style="color: #333; margin: 20px 0 15px 0;">⚡ Quick Actions</h3>
        <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 12px;">
            <div style="background: #eff6ff; border-left: 4px solid #3b82f6; padding: 15px; border-radius: 10px;">
                <div style="font-weight: bold; color: #1e40af;">❤️ I Am Alive</div>
                <div style="font-size: 13px; color: #666; margin-top: 5px;">Confirm you are alive</div>
            </div>
            <div style="background: #f0fdf4; border-left: 4px solid #22c55e; padding: 15px; border-radius: 10px;">
                <div style="font-weight: bold; color: #15803d;">🔐 Vault</div>
                <div style="font-size: 13px; color: #666; margin-top: 5px;">Store passwords</div>
            </div>
            <div style="background: #fef3c7; border-left: 4px solid #f59e0b; padding: 15px; border-radius: 10px;">
                <div style="font-weight: bold; color: #b45309;">📜 Testament</div>
                <div style="font-size: 13px; color: #666; margin-top: 5px;">Write multiple wills</div>
            </div>
            <div style="background: #fce7f3; border-left: 4px solid #ec4899; padding: 15px; border-radius: 10px;">
                <div style="font-weight: bold; color: #be185d;">💌 Messages</div>
                <div style="font-size: 13px; color: #666; margin-top: 5px;">Family & personal</div>
            </div>
            <div style="background: #ede9fe; border-left: 4px solid #8b5cf6; padding: 15px; border-radius: 10px;">
                <div style="font-weight: bold; color: #6d28d9;">👥 Heirs</div>
                <div style="font-size: 13px; color: #666; margin-top: 5px;">Add your loved ones</div>
            </div>
            <div style="background: #ecfeff; border-left: 4px solid #06b6d4; padding: 15px; border-radius: 10px;">
                <div style="font-weight: bold; color: #0e7490;">🤝 Notifiers</div>
                <div style="font-size: 13px; color: #666; margin-top: 5px;">Add witnesses</div>
            </div>
        </div>
        
        <div style="margin-top: 25px; padding: 15px; background: #f9fafb; border-radius: 10px; font-size: 13px; color: #666; text-align: center;">
            💡 Danna kowanne tab a saman shafin don sarrafa abubuwan da ke ciki.
        </div>
    </div>
    """

def send_warnings_and_notify():
    try:
        conn = get_db()
        users = conn.execute("SELECT email, name, last_login, is_dead FROM users").fetchall()
        conn.close()
        for u in users:
            email, name = u["email"], u["name"]
            if u["is_dead"]:
                continue
            days = (datetime.now() - datetime.fromisoformat(u["last_login"])).days
            level = None
            if 60 <= days < 75: level = "60_day"
            elif 75 <= days < 85: level = "75_day"
            elif 85 <= days < 90: level = "85_day"
            elif days >= 90: level = "90_day"
            if level:
                conn = get_db()
                already = conn.execute("SELECT 1 FROM warnings WHERE owner_email = ? AND level = ?",
                                       (email, level)).fetchone()
                if not already:
                    send_email(email, f"Baton Warning: {level}",
                               f"<h2>Hello {name},</h2><p>You have not checked in for {days} days.</p>")
                    conn2 = get_db()
                    conn2.execute("INSERT INTO warnings (owner_email, level, sent_at) VALUES (?, ?, ?)",
                                  (email, level, datetime.now().isoformat()))
                    conn2.commit()
                    conn2.close()
                conn.close()
    except Exception as e:
        print(f"Warning job error: {e}")

try:
    scheduler = BackgroundScheduler()
    scheduler.add_job(func=send_warnings_and_notify, trigger="interval", hours=24)
    scheduler.start()
except Exception as e:
    print(f"Scheduler error: {e}")

def check_liveness(user_email):
    conn = get_db()
    u = conn.execute("SELECT last_login, is_dead FROM users WHERE email = ?", (user_email,)).fetchone()
    conn.close()
    if not u:
        return {"status": "error", "message": "User not found"}
    if u["is_dead"]:
        return {"status": "triggered", "reason": "deceased"}
    days = (datetime.now() - datetime.fromisoformat(u["last_login"])).days
    if days > 90:
        return {"status": "triggered", "days": days}
    return {"status": "active", "days": days}

def transfer_agent(user_email, agent_name):
    return {"status": "success", "agent": agent_name, "heir": "designated"}

def archive_agent(user_email, agent_name):
    return {"status": "success", "seal_id": f"SEAL-{int(time.time())}"}

def decommission_agent(user_email, agent_name):
    return {"status": "success", "final_log": "keys revoked"}

SYSTEM_PROMPT = """You are Baton, an inheritance protocol for AI agents and digital assets.

Tools:
- check_liveness(user_email)
- transfer_agent(user_email, agent_name)
- archive_agent(user_email, agent_name)
- decommission_agent(user_email, agent_name)

Be concise."""

model = genai.GenerativeModel(
    "gemini-1.5-flash",
    system_instruction=SYSTEM_PROMPT,
    tools=[check_liveness, transfer_agent, archive_agent, decommission_agent],
)

def make_chat():
    return model.start_chat(enable_automatic_function_calling=True)

# ============ UI ============
with gr.Blocks(title="Baton - Agent Inheritance") as demo:
    gr.Markdown("# 🏛️ Baton")
    gr.Markdown("*Your agents don't drop when you do.*")

    email_state = gr.State("")
    name_state = gr.State("")
    chat_state = gr.State(None)

    welcome_banner = gr.Markdown(
        "## 👋 Welcome to Baton!\n\n"
        "**Baton** shine ka'idar gado don agents na AI masu zaman kansu.\n\n"
        "### 🚀 Yadda za ka fara\n"
        "1. **Yi rijista** da suna, imel, da kalmar sirri\n"
        "2. **Shiga** da bayananka\n"
        "3. Yi amfani da tabs don tsara gadonka\n\n"
        "---"
    )

    with gr.Tabs() as main_tabs:
        with gr.Tab("🔐 Login / Register", id="login") as login_tab:
            with gr.Row():
                with gr.Column():
                    gr.Markdown("### 🔐 Login")
                    li_email = gr.Textbox(label="Email")
                    li_pw = gr.Textbox(label="Password", type="password")
                    li_btn = gr.Button("Log in", variant="primary")
                    li_msg = gr.Textbox(label="Status", interactive=False)
                with gr.Column():
                    gr.Markdown("### 📝 Register")
                    rg_name = gr.Textbox(label="Name")
                    rg_email = gr.Textbox(label="Email")
                    rg_pw = gr.Textbox(label="Password", type="password")
                    rg_btn = gr.Button("Register", variant="primary")
                    rg_msg = gr.Textbox(label="Status", interactive=False)

        with gr.Tab("🏠 Dashboard", id="dashboard", visible=False) as dashboard_tab:
            dashboard_html = gr.HTML("<p>Loading...</p>")
            with gr.Row():
                quick_alive = gr.Button("❤️ I Am Alive", variant="primary")
                quick_logout = gr.Button("🚪 Log out", variant="stop")

        with gr.Tab("❤️ I Am Alive", id="alive", visible=False) as alive_tab:
            gr.Markdown("### Confirm you are alive")
            alive_status = gr.Markdown("Status: -")
            alive_btn = gr.Button("❤️ I Am Alive (Check In)", variant="primary", size="lg")
            alive_msg = gr.Textbox(label="Result", interactive=False)

        with gr.Tab("🔐 Vault", id="vault", visible=False) as vault_tab:
            gr.Markdown("### Store passwords and secrets")
            v_cat = gr.Dropdown(["Social Media", "Banking", "Email", "Crypto", "Other"], label="Category", value="Social Media")
            v_label = gr.Textbox(label="Label")
            v_secret = gr.Textbox(label="Secret", type="password")
            v_add = gr.Button("Add to Vault", variant="primary")
            v_list = gr.Dataframe(headers=["Category", "Label", "Secret"])
            v_msg = gr.Textbox(label="Status", interactive=False)

        with gr.Tab("📜 Testament", id="testament", visible=False) as t_tab:
            gr.Markdown("### 📜 My Testaments (like Google Docs)")
            t_list = gr.Dataframe(headers=["ID", "Title", "Last Updated"], label="📚 All My Testaments")
            with gr.Row():
                t_new_btn = gr.Button("➕ New", variant="secondary")
                t_load_btn = gr.Button("📥 Load", variant="primary")
                t_delete_btn = gr.Button("🗑️ Delete", variant="stop")
            t_selected_id = gr.Number(label="Selected ID", value=0, precision=0)
            gr.Markdown("### ✏️ Editor")
            t_title = gr.Textbox(label="Title")
            t_content = gr.Textbox(label="Content", lines=12)
            t_save = gr.Button("💾 Save", variant="primary")
            t_status = gr.Textbox(label="Status", interactive=False)
            gr.Markdown("---")
            gr.Markdown("### 📎 Attachments (Video/Audio)")
            attach_file = gr.File(label="Upload Video or Audio", file_count="single")
            attach_audio = gr.Audio(label="Or Record Audio", type="filepath", sources=["microphone", "upload"])
            attach_btn = gr.Button("📎 Attach", variant="secondary")
            attach_list = gr.Dataframe(headers=["ID", "File Name", "Type"])
            attach_selector = gr.Dropdown(label="Delete attachment", choices=[], interactive=True)
            attach_delete = gr.Button("🗑️ Delete Attachment", variant="stop")
            attach_msg = gr.Textbox(label="Status", interactive=False)

        with gr.Tab("👨‍👩‍👧 Family Message", id="family", visible=False) as fm_tab:
            gr.Markdown("### ONE message for ALL heirs")
            fm_title = gr.Textbox(label="Title")
            fm_content = gr.Textbox(label="Message", lines=6)
            fm_save = gr.Button("Save", variant="primary")
            fm_display = gr.Markdown("No family message yet.")

        with gr.Tab("💌 Personal Message", id="personal", visible=False) as pm_tab:
            gr.Markdown("### Different message for each heir")
            pm_heir = gr.Dropdown(label="Choose heir", choices=[], interactive=True)
            pm_content = gr.Textbox(label="Message", lines=5)
            pm_save = gr.Button("Save", variant="primary")
            pm_refresh = gr.Button("🔄 Refresh")
            pm_msg = gr.Textbox(label="Status", interactive=False)
            pm_all = gr.Dataframe(headers=["Heir", "Message"])

        with gr.Tab("👥 Heirs", id="heirs", visible=False) as h_tab:
            gr.Markdown("### Add heirs")
            h_name = gr.Textbox(label="Full Name")
            h_role = gr.Dropdown(
                ["Spouse (Mata/Miji)", "Son (Daa)", "Daughter (Ya)", "Mother (Uwa)",
                 "Father (Uba)", "Brother", "Sister", "Other"],
                label="Relationship", value="Daughter (Ya)")
            h_contact = gr.Textbox(label="Email or Phone")
            h_add = gr.Button("Add Heir", variant="primary")
            h_list = gr.Dataframe(headers=["Name", "Relationship", "Contact", "Code", "Status"])
            h_msg = gr.Textbox(label="Status", interactive=False)

        with gr.Tab("🤝 Notifiers", id="notifiers", visible=False) as n_tab:
            gr.Markdown("### People to NOTIFY (they see ONLY a note)")
            n_name = gr.Textbox(label="Their Name")
            n_role = gr.Dropdown(["Lawyer", "Imam", "Pastor", "Doctor", "Friend", "Other"], label="Role", value="Imam")
            n_contact = gr.Textbox(label="Contact")
            n_note = gr.Textbox(label="Personal note", lines=4)
            n_add = gr.Button("Add Notifier", variant="primary")
            n_list = gr.Dataframe(headers=["Name", "Role", "Contact", "Code", "Status"])
            n_msg = gr.Textbox(label="Status", interactive=False)

        with gr.Tab("💬 Chat", id="chat", visible=False) as chat_tab:
            chatbot = gr.Chatbot(label="Baton", height=350)
            chat_in = gr.Textbox(label="Message")
            chat_btn = gr.Button("Send", variant="primary")

        with gr.Tab("💀 Simulate Death", id="simulate", visible=False) as sim_tab:
            sim_btn = gr.Button("Simulate Death", variant="stop")
            sim_msg = gr.Textbox(label="Result", interactive=False)

        with gr.Tab("🔑 Heir Portal", id="heir", visible=False) as hc_tab:
            hc_code = gr.Textbox(label="Access Code")
            hc_btn = gr.Button("Unlock", variant="primary")
            hc_msg = gr.Textbox(label="Status", interactive=False)
            hc_personal = gr.Markdown("No personal message.")
            hc_family = gr.Markdown("No family message.")
            hc_test = gr.Markdown("No testament.")
            hc_vault = gr.Dataframe(headers=["Category", "Label", "Secret"])
            hc_attachments = gr.Dataframe(headers=["File Name", "Type"])

        with gr.Tab("🕊️ Notifier Portal", id="notifier", visible=False) as nc_tab:
            nc_code = gr.Textbox(label="Code")
            nc_btn = gr.Button("View", variant="primary")
            nc_msg = gr.Textbox(label="Status", interactive=False)
            nc_note = gr.Markdown("No notification.")

    # ===== HANDLERS =====
    def do_login(email, pw):
        u = verify_user(email, pw)
        vis_off = gr.update(visible=False)
        vis_on = gr.update(visible=True)
        banner_off = gr.update(visible=False)
        tab_dash = gr.update(selected="dashboard")
        if not u:
            return ("❌ Invalid credentials.", "", "", vis_on, vis_off, vis_off, vis_off, vis_off, vis_off,
                    vis_off, vis_off, vis_off, vis_off, vis_off, vis_off, gr.update(visible=True),
                    tab_dash, "<p>Login failed.</p>", [], 0, "", "", "", [])
        touch_login(email)
        status = get_status(email)
        stats = get_dashboard_stats(email)
        html = build_dashboard_html(u["name"], email, status, stats)
        tests = get_all_testaments(email)
        atts = get_attachments(email)
        att_choices = [f"{a['id']} - {a['name']} ({a['type']})" for a in atts]
        return (f"✅ Welcome, {u['name']}!", email, u["name"], vis_off, vis_on, vis_on, vis_on, vis_on,
                vis_on, vis_on, vis_on, vis_on, vis_on, vis_on, vis_on, banner_off, tab_dash, html,
                tests, 0, "", "", "", att_choices)

    def do_register(name, email, pw):
        if not name or not email or not pw:
            return "Fill all fields."
        if len(pw) < 6:
            return "Password must be 6+."
        ok, msg = create_user(email, pw, name)
        return msg

    def do_logout():
        vis_on = gr.update(visible=True)
        vis_off = gr.update(visible=False)
        banner_on = gr.update(visible=True)
        tab_login = gr.update(selected="login")
        return ("✅ Logged out.", "", "", vis_on, vis_off, vis_off, vis_off, vis_off, vis_off, vis_off,
                vis_off, vis_off, vis_off, vis_off, vis_off, banner_on, tab_login)

    def refresh_dashboard(name, email):
        if not email:
            return "<p>Please log in.</p>"
        status = get_status(email)
        stats = get_dashboard_stats(email)
        return build_dashboard_html(name, email, status, stats)

    def goto(tab_id):
        return gr.update(selected=tab_id)

    # ===== TESTAMENT =====
    def refresh_tests(email): return get_all_testaments(email)
    def new_testament(): return 0, "", "", "✏️ New testament."
    def load_testament(selected_id, email):
        if not email: return 0, "", "", "Please log in."
        if not selected_id or selected_id == 0: return 0, "", "", "Select first."
        t = get_testament_by_id(int(selected_id), email)
        if not t: return 0, "", "", "Not found."
        return t["id"], t["title"], t["content"], f"📥 Loaded: {t['title']}"
    def save_testament_ui(selected_id, email, title, content):
        if not email: return "Please log in.", refresh_tests(email), 0, "", ""
        if not title or not content: return "Required.", refresh_tests(email), selected_id, title, content
        if selected_id and int(selected_id) > 0:
            update_testament(int(selected_id), email, title, content)
            return f"✅ Updated: {title}", refresh_tests(email), int(selected_id), title, content
        new_id = create_testament(email, title, content)
        return f"✅ Created: {title}", refresh_tests(email), new_id, title, content
    def delete_testament_ui(selected_id, email):
        if not email: return "Please log in.", refresh_tests(email), 0, "", ""
        if not selected_id or selected_id == 0: return "Select first.", refresh_tests(email), 0, "", ""
        delete_testament(int(selected_id), email)
        return "🗑️ Deleted.", refresh_tests(email), 0, "", ""
    def select_row(evt: gr.SelectData, email):
        if evt.index is None: return 0, "", "", ""
        row_idx = evt.index[0]
        tests = get_all_testaments(email)
        if row_idx >= len(tests): return 0, "", "", ""
        tid = tests[row_idx][0]
        t = get_testament_by_id(int(tid), email)
        if not t: return 0, "", "", ""
        return t["id"], t["title"], t["content"], f"📥 Loaded: {t['title']}"

    # ===== ATTACHMENTS =====
    def do_attach(file_obj, audio_obj, selected_id, email):
        if not email: return "Please log in.", [], []
        if not selected_id or int(selected_id) == 0: return "Select a testament first.", [], []
        saved = 0
        if file_obj is not None:
            path = file_obj if isinstance(file_obj, str) else getattr(file_obj, "name", str(file_obj))
            name = os.path.basename(path)
            ext = os.path.splitext(name)[1].lower()
            ftype = "video" if ext in [".mp4", ".webm", ".mov", ".avi", ".mkv"] else "audio" if ext in [".mp3", ".wav", ".ogg", ".m4a"] else "file"
            save_attachment(email, int(selected_id), path, ftype, name)
            saved += 1
        if audio_obj is not None:
            path = audio_obj if isinstance(audio_obj, str) else getattr(audio_obj, "name", str(audio_obj))
            name = os.path.basename(path)
            save_attachment(email, int(selected_id), path, "audio", name)
            saved += 1
        if saved == 0: return "No file provided.", [], []
        atts = get_attachments(email, int(selected_id))
        rows = [[a["id"], a["name"], a["type"]] for a in atts]
        choices = [f"{a['id']} - {a['name']} ({a['type']})" for a in get_attachments(email)]
        return f"✅ Attached {saved} file(s).", rows, choices
    def refresh_attachments(email, selected_id):
        if not email or not selected_id or int(selected_id) == 0: return [], []
        atts = get_attachments(email, int(selected_id))
        rows = [[a["id"], a["name"], a["type"]] for a in atts]
        choices = [f"{a['id']} - {a['name']} ({a['type']})" for a in get_attachments(email)]
        return rows, choices
    def do_delete_attachment(choice, email):
        if not email: return "Please log in.", [], []
        att_id = parse_attachment_choice(choice)
        if not att_id: return "Select first.", [], []
        delete_attachment(att_id, email)
        atts = get_attachments(email)
        rows = [[a["id"], a["name"], a["type"]] for a in atts]
        choices = [f"{a['id']} - {a['name']} ({a['type']})" for a in atts]
        return "🗑️ Deleted.", rows, choices

    # ===== OTHER =====
    def do_add_vault(email, cat, label, secret):
        if not email: return [], "Please log in."
        if not label or not secret: return get_vault(email), "Required."
        add_vault(email, cat, label, secret)
        return get_vault(email), f"✅ Added {label}."
    def refresh_vault(email): return get_vault(email)
    def do_save_family(email, title, content):
        if not email: return "Please log in."
        if not title or not content: return "Required."
        save_family_message(email, title, content)
        return get_family_message(email)
    def refresh_family(email): return get_family_message(email)
    def refresh_heir_dropdown(email): return gr.update(choices=get_heir_names(email))
    def refresh_pm_table(email): return get_all_personal_messages(email)
    def do_save_personal(email, heir, content):
        if not email: return "Please log in.", []
        if not heir or not content: return "Required.", refresh_pm_table(email)
        save_personal_message(email, heir, content)
        return f"✅ Saved for {heir}.", refresh_pm_table(email)
    def do_add_heir(email, name, role, contact):
        if not email: return [], "Please log in."
        if not name or not contact: return get_heirs(email), "Required."
        code = add_heir(email, name, role, contact)
        return get_heirs(email), f"✅ Code: {code}"
    def refresh_heirs(email): return get_heirs(email)
    def do_add_notifier(email, name, role, contact, note):
        if not email: return [], "Please log in."
        if not name or not contact: return get_notifiers(email), "Required."
        code = add_notifier(email, name, role, contact, note)
        return get_notifiers(email), f"✅ Code: {code}"
    def refresh_notifiers(email): return get_notifiers(email)
    def do_notifier_view(code):
        row, owner_name = notifier_view(code)
        if not row: return "Invalid code.", "No notification."
        note = f"## Notification from {owner_name}\n\nTo {row['notifier_name']} ({row['notifier_role']}):\n\n{row['personal_note']}"
        return "✅ Received.", note
    @spaces.GPU
    def do_chat(msg, hist, email, sess):
        if not email:
            hist = hist + [{"role": "user", "content": msg}, {"role": "assistant", "content": "Please log in."}]
            return hist, ""
        if sess is None: sess = make_chat()
        try:
            r = sess.send_message(msg)
            hist = hist + [{"role": "user", "content": msg}, {"role": "assistant", "content": r.text}]
        except Exception as e:
            hist = hist + [{"role": "user", "content": msg}, {"role": "assistant", "content": f"Error: {e}"}]
        return hist, ""
    def do_simulate_death(email):
        if not email: return "Please log in."
        mark_dead(email)
        heirs = get_heirs(email)
        notifiers = get_notifiers(email)
        lines = ["💀 Flagged.\n"]
        if heirs:
            lines.append("HEIRS:")
            for h in heirs:
                lines.append(f"  {h[0]} ({h[1]}) - Code: {h[3]}")
                if h[2] and "@" in h[2]:
                    send_email(h[2], "Baton: Inheritance", f"<h2>Hello {h[0]},</h2><p>Code: <b>{h[3]}</b></p>")
        if notifiers:
            lines.append("\nWITNESSES:")
            for n in notifiers:
                lines.append(f"  {n[0]} ({n[1]}) - Code: {n[3]}")
        return "\n".join(lines)
    def do_checkin(email):
        if not email: return "Please log in."
        check_in(email)
        return get_status(email)
    def refresh_status(email):
        if not email: return "Please log in."
        return get_status(email)
    def do_heir_unlock(code):
        row, msg = heir_login(code)
        if not row:
            return msg, "No personal message.", "No family message.", "No testament.", [], []
        owner = row["owner_email"]
        heir_name = row["heir_name"]
        vault, fam, atts = get_deceased_info(owner)
        vault_rows = [[v["category"], v["label"], v["secret"]] for v in vault]
        att_rows = [[a["name"], a["type"]] for a in atts]
        personal = get_personal_message(owner, heir_name)
        personal_md = f"## Personal Message for {heir_name}\n\n{personal}" if personal else "No personal message."
        family_md = f"## Family Message\n\n{fam[0]['content']}" if fam else "No family message."
        test_md = get_testaments(owner)
        return f"✅ Access granted. Welcome, {heir_name}.", personal_md, family_md, test_md, vault_rows, att_rows

    # ===== CONNECT =====
    li_btn.click(
        do_login,
        [li_email, li_pw],
        [li_msg, email_state, name_state, login_tab, dashboard_tab, alive_tab, vault_tab, t_tab, fm_tab,
         pm_tab, h_tab, n_tab, chat_tab, sim_tab, hc_tab, nc_tab, welcome_banner, main_tabs, dashboard_html,
         t_list, t_selected_id, t_title, t_content, attach_selector]
    )
    rg_btn.click(do_register, [rg_name, rg_email, rg_pw], rg_msg)
    quick_logout.click(
        do_logout,
        [],
        [li_msg, email_state, name_state, login_tab, dashboard_tab, alive_tab, vault_tab, t_tab, fm_tab,
         pm_tab, h_tab, n_tab, chat_tab, sim_tab, hc_tab, nc_tab, welcome_banner, main_tabs]
    )
    quick_alive.click(lambda: goto("alive"), [], [main_tabs])

    # Testament events
    t_new_btn.click(new_testament, [], [t_selected_id, t_title, t_content, t_status])
    t_load_btn.click(load_testament, [t_selected_id, email_state], [t_selected_id, t_title, t_content, t_status])
    t_delete_btn.click(delete_testament_ui, [t_selected_id, email_state], [t_status, t_list, t_selected_id, t_title, t_content])
    t_save.click(save_testament_ui, [t_selected_id, email_state, t_title, t_content], [t_status, t_list, t_selected_id, t_title, t_content])
    t_list.select(select_row, [email_state], [t_selected_id, t_title, t_content, t_status])
    email_state.change(refresh_tests, [email_state], [t_list])
    attach_btn.click(do_attach, [attach_file, attach_audio, t_selected_id, email_state], [attach_msg, attach_list, attach_selector])
    attach_delete.click(do_delete_attachment, [attach_selector, email_state], [attach_msg, attach_list, attach_selector])
    t_selected_id.change(refresh_attachments, [email_state, t_selected_id], [attach_list, attach_selector])

    # Other events
    v_add.click(do_add_vault, [email_state, v_cat, v_label, v_secret], [v_list, v_msg])
    email_state.change(refresh_vault, [email_state], [v_list])
    fm_save.click(do_save_family, [email_state, fm_title, fm_content], [fm_display])
    email_state.change(refresh_family, [email_state], [fm_display])
    pm_refresh.click(refresh_heir_dropdown, [email_state], [pm_heir])
    pm_save.click(do_save_personal, [email_state, pm_heir, pm_content], [pm_msg, pm_all])
    email_state.change(refresh_pm_table, [email_state], [pm_all])
    h_add.click(do_add_heir, [email_state, h_name, h_role, h_contact], [h_list, h_msg])
    email_state.change(refresh_heirs, [email_state], [h_list])
    n_add.click(do_add_notifier, [email_state, n_name, n_role, n_contact, n_note], [n_list, n_msg])
    email_state.change(refresh_notifiers, [email_state], [n_list])
    chat_btn.click(do_chat, [chat_in, chatbot, email_state, chat_state], [chatbot, chat_in])
    chat_in.submit(do_chat, [chat_in, chatbot, email_state, chat_state], [chatbot, chat_in])
    sim_btn.click(do_simulate_death, [email_state], sim_msg)
    hc_btn.click(do_heir_unlock, [hc_code], [hc_msg, hc_personal, hc_family, hc_test, hc_vault, hc_attachments])
    nc_btn.click(do_notifier_view, [nc_code], [nc_msg, nc_note])
    email_state.change(refresh_status, [email_state], [alive_status])
    email_state.change(refresh_dashboard, [name_state, email_state], [dashboard_html])
    alive_btn.click(do_checkin, [email_state], [alive_msg])

demo.launch()
