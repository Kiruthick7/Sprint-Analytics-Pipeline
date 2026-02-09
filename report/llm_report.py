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
    input_variables=["team_name", "sprint_name", "sprint_features"],
    template="""
You are a professional scrum master analyzing the current sprint for Team: {team_name}.
Sprint: {sprint_name}

You are provided with a list of Jira issues including issue key, summary, status, assignee, reporter, and recent comments.

### Overall Progress and Completion Status
- Summarize overall progress with key metrics and completion status

### Blockers, Delays, or Issues Needing Attention
- List each blocker with issue key and brief description (if any exist)
- If no blockers, state "No blockers identified"

### Risks and Areas Needing Updates
- Highlight risks, dependencies, and areas needing attention

Do not include code or SQL interpretation.
If there is insufficient detail to summarize, clearly state what is missing and ask for updates.

Use the following issue data for context only (do not output a table):
{sprint_features}
""",
)

sprint_chain = RunnableSequence(daily_scrum_template | llm)
