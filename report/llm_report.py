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
        logger.debug("Using LLM: Gemini (gemini-1.5-flash, 1M context)")
    else:
        from langchain_groq import ChatGroq
        llm = ChatGroq(  # type: ignore[call-arg]
            model="meta-llama/llama-4-scout-17b-16e-instruct",
            temperature=0.2,
            max_tokens=4096,
        )
        logger.debug("Using LLM: Groq (llama-4-scout-17b, 30K TPM)")
except Exception as e:
    logger.error(f"Failed to initialize LLM: {e}")
    raise

# One-line comment summary per issue (key -> summary). Output is parsed in report_builder and merged into each issue line as "key <-> status <-> description: summary".
# Audience: scrum masters, EMs, directors, stakeholders—plain language, minimal jargon.
comment_summary_template = PromptTemplate(
    input_variables=["team_name", "sprint_name", "backlog_issues_context", "on_hold_issues_context", "blocked_issues_context"],
    template="""You are writing one short summary per Jira issue for a sprint report. The audience includes Scrum Masters, Engineering Managers, Directors, and stakeholders; use plain, professional language and avoid jargon.

**Team:** {team_name}
**Sprint:** {sprint_name}

**Output format (strict):** Output exactly one line per issue. Each line must be in this form only:
KEY: one-line summary

Use the exact Jira key (e.g. DGROCERY-7216). Do not repeat or copy the ticket title/summary—write only a short summary of the comments or the reason (e.g. why on hold). Never output the issue title as the summary.
- **Backlog:** Summarize what the comments add. If there are no comments or nothing useful, output nothing for that key (omit the line entirely so the report shows blank; we will show "Blocked by KEY" separately when applicable).
- **On Hold:** Summarize why it is on hold or what is blocking it.
- **Blocked:** Summarize why it is blocked or what the comments say.
One line per issue, no bullets or extra headings.

**Order:** First Backlog issues in the order listed, then On Hold issues, then Blocked issues. If a list is empty, output nothing for that list.

**Issues on Backlog (with comment excerpts):**
{backlog_issues_context}

**Issues on Hold (with comment excerpts):**
{on_hold_issues_context}

**Issues on Blocked (with comment excerpts):**
{blocked_issues_context}
""",
)
sprint_chain_comments = RunnableSequence(comment_summary_template | llm)
