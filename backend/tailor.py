import json
import re
from datetime import date
from pydantic import BaseModel, Field
import config
from claude_cache import CachedPrompt
from ai_providers import (
    AIProviderError,
    AIProviderSettings,
    default_model,
    generate_structured,
    normalize_provider,
    claude_workflow_key,
    provider_label,
)
from operations import OperationCancelled
from typing import Callable


class TailoringResponse(BaseModel):
    tailored_resume: str = Field(description="Complete tailored resume in Markdown.")
    cover_letter: str = Field(description="Complete cover letter in plain text.")


class JobMatchResponse(BaseModel):
    match_score: int = Field(ge=0, le=100)
    match_analysis: str


class BatchJobMatch(BaseModel):
    index: int = Field(ge=0)
    match_score: int = Field(ge=0, le=100)
    match_analysis: str


class BatchMatchResponse(BaseModel):
    matches: list[BatchJobMatch]


FACTUAL_EVIDENCE_CONTRACT = """Factual evidence contract for candidate claims:
- Use the candidate's resume and any supplied professional evidence as the sources of candidate facts. A job posting describes the employer's requirements, not the candidate's experience.
- Omitted skills, certifications, licenses and work authorization are unknown, not confirmed absent. Say "not documented" or "requires confirmation" when relevant; do not say the candidate has none or lacks experience unless the candidate evidence explicitly establishes that absence.
- Preserve explicit positive and negative candidate statements. If candidate sources conflict, identify the conflict as requiring confirmation rather than resolving it by guessing.
- Preserve each employer, role, date, task and metric attribution. Separate facts do not establish a relationship: API maintenance is not API development; automated tests are not necessarily API tests; dashboards are not necessarily connected to a separately mentioned reporting pipeline.
- Preferred requirements do not become mandatory requirements or candidate credentials. Do not invent responsibilities, production scope, qualifications or causal relationships to improve the fit.
"""


def format_cover_letter_date(value: date) -> str:
    """Format dates without platform-specific strftime day directives."""
    return f"{value.strftime('%B')} {value.day}, {value.year}"


def finalize_cover_letter(cover_letter: str, letter_date: date | None = None) -> str:
    """Resolve date tokens so unfinished template text is never delivered."""
    text = (cover_letter or "").strip()
    if not text:
        raise ValueError("The AI provider returned an empty cover letter.")

    concrete_date = format_cover_letter_date(letter_date or date.today())
    text = re.sub(r"(?i)\[\s*date\s*\]", concrete_date, text)
    text = re.sub(r"(?i)\{\s*date\s*\}", concrete_date, text)
    text = re.sub(r"(?i)<\s*date\s*>", concrete_date, text)
    if re.search(r"(?i)\[\s*(?:insert\s+)?date\s*(?:here)?\s*\]", text):
        raise ValueError("The generated cover letter contains an unresolved date placeholder.")
    return text

def _provider_settings(api_key: str | None, ai_provider: str, ai_model: str | None) -> AIProviderSettings:
    provider = normalize_provider(ai_provider)
    key = (api_key or "").strip()
    if not key:
        if provider == 'anthropic':
            key = claude_workflow_key()
        else:
            key = config.get_openai_api_key() if provider == "openai" else config.get_gemini_api_key()
    return AIProviderSettings(provider, (ai_model or default_model(provider)).strip(), key)

RESUME_MODE_GUIDANCE = {
    "it": "Emphasize technical depth, architecture, delivery outcomes, platforms, and relevant tools without keyword stuffing.",
    "technical_executive": "Lead with enterprise scope, transformation outcomes, operating model, governance, budgets, and leadership impact.",
    "general_professional": "Use broadly applicable accomplishment-focused language and a conventional reverse-chronological structure.",
    "federal": "Use detailed duty scope, month/year dates, hours where known, and qualification evidence; do not invent federal-specific facts.",
    "healthcare": "Prioritize licenses, patient or operational outcomes, compliance, safety, and domain systems when evidenced.",
    "education": "Prioritize teaching, curriculum, learner outcomes, credentials, research, and service when evidenced.",
    "sales": "Prioritize quota, revenue, pipeline, territory, retention, and customer outcomes when evidenced.",
    "trades_operations": "Prioritize licenses, safety, equipment, throughput, quality, scheduling, and hands-on scope when evidenced.",
    "academic_cv": "Use an academic CV structure and retain relevant publications, research, teaching, grants, and service; the two-page resume limit does not apply.",
    "cover_letter": "Keep the resume conventional while placing extra emphasis on a specific, persuasive cover letter.",
}

