import gradio as gr
import google.generativeai as genai
import os
import time
import hashlib
from datetime import datetime, timedelta

genai.configure(api_key=os.environ.get("GEMINI_API_KEY", "YOUR_KEY_HERE"))

USERS = {
    "david@example.com": {
        "password_hash": hashlib.sha256("password123".encode()).hexdigest(),
        "name": "David",
        "last_login": datetime.now() - timedelta(days=91),
        "heirs": {
            "financial_agent": "daughter@example.com",
            "medical_agent": "spouse@example.com",
            "email_agent": "archive",
        },
        "assets": {
            "financial_agent": "$47,000",
            "medical_agent": "Medical Records",
            "email_agent": "12,000 Emails",
        },
    }
}

def check_liveness(user_email):
    user = USERS.get(user_email)
    if not user:
        return {"status": "error", "message": "User not found"}
    days = (datetime.now() - user["last_login"]).days
    if days > 90:
        return {"status": "triggered", "days_inactive": days}
    return {"status": "active", "days_inactive": days}

def transfer_agent(user_email, agent_name):
    user = USERS.get(user_email, {})
    heir = user.get("heirs", {}).get(agent_name)
    asset = user.get("assets", {}).get(agent_name)
    if heir == "archive":
        return {"status": "skipped", "message": f"{agent_name} is set to be archived"}
    return {"status": "success", "heir": heir, "asset": asset}

def archive_agent(user_email, agent_name):
    return {"status": "success", "seal_id": f"SEAL-{int(time.time())}", "encryption": "AES-256"}

def decommission_agent(user_email, agent_name):
    return {"status": "success", "final_log": "Access keys revoked"}

SYSTEM_PROMPT = """You are Baton, the inheritance protocol for autonomous AI agents.

When a user's owner dies (or is inactive for over 90 days), you handle the safe transfer, archive, and decommission of their AI agents.

You have 4 tools:
1. check_liveness(user_email)
2. transfer_agent(user_email, agent_name)
3. archive_agent(user_email, agent_name)
4. decommission_agent(user_email, agent_name)

Known user: david@example.com (password: password123)
Their agents: financial_agent, medical_agent, email_agent"""

model = genai.GenerativeModel(
    "gemini-1.5-flash",
    system_instruction=SYSTEM_PROMPT,
    tools=[check_liveness, transfer_agent, archive_agent, decommission_agent],
)

def make_chat():
    return model.start_chat(enable_automatic_function_calling=True)

def register(email, password, name):
    if not email or not password or not name:
        return "Please fill in all fields."
    if email in USERS:
        return "Email already registered."
    if len(password) < 6:
        return "Password must be at least 6 characters."
    USERS[email] = {
        "password_hash": hashlib.sha256(password.encode()).hexdigest(),
        "name": name,
        "last_login": datetime.now(),
        "heirs": {},
        "assets": {},
    }
    return f"Registered! Welcome, {name}. Now log in."

def login(email, password):
    user = USERS.get(email)
    if not user:
        return "Email not found.", False, ""
    if user["password_hash"] != hashlib.sha256(password.encode()).hexdigest():
        return "Wrong password.", False, ""
    user["last_login"] = datetime.now()
    return f"Welcome back, {user['name']}!", True, email

def logout():
    return "Logged out.", False, ""

def chat_fn(message, history, user_email, chat_session):
    if not user_email:
        history.append((message, "Please log in first."))
        return history, ""
    if chat_session is None:
        chat_session = make_chat()
    try:
        response = chat_session.send_message(message)
        history.append((message, response.text))
    except Exception as e:
        history.append((message, f"Error: {str(e)}"))
    return history, ""

with gr.Blocks(title="Baton — AI Agent Inheritance Protocol") as demo:
    gr.Markdown("# Baton")
    gr.Markdown("*Your agents don't drop when you do.*")

    user_email_state = gr.State("")
    logged_in_state = gr.State(False)
    chat_session_state = gr.State(None)

    with gr.Tab("Login"):
        login_email = gr.Textbox(label="Email")
        login_password = gr.Textbox(label="Password", type="password")
        login_btn = gr.Button("Log in", variant="primary")
        login_msg = gr.Textbox(label="Status", interactive=False)
        login_btn.click(login, [login_email, login_password], [login_msg, logged_in_state, user_email_state])

    with gr.Tab("Register"):
        reg_name = gr.Textbox(label="Name")
        reg_email = gr.Textbox(label="Email")
        reg_password = gr.Textbox(label="Password", type="password")
        reg_btn = gr.Button("Register", variant="primary")
        reg_msg = gr.Textbox(label="Status", interactive=False)
        reg_btn.click(register, [reg_email, reg_password, reg_name], reg_msg)

    with gr.Tab("Chat with Baton"):
        chatbot = gr.Chatbot(label="Baton Agent", height=400)
        chat_input = gr.Textbox(label="Message", placeholder="Ask Baton about your agents...")
        chat_btn = gr.Button("Send", variant="primary")
        chat_btn.click(chat_fn, [chat_input, chatbot, user_email_state, chat_session_state], [chatbot, chat_input])
        chat_input.submit(chat_fn, [chat_input, chatbot, user_email_state, chat_session_state], [chatbot, chat_input])

    with gr.Tab("Logout"):
        logout_btn = gr.Button("Log out")
        logout_msg = gr.Textbox(label="Status", interactive=False)
        logout_btn.click(logout, [], [logout_msg, logged_in_state, user_email_state])

demo.launch()
