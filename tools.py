import time
from datetime import datetime, timedelta

USERS = {"david": {
    "last_login": datetime.now() - timedelta(days=91),
    "heirs": {"financial": "daughter@example.com", "medical": "spouse@example.com"},
    "assets": {"financial": "$47,000", "medical": "Records"},
}}

def check_liveness(user_id):
    u = USERS.get(user_id)
    if not u: return {"status": "error"}
    d = (datetime.now() - u["last_login"]).days
    return {"status": "triggered" if d > 90 else "active", "days": d}

def transfer_agent(user_id, name):
    u = USERS[user_id]
    return {"status": "success", "heir": u["heirs"].get(name), "asset": u["assets"].get(name)}

def archive_agent(user_id, name):
    return {"status": "success", "seal_id": "SEAL-" + str(int(time.time()))}

def decommission_agent(user_id, name):
    return {"status": "success", "final_log": "Keys revoked"}