RESUME_SECTION_TEMPLATES = {
    "it": ("Summary", "Technical Skills", "Professional Experience", "Projects", "Portfolio & Work Samples", "Licenses & Certifications", "Education"),
    "technical_executive": ("Executive Summary", "Leadership Competencies", "Professional Experience", "Selected Projects", "Board & Advisory", "Portfolio & Work Samples", "Licenses & Certifications", "Education"),
    "general_professional": ("Professional Summary", "Core Skills", "Professional Experience", "Projects", "Portfolio & Work Samples", "Licenses & Certifications", "Education"),
    "federal": ("Summary of Qualifications", "Core Competencies", "Professional Experience", "Projects & Work Samples", "Licenses & Certifications", "Education"),
    "healthcare": ("Professional Summary", "Licenses & Certifications", "Clinical Skills", "Professional Experience", "Projects & Work Samples", "Education"),
    "education": ("Professional Summary", "Credentials & Licenses", "Teaching Experience", "Education", "Projects & Work Samples", "Research & Publications", "Service"),
    "sales": ("Professional Summary", "Sales Competencies", "Professional Experience", "Achievements", "Projects & Work Samples", "Certifications", "Education"),
    "trades_operations": ("Summary", "Licenses & Certifications", "Skills", "Professional Experience", "Projects & Work Samples", "Education"),
    "academic_cv": ("Education", "Academic Appointments", "Research", "Projects & Work Samples", "Publications", "Teaching", "Grants & Awards", "Portfolio", "Service", "Professional Experience"),
    "cover_letter": ("Professional Summary", "Core Skills", "Professional Experience", "Projects", "Portfolio & Work Samples", "Licenses & Certifications", "Education"),
}


def _canonical_section(title: str, template: tuple[str, ...]) -> str | None:
    normalized = re.sub(r"[^a-z& ]", "", title.lower()).strip()
    exact = {section.lower(): section for section in template}
    if normalized in exact:
        return exact[normalized]
    semantic = (
        (("summary", "profile", "objective", "qualifications"), ("summary", "qualifications")),
        (("skill", "competenc", "expertise", "technolog"), ("skill", "competenc")),
        (("experience", "employment", "career"), ("experience", "appointments")),
        (("project",), ("project",)),
        (("portfolio", "work sample"), ("portfolio", "work sample", "project")),
        (("certif", "license", "credential"), ("certif", "license", "credential")),
        (("education",), ("education",)),
        (("publication",), ("publication",)),
        (("research",), ("research",)),
        (("teaching",), ("teaching",)),
        (("grant", "award", "achievement"), ("grant", "award", "achievement")),
        (("board", "advisory", "service"), ("board", "advisory", "service")),
    )
    for source_terms, target_terms in semantic:
        if any(term in normalized for term in source_terms):
            match = next(
                (section for term in target_terms for section in template if term in section.lower()),
                None,
            )
            if match:
                return match
    return None


def apply_resume_section_template(markdown: str, resume_mode: str) -> str:
    """Normalize generated level-two sections into a deterministic mode order."""
    template = RESUME_SECTION_TEMPLATES.get(resume_mode, RESUME_SECTION_TEMPLATES["general_professional"])
    matches = list(re.finditer(r"(?m)^##\s+(.+?)\s*$", markdown))
    if not matches:
        raise ValueError("Tailored resume did not contain the required Markdown sections.")
    preamble = markdown[:matches[0].start()].rstrip()
    grouped: dict[str, list[str]] = {}
    unknown = []
    for index, match in enumerate(matches):
        body_end = matches[index + 1].start() if index + 1 < len(matches) else len(markdown)
        body = markdown[match.end():body_end].strip()
        canonical = _canonical_section(match.group(1), template)
        if not canonical:
            unknown.append(match.group(1).strip())
            continue
        if body:
            grouped.setdefault(canonical, []).append(body)
    if unknown:
        raise ValueError(f"Tailored resume used unsupported section(s) for {resume_mode}: {', '.join(unknown)}")
    if not grouped:
        raise ValueError("Tailored resume sections were empty.")
    sections = [f"## {section}\n\n" + "\n\n".join(grouped[section]) for section in template if section in grouped]
    return "\n\n".join(([preamble] if preamble else []) + sections).strip()


