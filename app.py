import gradio as gr
import os
import time
import hashlib
import secrets
import sqlite3
import requests
import spaces
from datetime import datetime
from apscheduler.schedulers.background import BackgroundScheduler

import google.generativeai as genai

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
FROM_EMAIL = os.environ.get("FROM_EMAIL", "onboarding@resend.dev")
DB_PATH = "baton.db"

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

# ============ EMAIL ============
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

# ============ DATABASE ============
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

# ============ USERS ============
def create_user(email, password, name):
    conn = get_db()
    try:
        conn.execute("INSERT INTO users (email, password_hash, name, last_login) VALUES (?, ?, ?, ?)",
                     (email, hash_pw(password), name, datetime.now().isoformat()))
        conn.commit()
        return True, "Account created!"
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
    if days <= 60: return f"SAFE - {days} days since check-in"
    if days <= 75: return f"WARNING 1 - {days} days. Please check in."
    if days <= 85: return f"WARNING 2 - {days} days. Check in soon!"
    if days < 90: return f"CRITICAL - {90 - days} days until inheritance!"
    return f"TRIGGERED - {days} days"

# ============ VAULT ============
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

# ============ TESTAMENT ============
def save_testament(owner, title, content):
    conn = get_db()
    existing = conn.execute("SELECT id FROM testament WHERE owner_email = ? LIMIT 1", (owner,)).fetchone()
    if existing:
        conn.execute("UPDATE testament SET title = ?, content = ?, updated_at = ? WHERE id = ?",
                     (title, content, datetime.now().isoformat(), existing["id"]))
    else:
        conn.execute("INSERT INTO testament (owner_email, title, content, updated_at) VALUES (?, ?, ?, ?)",
                     (owner, title, content, datetime.now().isoformat()))
    conn.commit()
    conn.close()
    return "Testament saved!"

def get_testaments(owner):
    conn = get_db()
    r = conn.execute("SELECT title, content, updated_at FROM testament WHERE owner_email = ? ORDER BY updated_at DESC LIMIT 1",
                     (owner,)).fetchone()
    conn.close()
    if not r:
        return "No testament yet."
    return f"## {r['title']}\n\n{r['content']}\n\n---\nLast updated: {r['updated_at'][:19]}"

def load_testament_for_edit(owner):
    if not owner:
        return "", "", "Please log in first."
    conn = get_db()
    r = conn.execute("SELECT title, content FROM testament WHERE owner_email = ? ORDER BY updated_at DESC LIMIT 1",
                     (owner,)).fetchone()
    conn.close()
    if not r:
        return "", "", "No existing testament. Write a new one below and click Save."
    return r["title"], r["content"], "Loaded! You can now edit and click Save."

# ============ HEIRS ============
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

# ============ FAMILY / PERSONAL MESSAGES ============
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

# ============ NOTIFIERS ============
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
    test = conn.execute("SELECT title, content FROM testament WHERE owner_email = ? ORDER BY updated_at DESC LIMIT 1",
                        (owner,)).fetchall()
    fam = conn.execute("SELECT title, content FROM family_message WHERE owner_email = ? ORDER BY updated_at DESC LIMIT 1",
                       (owner,)).fetchall()
    conn.close()
    return vault, test, fam

# ============ AUTO WARNINGS ============
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
                               f"<h2>Hello {name},</h2><p>You have not checked in for {days} days. Please log in and tap I Am Alive.</p>")
                    conn2 = get_db()
                    conn2.execute("INSERT INTO warnings (owner_email, level, sent_at) VALUES (?, ?, ?)",
                                  (email, level, datetime.now().isoformat()))
                    conn2.commit()
                    conn2.close()
                    if level == "90_day":
                        for h in get_heirs(email):
                            if h[2] and "@" in h[2]:
                                send_email(h[2], f"Baton: Inheritance for {name}",
                                           f"<h2>Hello {h[0]},</h2><p>Access code: <b>{h[3]}</b></p>")
                        for n in get_notifiers(email):
                            if n[2] and "@" in n[2]:
                                send_email(n[2], f"Baton: Notification for {name}",
                                           f"<h2>Hello {n[0]},</h2><p>Code: <b>{n[3]}</b></p>")
                conn.close()
    except Exception as e:
        print(f"Warning job error: {e}")

try:
    scheduler = BackgroundScheduler()
    scheduler.add_job(func=send_warnings_and_notify, trigger="interval", hours=24)
    scheduler.start()
