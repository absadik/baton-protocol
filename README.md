# Baton
> Your agents don't drop when you do.

Baton is the first inheritance protocol for autonomous AI agents. When you die, your agents keep running. Baton makes sure they pass to the right hands — safely, legally, and on your terms.

## The Problem
Every autonomous agent you own will outlive you. Nobody asks: what happens next? It pays bills from a frozen account. It sends emails as a dead person. It manages your digital life as a ghost process.

## Who It's For
Everyone who uses AI agents. And their families.

## How It Works
1. Liveness Check — You re-authenticate weekly. Miss 90 days, the protocol activates.
2. Transfer Agent — Verified heirs receive your agents.
3. Archive Agent — Memories and logs sealed, signed, encrypted.
4. Decommission Agent — Access keys revoked. Final log written.

## Architecture
- Orchestrator: Strands Agents SDK
- Tools: check_liveness, transfer_agent, archive_agent, decommission_agent
- Runtime: Google Colab / Amazon Bedrock AgentCore

## Tech Stack
- Python 3
- Strands Agents SDK
- Amazon Bedrock (planned)
- boto3
- Google Colab

## Demo
Run in Google Colab:

pip install strands-agents boto3
wget -q https://raw.githubusercontent.com/absadik/baton-protocol/main/tools.py
from tools import check_liveness, transfer_agent, archive_agent, decommission_agent
print(check_liveness("david"))
print(transfer_agent("david", "financial"))

Expected output:
LIVENESS: {'status': 'triggered', 'days': 91}
TRANSFER: {'status': 'success', 'heir': 'daughter@example.com', 'asset': '$47,000'}

## Why It Matters
The $90 trillion wealth transfer is underway. Every autonomous agent user will eventually face this problem. No consumer product exists for agent inheritance. Baton is the first.

## Built For
Agents for Humans Hackathon 2026 — Everyday Agents Track

## Team
Built solo by Abubakar Isah (@absadik)

## License
MITdigital