def _cached_tailoring_prompt(base_resume_text, supplemental_evidence, resume_mode, mode_guidance,
                             section_template, page_contract, letter_date, job_title,
                             company_name, job_description):
    """Explicit candidate-only variant; default callers retain their original prompt.

    No cache hit is assumed and no input is padded to reach a provider threshold.
    Reordering is a new prompt variant requiring its own quality acceptance.
    """
    prefix = f"""
    You are an expert resume writer and career coach for both technical and non-technical professions. Your task is to perform two actions for a candidate applying to the job supplied below:
    1. Tailor their base resume to match the job requirements, emphasizing only relevant, evidenced skills, projects, and work experience. Maintain absolute factual accuracy: DO NOT invent jobs, dates, degrees, credentials, technologies, metrics, or companies.
    2. Write a customized, compelling, and professional cover letter (under 350 words, 3-4 paragraphs) addressed to the hiring team at the company supplied below.

    {FACTUAL_EVIDENCE_CONTRACT}

    Cover-letter contract:
    - Use the concrete letter date supplied below in the letter heading.
    - Never emit [Date], {{Date}}, <Date>, or any other placeholder token.
    - When a hiring manager's name is unknown, address the letter to "Hiring Team" without a placeholder.

    Resume formatting contract:
    - Selected resume mode: {resume_mode}. Mode guidance: {mode_guidance}
    - Use only the applicable level-two sections from this exact ordered template, omitting empty sections: {"; ".join(section_template)}.
    - {page_contract}
    - Use only standard Markdown headings (#, ##, ###), paragraphs, bold/italics, and flat bullet lists.
    - Do not use horizontal rules, tables, columns, images, badges, skill ratings, or decorative symbols.
    - Use the supplied template's conventional section names for the profession, including relevant Skills, Projects, Portfolio, Work Samples, Licenses, and Certifications sections only when supported by evidence.
    - Limit the summary to 3-4 lines. Use at most 4-6 concise bullets for the most recent role and 2-3 for older roles. Each bullet should normally fit in 1-2 lines.
    - Prefer demonstrated outcomes over keyword repetition. Remove or compress older and irrelevant material instead of shrinking the text.

    Candidate's Base Resume:
    \"\"\"{base_resume_text}\"\"\"

    Candidate's Supplemental Professional Evidence:
    \"\"\"{supplemental_evidence}\"\"\"
    Treat these notes as factual source material supplied by the candidate. Use only relevant items, preserve URLs when useful, and never infer or invent missing dates, issuers, credential status, project outcomes, or metrics.
    """
    suffix = f"""
    Job Details:
    - Concrete letter date: {letter_date}
    - Job Title: {job_title}
    - Company: {company_name}
    - Job Description:
    \"\"\"{job_description}\"\"\"

    Return the complete tailored resume and cover letter using the supplied response schema.
    """
    return CachedPrompt(prefix, suffix)


