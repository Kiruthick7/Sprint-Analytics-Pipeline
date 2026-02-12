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

# Comment summaries (for backlog + on-hold). List format is built in report_builder; this output is shown as "Comment summaries" below it.
# Styled for scrum masters, engineering managers, directors, and stakeholders—use plain language; many readers are not deeply technical.
comment_summary_template = PromptTemplate(
    input_variables=["team_name", "sprint_name", "backlog_issues_context", "on_hold_issues_context"],
    template="""You are writing a brief, actionable summary for a sprint report. The audience includes Scrum Masters, Engineering Managers, Directors, and other stakeholders. Many readers do not have deep technical knowledge—use plain, professional language and avoid jargon, acronyms, or technical detail unless necessary. Focus on what needs attention and why it matters.

**Team:** {team_name}
**Sprint:** {sprint_name}

**Your task:** Write only the **Comment summaries** section. Use the issue summary and comment excerpts below to write one short, clear line per issue. Explain in business-friendly terms: what is the situation, why is it on hold (for On Hold issues), or what context do the comments add (for Backlog). If there are no useful comments, say so simply (e.g. "No updates in comments.").

- **Comment summaries (Backlog):** One line per Backlog issue: [Issue key]: [plain-language summary or "No updates in comments."]. Keep the same order as the list.
- **Comment summaries (On Hold):** One line per On Hold issue: [Issue key]: [why it is on hold or what’s blocking it, in plain language]. Keep the same order as the list.

If a list is empty, write "None." No code, no SQL, no tables. One line break between list items.

**Issues on Backlog (with comment excerpts):**
{backlog_issues_context}

**Issues on Hold (with comment excerpts):**
{on_hold_issues_context}
""",
)
sprint_chain_comments = RunnableSequence(comment_summary_template | llm)
