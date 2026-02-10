"""LLM and prompt for daily scrum report summary."""
import os
import logging
from langchain_core.prompts import PromptTemplate
from langchain_core.runnables import RunnableSequence

logger = logging.getLogger(__name__)

LLM_PROVIDER = (os.getenv("LLM_PROVIDER") or "groq").strip().lower()

try:
    if LLM_PROVIDER == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI
        _api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        if not _api_key:
            raise ValueError("GEMINI_API_KEY or GOOGLE_API_KEY required for LLM_PROVIDER=gemini")
        llm = ChatGoogleGenerativeAI(
            model="gemini-1.5-flash",
            temperature=0.2,
            max_tokens=8192,
            google_api_key=_api_key,
        )
        logger.info("Using LLM: Gemini (gemini-1.5-flash, 1M context)")
    else:
        from langchain_groq import ChatGroq
        llm = ChatGroq(  # type: ignore[call-arg]
            model="meta-llama/llama-4-scout-17b-16e-instruct",
            temperature=0.2,
            max_tokens=4096,
        )
        logger.info("Using LLM: Groq (llama-4-scout-17b, 30K TPM)")
except Exception as e:
    logger.error(f"Failed to initialize LLM: {e}")
    raise

daily_scrum_template = PromptTemplate(
    input_variables=["team_name", "sprint_name", "sprint_features", "backlog_issues_context", "on_hold_issues_context"],
    template="""You are writing a brief, actionable summary for the Scrum Master, directors, and stakeholders. The report audience needs a clear read on what needs attention—not progress metrics (those come from Jira).

**Team:** {team_name}
**Sprint:** {sprint_name}

**Your task:** Write one section only: **Blockers, Delays or Issues Needing Attention**.

Include only these two subsections (use short bullet points; do not add extra blank lines between subsections):
- **Issues on Backlog:** For each Backlog issue listed below, write one bullet with the Jira key and a one-line summary (use the issue summary and comment excerpts provided). If the list is empty, write "None."
- **Issues on Hold:** For each On Hold issue listed below, write one bullet with the Jira key and a one-line summary of why it is on hold (use the issue summary and comment excerpts provided). If the list is empty, write "None."

Do not include "Delays" or "Risks & dependencies" sections at all.

**Issues on Backlog (use this to write the Issues on Backlog bullets):**
{backlog_issues_context}

**Issues on Hold (use this to write the Issues on Hold bullets):**
{on_hold_issues_context}

**Style:** Plain, professional language. One line break between list items; no extra gaps. No code, no SQL, no tables.

**Issue data (for context only):**
{sprint_features}
""",
)

sprint_chain = RunnableSequence(daily_scrum_template | llm)