def tailor_resume_and_cover_letter(
    base_resume_text: str,
    job_title: str,
    company_name: str,
    job_description: str,
    api_key: str = None,
    resume_mode: str = "general_professional",
    cancel_check: Callable[[], None] | None = None,
    ai_provider: str = "gemini",
    ai_model: str | None = None,
    professional_evidence: str = "",
    claude_prompt_cache: bool = False,
) -> dict:
    """
    Consolidates resume tailoring and cover letter drafting into one structured AI request.
    
    Args:
        base_resume_text (str): The candidate's raw base resume.
        job_title (str): The target job title.
        company_name (str): The name of the hiring company.
        job_description (str): The description/requirements of the job.
        api_key (str, optional): The selected provider's API key.
        
    Returns:
        dict: A dictionary containing success status, tailored resume, and cover letter text.
    """
    if cancel_check:
        cancel_check()
    if type(claude_prompt_cache) is not bool or (claude_prompt_cache and normalize_provider(ai_provider) != 'anthropic'):
        raise ValueError('Prompt caching is an explicit Claude-only candidate option')
    settings = _provider_settings(api_key, ai_provider, ai_model)

    letter_date = date.today()
    formatted_letter_date = format_cover_letter_date(letter_date)
    mode_guidance = RESUME_MODE_GUIDANCE.get(resume_mode, RESUME_MODE_GUIDANCE["general_professional"])
    section_template = RESUME_SECTION_TEMPLATES.get(resume_mode, RESUME_SECTION_TEMPLATES["general_professional"])
    page_contract = "The academic CV may exceed two pages when the evidenced content requires it." if resume_mode == "academic_cv" else "The resume must fit within two US Letter pages at 10-point type."
    supplemental_evidence = professional_evidence.strip() or "No supplemental professional evidence was provided."

    # Combine resume tailoring and cover letter writing into a single prompt.
    prompt = f"""
    You are an expert resume writer and career coach for both technical and non-technical professions. Your task is to perform two actions for a candidate applying to a job:
    1. Tailor their base resume to match the job requirements, emphasizing only relevant, evidenced skills, projects, and work experience. Maintain absolute factual accuracy: DO NOT invent jobs, dates, degrees, credentials, technologies, metrics, or companies.
    2. Write a customized, compelling, and professional cover letter (under 350 words, 3-4 paragraphs) addressed to the hiring team at {company_name}.

    {FACTUAL_EVIDENCE_CONTRACT}

    Cover-letter contract:
    - Use the concrete date "{formatted_letter_date}" in the letter heading.
    - Never emit [Date], {{Date}}, <Date>, or any other placeholder token.
    - When a hiring manager's name is unknown, address the letter to "Hiring Team" without a placeholder.

    Resume formatting contract:
    - Selected resume mode: {resume_mode}. Mode guidance: {mode_guidance}
    - Use only the applicable level-two sections from this exact ordered template, omitting empty sections: {"; ".join(section_template)}.
    - {page_contract}
    - Use only standard Markdown headings (#, ##, ###), paragraphs, bold/italics, and flat bullet lists.
    - Do not use horizontal rules, tables, columns, images, badges, skill ratings, or decorative symbols.
    - Use the supplied template's conventional section names for the profession, including relevant Skills, Projects, Portfolio, Work Samples, Licenses, and Certifications sections only when supported by evidence.
    - Limit the summary to 3-4 lines. Use at most 4-6 concise bullets for the most recent role and 2-3 for older roles. Each bullet should normally fit in 1-2 lines.
    - Prefer demonstrated outcomes over keyword repetition. Remove or compress older and irrelevant material instead of shrinking the text.
    
    Job Details:
    - Job Title: {job_title}
    - Company: {company_name}
    - Job Description:
    \"\"\"{job_description}\"\"\"
    
    Candidate's Base Resume:
    \"\"\"{base_resume_text}\"\"\"

    Candidate's Supplemental Professional Evidence:
    \"\"\"{supplemental_evidence}\"\"\"
    Treat these notes as factual source material supplied by the candidate. Use only relevant items, preserve URLs when useful, and never infer or invent missing dates, issuers, credential status, project outcomes, or metrics.
    
    Return the complete tailored resume and cover letter using the supplied response schema.
    """

    if claude_prompt_cache:
        prompt = _cached_tailoring_prompt(base_resume_text, supplemental_evidence, resume_mode,
            mode_guidance, section_template, page_contract, formatted_letter_date,
            job_title, company_name, job_description)

    try:
        if cancel_check:
            cancel_check()
        res_data = generate_structured(settings, prompt, TailoringResponse)
        if cancel_check:
            cancel_check()
        tailored_resume = res_data.get("tailored_resume", "").strip()
        cover_letter = finalize_cover_letter(res_data.get("cover_letter", ""), letter_date)

        # Clean up markdown tags if the model still wrapped them (rare for JSON output)
        if tailored_resume.startswith("```markdown"):
            tailored_resume = tailored_resume[11:]
        elif tailored_resume.startswith("```"):
            tailored_resume = tailored_resume[3:]
        if tailored_resume.endswith("```"):
            tailored_resume = tailored_resume[:-3]
        tailored_resume = tailored_resume.strip()
        tailored_resume = apply_resume_section_template(tailored_resume, resume_mode)

        return {
            "success": True,
            "tailored_resume": tailored_resume,
            "cover_letter": cover_letter
        }
    except OperationCancelled:
        raise
    except AIProviderError as e:
        return {"success": False, "error": str(e)}
    except Exception as e:
        return {
            "success": False,
            "error": f"Tailoring failed after the {provider_label(settings.provider)} response: {str(e)}"
        }

