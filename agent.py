from strands import Agent
from strands.models import BedrockModel
from tools import check_liveness, transfer_agent, archive_agent, decommission_agent

model = BedrockModel(
    model_id="us.amazon.nova-pro-v1:0",
    temperature=0.2,
)

SYSTEM_PROMPT = """
You are Baton, the inheritance protocol for autonomous AI agents.

Your job: when a user dies, safely transfer, archive, or decommission their AI agents.

RULES:
1. Always check_liveness first.
2. Only proceed if status is 'triggered'.
3. Transfer financial and medical agents to heirs.
4. Archive email agents (do not transfer them).
5. Decommission agents that have no heir.
6. Report clearly: what you did, and why.
"""

baton = Agent(
    model=model,
    system_prompt=SYSTEM_PROMPT,
    tools=[
        check_liveness,
        transfer_agent,
        archive_agent,
        decommission_agent,
    ],
)

if __name__ == "__main__":
    print("Baton is waking up...")
    result = baton(
        "Check the liveness of user 'david'. "
        "If the deadman switch is triggered, "
        "transfer his financial agent to his heir, "
        "archive his email agent, and "
        "write a final report."
    )
    print(result)