except Exception as e:
    print(f"Scheduler error: {e}")

# ============ AGENT TOOLS ============
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
    chat_state = gr.State(None)

    with gr.Tab("🔐 Login / Register") as login_tab:
        with gr.Row():
            with gr.Column():
                gr.Markdown("### Login")
                li_email = gr.Textbox(label="Email")
                li_pw = gr.Textbox(label="Password", type="password")
                li_btn = gr.Button("Log in", variant="primary")
                li_msg = gr.Textbox(label="Status", interactive=False)
            with gr.Column():
                gr.Markdown("### Register")
                rg_name = gr.Textbox(label="Name")
                rg_email = gr.Textbox(label="Email")
                rg_pw = gr.Textbox(label="Password", type="password")
                rg_btn = gr.Button("Register", variant="primary")
                rg_msg = gr.Textbox(label="Status", interactive=False)

    with gr.Tab("🏠 Dashboard", visible=False) as dashboard_tab:
        dash_welcome = gr.Markdown("Welcome!")

    with gr.Tab("❤️ I Am Alive", visible=False) as alive_tab:
        gr.Markdown("### Confirm you are alive")
        alive_status = gr.Markdown("Status: -")
        alive_btn = gr.Button("I Am Alive (Check In)", variant="primary", size="lg")
        alive_msg = gr.Textbox(label="Result", interactive=False)

    with gr.Tab("🔐 Vault (Secrets)", visible=False) as vault_tab:
        gr.Markdown("### Store passwords and secrets")
        v_cat = gr.Dropdown(["Social Media", "Banking", "Email", "Crypto", "Other"], label="Category", value="Social Media")
        v_label = gr.Textbox(label="Label")
        v_secret = gr.Textbox(label="Secret", type="password")
        v_add = gr.Button("Add to Vault", variant="primary")
        v_list = gr.Dataframe(headers=["Category", "Label", "Secret"])
        v_msg = gr.Textbox(label="Status", interactive=False)

    with gr.Tab("📜 Testament", visible=False) as t_tab:
        gr.Markdown("### Your official will (wasiyya)")
        gr.Markdown("Tip: Idan ka riga ka rubuta wasiyya, danna Load my testament don ka gyara ta. Idan ba ka rubuta ba, rubuta sabo sannan ka danna Save.")
        t_load = gr.Button("Load my testament", variant="secondary")
        t_title = gr.Textbox(label="Title (Take)")
        t_content = gr.Textbox(label="Content (Abin da ka rubuta)", lines=8)
        t_save = gr.Button("Save Testament (Ajiye)", variant="primary")
        t_status = gr.Textbox(label="Status", interactive=False)
        gr.Markdown("---")
        gr.Markdown("### Current Testament (Abin da ke ajiye yanzu)")
        t_display = gr.Markdown("No testament yet.")

    with gr.Tab("👨‍👩‍👧 Family Message", visible=False) as fm_tab:
        gr.Markdown("### ONE message for ALL heirs")
        fm_title = gr.Textbox(label="Title")
        fm_content = gr.Textbox(label="Message", lines=6)
        fm_save = gr.Button("Save Family Message", variant="primary")
        fm_display = gr.Markdown("No family message yet.")

    with gr.Tab("💌 Personal Message", visible=False) as pm_tab:
        gr.Markdown("### Different message for each heir")
        pm_heir = gr.Dropdown(label="Choose heir", choices=[], interactive=True)
        pm_content = gr.Textbox(label="Message", lines=5)
        pm_save = gr.Button("Save Personal Message", variant="primary")
        pm_refresh = gr.Button("Refresh heir list")
        pm_msg = gr.Textbox(label="Status", interactive=False)
        pm_all = gr.Dataframe(headers=["Heir", "Message"])

    with gr.Tab("👥 Heirs", visible=False) as h_tab:
        gr.Markdown("### Add heirs (children, spouse, mother, father)")
        h_name = gr.Textbox(label="Full Name")
        h_role = gr.Dropdown(
            ["Spouse (Mata/Miji)", "Son (Daa)", "Daughter (Ya)", "Mother (Uwa)",
             "Father (Uba)", "Brother", "Sister", "Other"],
            label="Relationship", value="Daughter (Ya)")
        h_contact = gr.Textbox(label="Email or Phone")
        h_add = gr.Button("Add Heir", variant="primary")
        h_list = gr.Dataframe(headers=["Name", "Relationship", "Contact", "Code", "Status"])
        h_msg = gr.Textbox(label="Status", interactive=False)

    with gr.Tab("🤝 Notifiers", visible=False) as n_tab:
        gr.Markdown("### People to NOTIFY of your death (they see ONLY a note, no secrets)")
        n_name = gr.Textbox(label="Their Name")
        n_role = gr.Dropdown(["Lawyer", "Imam", "Pastor", "Doctor", "Friend", "Other"], label="Role", value="Imam")
        n_contact = gr.Textbox(label="Contact (email/phone)")
        n_note = gr.Textbox(label="Personal note to them (they see ONLY this)", lines=4)
        n_add = gr.Button("Add Notifier", variant="primary")
        n_list = gr.Dataframe(headers=["Name", "Role", "Contact", "Code", "Status"])
        n_msg = gr.Textbox(label="Status", interactive=False)

    with gr.Tab("💬 Chat with Baton", visible=False) as chat_tab:
        chatbot = gr.Chatbot(label="Baton", height=350)
        chat_in = gr.Textbox(label="Message")
        chat_btn = gr.Button("Send", variant="primary")

    with gr.Tab("💀 Simulate Death", visible=False) as sim_tab:
        gr.Markdown("### Flag your account as deceased (demo control)")
        sim_btn = gr.Button("Simulate Death", variant="stop")
        sim_msg = gr.Textbox(label="Result", interactive=False)

    with gr.Tab("🔑 Heir Access Portal", visible=False) as hc_tab:
        gr.Markdown("### Enter your access code to unlock inheritance")
        hc_code = gr.Textbox(label="Access Code")
        hc_btn = gr.Button("Unlock", variant="primary")
        hc_msg = gr.Textbox(label="Status", interactive=False)
        hc_personal = gr.Markdown("No personal message.")
        hc_family = gr.Markdown("No family message.")
        hc_test = gr.Markdown("No testament.")
        hc_vault = gr.Dataframe(headers=["Category", "Label", "Secret"])

    with gr.Tab("🕊️ Notifier Portal", visible=False) as nc_tab:
        gr.Markdown("### Witnesses enter their code here (see ONLY their note)")
        nc_code = gr.Textbox(label="Notification Code")
        nc_btn = gr.Button("View Notification", variant="primary")
        nc_msg = gr.Textbox(label="Status", interactive=False)
        nc_note = gr.Markdown("No notification.")

    # ===== HANDLERS =====
    def do_login(email, pw):
        u = verify_user(email, pw)
        vis_off = gr.update(visible=False)
        if not u:
            return ("Invalid credentials.", "", vis_off, vis_off, vis_off, vis_off, vis_off,
                    vis_off, vis_off, vis_off, vis_off, vis_off, vis_off, vis_off, "Please try again.")
        touch_login(email)
        status = get_status(email)
        welcome = f"# Welcome, {u['name']}!\n\nYour Baton account is active.\n\nStatus: {status}\n\nUse the tabs above to manage your digital legacy."
        vis_on = gr.update(visible=True)
        return (f"Welcome, {u['name']}!", email, vis_on, vis_on, vis_on, vis_on, vis_on,
                vis_on, vis_on, vis_on, vis_on, vis_on, vis_on, vis_on, welcome)

    def do_register(name, email, pw):
        if not name or not email or not pw:
            return "Fill all fields."
        if len(pw) < 6:
            return "Password must be 6+."
        ok, msg = create_user(email, pw, name)
        return msg

    def do_add_vault(email, cat, label, secret):
        if not email: return [], "Please log in."
        if not label or not secret: return get_vault(email), "Required."
        add_vault(email, cat, label, secret)
        return get_vault(email), f"Added {label}."

    def refresh_vault(email): return get_vault(email)

    def do_save_testament(email, title, content):
        if not email: return "Please log in.", "Please log in."
        if not title or not content: return "Title and content required.", get_testaments(email)
        msg = save_testament(email, title, content)
        return msg, get_testaments(email)

    def refresh_testament(email): return get_testaments(email)

    def do_load_testament(email):
        title, content, msg = load_testament_for_edit(email)
        return title, content, msg

    def do_save_family(email, title, content):
        if not email: return "Please log in."
        if not title or not content: return "Required."
        save_family_message(email, title, content)
        return get_family_message(email)

    def refresh_family(email): return get_family_message(email)

    def refresh_heir_dropdown(email):
        return gr.update(choices=get_heir_names(email))

    def refresh_pm_table(email): return get_all_personal_messages(email)

    def do_save_personal(email, heir, content):
        if not email: return "Please log in.", []
        if not heir or not content: return "Choose heir and write.", refresh_pm_table(email)
        save_personal_message(email, heir, content)
        return f"Saved for {heir}.", refresh_pm_table(email)

    def do_add_heir(email, name, role, contact):
        if not email: return [], "Please log in."
        if not name or not contact: return get_heirs(email), "Required."
        code = add_heir(email, name, role, contact)
        return get_heirs(email), f"Heir added. Code: {code}"

    def refresh_heirs(email): return get_heirs(email)

    def do_add_notifier(email, name, role, contact, note):
        if not email: return [], "Please log in."
        if not name or not contact: return get_notifiers(email), "Required."
        code = add_notifier(email, name, role, contact, note)
        return get_notifiers(email), f"Notifier added. Code: {code}"

    def refresh_notifiers(email): return get_notifiers(email)

    def do_notifier_view(code):
        row, owner_name = notifier_view(code)
        if not row:
            return "Invalid code.", "No notification."
        note = f"## Notification from {owner_name}\n\nTo {row['notifier_name']} ({row['notifier_role']}):\n\n{row['personal_note']}\n\n---\n{owner_name} has passed away. This is their message to you. You do not have access to their private vault."
        return "Notification received.", note

    @spaces.GPU
    def do_chat(msg, hist, email, sess):
        if not email:
            hist = hist + [{"role": "user", "content": msg}, {"role": "assistant", "content": "Please log in first."}]
            return hist, ""
        if sess is None:
            sess = make_chat()
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
        lines = ["Account flagged as deceased.\n"]
        if heirs:
            lines.append("HEIRS (full access):")
            for h in heirs:
                lines.append(f"  {h[0]} ({h[1]}) - Code: {h[3]}")
                if h[2] and "@" in h[2]:
                    send_email(h[2], f"Baton: Inheritance for {email}",
                               f"<h2>Hello {h[0]},</h2><p>Access code: <b>{h[3]}</b></p>")
        if notifiers:
            lines.append("\nWITNESSES (view-only note):")
            for n in notifiers:
                lines.append(f"  {n[0]} ({n[1]}) - Code: {n[3]}")
                if n[2] and "@" in n[2]:
                    send_email(n[2], f"Baton: Notification for {email}",
                               f"<h2>Hello {n[0]},</h2><p>Code: <b>{n[3]}</b></p>")
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
            return msg, "No personal message.", "No family message.", "No testament.", []
        owner = row["owner_email"]
        heir_name = row["heir_name"]
        vault, test, fam = get_deceased_info(owner)
        vault_rows = [[v["category"], v["label"], v["secret"]] for v in vault]
        personal = get_personal_message(owner, heir_name)
        personal_md = f"## Personal Message for {heir_name}\n\n{personal}" if personal else "No personal message."
        family_md = f"## Family Message\n\n{fam[0]['content']}" if fam else "No family message."
        test_md = f"## Testament\n\n{test[0]['content']}" if test else "No testament."
        return f"Access granted. Welcome, {heir_name}.", personal_md, family_md, test_md, vault_rows

    # ===== CONNECT EVENTS =====
    li_btn.click(
        do_login,
        [li_email, li_pw],
        [li_msg, email_state, dashboard_tab, alive_tab, vault_tab, t_tab, fm_tab, pm_tab, h_tab, n_tab, chat_tab, sim_tab, hc_tab, nc_tab, dash_welcome]
    )
    rg_btn.click(do_register, [rg_name, rg_email, rg_pw], rg_msg)

    v_add.click(do_add_vault, [email_state, v_cat, v_label, v_secret], [v_list, v_msg])
    email_state.change(refresh_vault, [email_state], [v_list])

    t_load.click(do_load_testament, [email_state], [t_title, t_content, t_status])
    t_save.click(do_save_testament, [email_state, t_title, t_content], [t_status, t_display])
    email_state.change(refresh_testament, [email_state], [t_display])

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
    hc_btn.click(do_heir_unlock, [hc_code], [hc_msg, hc_personal, hc_family, hc_test, hc_vault])
    nc_btn.click(do_notifier_view, [nc_code], [nc_msg, nc_note])
    email_state.change(refresh_status, [email_state], [alive_status])
    alive_btn.click(do_checkin, [email_state], [alive_msg])

demo.launch()