def analyze_job_match(
    base_resume_text: str,
    job_title: str,
    company_name: str,
    job_description: str,
    api_key: str = None,
    cancel_check: Callable[[], None] | None = None,
    ai_provider: str = "gemini",
    ai_model: str | None = None,
) -> dict:
    """
    Analyzes how well the candidate's resume matches a single job description
    and returns a match score and structural analysis.
    
    Args:
        base_resume_text (str): The candidate's raw base resume.
        job_title (str): The target job title.
        company_name (str): The name of the hiring company.
        job_description (str): The description/requirements of the job.
        api_key (str, optional): The selected provider's API key.
        
    Returns:
        dict: A dictionary containing success status, match score, and analysis markdown.
    """
    if cancel_check:
        cancel_check()
    settings = _provider_settings(api_key, ai_provider, ai_model)

    prompt = f"""
    You are an AI Job Matching Assistant. Analyze the match between the candidate's resume and a job description.

    {FACTUAL_EVIDENCE_CONTRACT}
    
    Job Title: {job_title}
    Company: {company_name}
    
    Job Description:
    \"\"\"{job_description}\"\"\"
    
    Candidate's Resume:
    \"\"\"{base_resume_text}\"\"\"
    
    Determine:
    1. A match score between 0 and 100.
    2. A concise analysis summarizing:
       - Strengths (why the candidate fits)
       - Gaps (missing key skills or experiences)
       - Recommendations (how to position themselves)
       
    Return the score and analysis using the supplied response schema.
    """
    
    try:
        if cancel_check:
            cancel_check()
        data = generate_structured(settings, prompt, JobMatchResponse)
        if cancel_check:
            cancel_check()
        return {
            "success": True,
            "match_score": data.get("match_score", 50),
            "match_analysis": data.get("match_analysis", "No analysis provided.")
        }
    except OperationCancelled:
        raise
    except AIProviderError as e:
        return {"success": False, "error": str(e)}
    except Exception as e:
        return {
            "success": False,
            "error": f"Failed to analyze the match with {provider_label(settings.provider)}: {str(e)}"
        }

def analyze_job_matches_batch(
    base_resume_text: str,
    jobs_list: list,
    api_key: str = None,
    ai_provider: str = "gemini",
    ai_model: str | None = None,
) -> dict:
    """
    Analyzes multiple job postings in a single API call to conserve free-tier
    request quotas and evaluate job compatibility in bulk.
    
    Args:
        base_resume_text (str): The candidate's raw base resume.
        jobs_list (list): A list of dictionaries representing discovered jobs to match.
        api_key (str, optional): The selected provider's API key.
        
    Returns:
        dict: A dictionary containing success status and a list of structured match results.
    """
    settings = _provider_settings(api_key, ai_provider, ai_model)
        
    # Format the jobs list compactly to save tokens
    formatted_jobs = []
    for idx, job in enumerate(jobs_list):
        formatted_jobs.append({
            "index": idx,
            "title": job["title"],
            "company": job["company"],
            "description": job["description"][:1000] if job["description"] else ""  # truncate to save tokens
        })
        
    prompt = f"""
    You are an AI Job Matching Assistant. Analyze the match between the candidate's resume and a list of job postings.

    {FACTUAL_EVIDENCE_CONTRACT}
    
    Candidate's Resume:
    \"\"\"{base_resume_text}\"\"\"
    
    Job Postings:
    {json.dumps(formatted_jobs, indent=2)}
    
    For each job posting, determine:
    1. A match score between 0 and 100 based on how well the candidate's skills and experience align with the job description.
    2. A concise analysis (1-2 sentences) summarizing why the candidate fits, or what key gaps exist.
    
    Return one result for every posting, in the same order, using the supplied response schema.
    """
    
    try:
        data = generate_structured(settings, prompt, BatchMatchResponse)
        return {
            "success": True,
            "matches": data.get("matches", [])
        }
    except AIProviderError as e:
        return {"success": False, "error": str(e), "error_code": e.code}
    except Exception as e:
        return {
            "success": False,
            "error": f"Failed to analyze matches with {provider_label(settings.provider)}: {str(e)}",
            "error_code": "provider_error",
        }
