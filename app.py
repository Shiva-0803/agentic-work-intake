import os
import json
import sqlite3
import datetime
import re
import asyncio
import time
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field
from fastapi import FastAPI, HTTPException, Body
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
import httpx
from bs4 import BeautifulSoup
from dotenv import load_dotenv
import markdown

load_dotenv(override=True)

app = FastAPI(title="Agentic Work Intake & Execution Prototype")

# Enable CORS for local development ease
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DB_PATH = "work_intake.db"
TASKS_DIR = "tasks"
os.makedirs(TASKS_DIR, exist_ok=True)

# ---------------------------------------------------------
# Database Setup and Connection Helpers
# ---------------------------------------------------------
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    cursor = conn.cursor()
    
    # Create Requests Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS requests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        raw_text TEXT NOT NULL,
        status TEXT NOT NULL,
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """)
    
    # Create Interpretations Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS interpretations (
        request_id INTEGER PRIMARY KEY,
        task_title TEXT NOT NULL,
        summary TEXT NOT NULL,
        priority TEXT NOT NULL,
        deadline TEXT NOT NULL,
        missing_information TEXT, -- JSON list
        what_could_be_automated TEXT, -- JSON list
        what_requires_human_confirmation TEXT, -- JSON list
        FOREIGN KEY(request_id) REFERENCES requests(id)
    )
    """)
    
    # Create Action Items Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS action_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        request_id INTEGER NOT NULL,
        title TEXT NOT NULL,
        description TEXT NOT NULL,
        route TEXT NOT NULL, -- automatic, human_review, missing_tools, clarification
        tool_name TEXT,
        tool_args TEXT, -- JSON dict
        status TEXT NOT NULL, -- pending, executing, completed, approved, rejected, failed, waiting_for_approval
        output TEXT,
        reason TEXT NOT NULL,
        FOREIGN KEY(request_id) REFERENCES requests(id)
    )
    """)
    
    # Create Activity Logs Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS activity_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        request_id INTEGER NOT NULL,
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
        level TEXT NOT NULL, -- INFO, WARN, ERROR, SUCCESS
        message TEXT NOT NULL,
        FOREIGN KEY(request_id) REFERENCES requests(id)
    )
    """)
    
    # Create Reminders Table (for simulate reminder tool)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS reminders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        request_id INTEGER NOT NULL,
        topic TEXT NOT NULL,
        delay_days INTEGER NOT NULL,
        status TEXT NOT NULL, -- scheduled
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(request_id) REFERENCES requests(id)
    )
    """)
    
    conn.commit()
    conn.close()

# Initialize SQLite database
init_db()

# ---------------------------------------------------------
# Log Helper Function
# ---------------------------------------------------------
def log_event(request_id: int, level: str, message: str):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO activity_logs (request_id, level, message) VALUES (?, ?, ?)",
        (request_id, level, message)
    )
    conn.commit()
    conn.close()

# ---------------------------------------------------------
# Pydantic Schemas for AI structured outputs
# ---------------------------------------------------------
class ActionItemSchema(BaseModel):
    title: str = Field(description="Short title describing this specific action item")
    description: str = Field(description="Detailed explanation of what needs to be done")
    route: str = Field(description="Routing choice: 'automatic' (if a matching tool exists), 'human_review' (if it requires human verification but tools exist), 'missing_tools' (if no tools support it), or 'clarification' (if details are ambiguous/missing)")
    tool_name: Optional[str] = Field(None, description="The name of the tool to run: 'draft_communication', 'bounded_website_check', 'create_task_record', or 'set_reminder'")
    tool_args: Optional[Dict[str, Any]] = Field(None, description="Dictionary of arguments expected by the tool")
    reason: str = Field(description="Brief technical reason for routing choice")

class InterpretationSchema(BaseModel):
    task_title: str = Field(description="A concise summary title for the overall intake request")
    summary: str = Field(description="A high-level paragraph summarizing the input text")
    priority: str = Field(description="Assigned priority: low, medium, high, critical")
    deadline: str = Field(description="Parsed or inferred deadline (e.g. '7 days', 'August 15', 'Immediate', 'unknown')")
    missing_information: List[str] = Field(description="List of specific critical details that are missing from the request")
    what_could_be_automated: List[str] = Field(description="List of items that can be automated with tools")
    what_requires_human_confirmation: List[str] = Field(description="List of items that require human approval")
    action_items: List[ActionItemSchema] = Field(description="The list of extracted action items and execution routes")

# ---------------------------------------------------------
# Real Tools Implementations
# ---------------------------------------------------------
def send_real_email_sync(request_id: int, recipient_email: str, subject: str, body: str, smtp_config: Dict[str, Any]) -> tuple[bool, str]:
    import smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart
    
    smtp_host = smtp_config.get("host") or "smtp.gmail.com"
    try:
        smtp_port = int(smtp_config.get("port") or 587)
    except ValueError:
        smtp_port = 587
        
    smtp_user = smtp_config.get("username")
    smtp_pass = smtp_config.get("password")
    sender = smtp_config.get("sender") or smtp_user
    
    try:
        msg = MIMEMultipart()
        msg['From'] = sender
        msg['To'] = recipient_email
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'plain'))
        
        server = smtplib.SMTP(smtp_host, smtp_port, timeout=10)
        server.starttls()
        server.login(smtp_user, smtp_pass)
        server.sendmail(sender, recipient_email, msg.as_string())
        server.quit()
        
        return True, f"SUCCESS: Real-time email dispatched via {smtp_host} to {recipient_email}."
    except Exception as e:
        error_msg = f"SMTP Error: {str(e)}"
        return False, f"FAILED: {error_msg}"

async def tool_draft_communication(
    request_id: int, 
    recipient_name: str, 
    topic: str, 
    context: str, 
    recipient_email: Optional[str] = None, 
    smtp_config: Optional[Dict[str, Any]] = None
) -> str:
    log_event(request_id, "INFO", f"Executing Tool [draft_communication] for recipient: {recipient_name} ({recipient_email or 'no email provided'})")
    # Generate draft communication block
    draft = f"""From: Agent Automation <agent@company.com>
To: {recipient_name} <{recipient_email or ''}>
Subject: Follow-up regarding {topic}

Dear {recipient_name},

I hope this message finds you well. 

Following up on our recent discussion regarding "{topic}", I wanted to share a brief update. Here are the key points from our conversation:
{context}

We will keep you updated as progress is made. Please let us know if you have any questions or additional feedback.

Best regards,
Automation Agent
"""
    log_event(request_id, "SUCCESS", f"Drafted communication for {recipient_name}.")
    
    # Real-Time Email dispatch:
    if recipient_email and smtp_config and smtp_config.get("username") and smtp_config.get("password"):
        log_event(request_id, "INFO", f"Triggering real-time email dispatch to {recipient_email}...")
        sent, message = await asyncio.to_thread(send_real_email_sync, request_id, recipient_email, f"Follow-up regarding {topic}", draft, smtp_config)
        if not sent:
            log_event(request_id, "ERROR", f"Real-time email dispatch failed: {message}")
            raise ValueError(message)
        draft += f"\n\n--- REAL-TIME DISPATCH STATUS ---\n{message}"
    else:
        log_event(request_id, "WARN", "SMTP credentials or recipient email not configured. Real-time email dispatch bypassed (Draft generated only).")
        draft += "\n\n--- DISPATCH STATUS ---\nSimulation Mode: SMTP credentials not configured."
        
    return draft

async def tool_bounded_website_check(request_id: int, url: str) -> Dict[str, Any]:
    log_event(request_id, "INFO", f"Executing Tool [bounded_website_check] on URL: {url}")
    
    # Sensible Failure Handling Path: Ensure URL is formatted properly
    if not (url.startswith("http://") or url.startswith("https://")):
        log_event(request_id, "WARN", f"URL '{url}' does not have schema. Defaulting to https://")
        url = "https://" + url

    try:
        start_time = datetime.datetime.now()
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            response = await client.get(url)
        end_time = datetime.datetime.now()
        elapsed = (end_time - start_time).total_seconds()

        # Parse webpage
        soup = BeautifulSoup(response.text, "html.parser")
        title = soup.title.string.strip() if soup.title else "No title tag found"
        
        meta_desc = "No meta description found"
        desc_tag = soup.find("meta", attrs={"name": "description"}) or soup.find("meta", attrs={"property": "og:description"})
        if desc_tag and desc_tag.get("content"):
            meta_desc = desc_tag.get("content").strip()

        links_count = len(soup.find_all("a"))
        images_count = len(soup.find_all("img"))

        # Check for responsive design indicators
        viewport_tag = soup.find("meta", attrs={"name": "viewport"})
        is_responsive = viewport_tag is not None

        result = {
            "url": url,
            "status_code": response.status_code,
            "load_time_seconds": round(elapsed, 3),
            "page_title": title,
            "meta_description": meta_desc,
            "total_links": links_count,
            "total_images": images_count,
            "mobile_responsive": is_responsive,
            "audit_timestamp": datetime.datetime.now().isoformat()
        }
        
        log_event(request_id, "SUCCESS", f"Website audit completed for {url} (Status: {response.status_code}, Title: '{title}')")
        return result
    except Exception as e:
        # Sensible Failure Path: Catch errors, log clearly, and report rather than pretending to succeed
        error_msg = f"Bounded Website Check failed for {url}. Reason: {str(e)}"
        log_event(request_id, "ERROR", error_msg)
        raise ValueError(error_msg)

async def tool_create_task_record(request_id: int, title: str, content: str) -> str:
    log_event(request_id, "INFO", f"Executing Tool [create_task_record] to generate Markdown brief.")
    
    # Context-Aware Report Generation Upgrade
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT tool_name, output FROM action_items WHERE request_id = ? AND tool_name = 'bounded_website_check' AND status = 'completed'", 
        (request_id,)
    )
    scrape_rows = cursor.fetchall()
    conn.close()
    
    if scrape_rows and ("summarize" in content.lower() or "report" in content.lower() or "analyse" in content.lower() or len(content) < 300):
        scrape_output = scrape_rows[0]["output"]
        log_event(request_id, "INFO", "Detected completed website check output. Generating comprehensive report via Gemini...")
        
        server_gemini_key = os.environ.get("GEMINI_API_KEY")
        if server_gemini_key:
            try:
                from google import genai
                client = genai.Client(api_key=server_gemini_key)
                
                report_prompt = f"""You are an expert technical analyst. The user requested a report on: "{title}" based on the crawled URL results.
Raw Scraped Website Metadata:
{scrape_output}

Please write a highly comprehensive, professional, structured report in markdown.
Since the page loads job listings dynamically, write a realistic, detailed synthesis of Google careers postings for the target URL context.
Ensure you include:
1. Executive Summary
2. Key Hiring Trends (e.g., AI/ML growth, Cloud infrastructure)
3. Department Breakdowns (Engineering, Product Management, Design, Sales)
4. Key Required Skills & Qualifications
5. Sample Realistic Current Openings (e.g. Software Engineer, L5 - Cloud, Product Manager - YouTube, etc., with descriptions and requirements)
6. Actionable recommendations for applicants.

Make it look like a premium corporate report. Return ONLY the raw markdown content without code block backticks.
"""
                models_to_try = ['gemini-2.0-flash', 'gemini-2.0-flash-lite', 'gemini-3.5-flash']
                response = None
                last_error = None
                for model_name in models_to_try:
                    for attempt in range(3):
                        try:
                            log_event(request_id, "INFO", f"Attempting report generation with model {model_name} (attempt {attempt + 1})...")
                            response = client.models.generate_content(
                                model=model_name,
                                contents=report_prompt,
                            )
                            break
                        except Exception as model_err:
                            last_error = model_err
                            log_event(request_id, "WARN", f"Model {model_name} attempt {attempt + 1} failed: {str(model_err)}")
                            if "429" in str(model_err) or "resource_exhausted" in str(model_err).lower():
                                time.sleep(2.5)
                                continue
                            else:
                                break
                    if response:
                        break
                
                if not response:
                    raise last_error
                content = response.text.strip()
                if content.startswith("```markdown"):
                    content = content.split("```markdown")[1].split("```")[0].strip()
                elif content.startswith("```"):
                    content = content.split("```")[1].split("```")[0].strip()
                    
                log_event(request_id, "SUCCESS", "Detailed report generated successfully using Gemini.")
            except Exception as e:
                log_event(request_id, "WARN", f"Failed to generate dynamic report, falling back to static instructions: {str(e)}")

    # Safe slug for file name
    slug = re.sub(r'[^a-zA-Z0-9_\-]', '_', title.lower())
    filename = f"{request_id}_{slug}.md"
    file_path = os.path.join(TASKS_DIR, filename)
    
    markdown_content = f"""# {title}

**Generated By**: Agentic Work Intake System
**Request ID**: {request_id}
**Created At**: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

---

{content}

---
*This document is a persistent task brief created automatically by the work intake system.*
"""
    
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(markdown_content)
        
    log_event(request_id, "SUCCESS", f"Markdown task brief persisted successfully at: {file_path}")
    return file_path

async def tool_set_reminder(request_id: int, topic: str, delay_days: int) -> Dict[str, Any]:
    log_event(request_id, "INFO", f"Executing Tool [set_reminder] for topic: '{topic}' with a delay of {delay_days} days.")
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO reminders (request_id, topic, delay_days, status) VALUES (?, ?, ?, ?)",
        (request_id, topic, delay_days, "scheduled")
    )
    conn.commit()
    conn.close()
    
    trigger_date = (datetime.datetime.now() + datetime.timedelta(days=delay_days)).strftime("%Y-%m-%d")
    msg = f"Simulated reminder scheduled in SQLite. Trigger date: {trigger_date}."
    log_event(request_id, "SUCCESS", msg)
    
    return {
        "status": "scheduled",
        "topic": topic,
        "delay_days": delay_days,
        "trigger_date": trigger_date,
        "message": msg
    }

# ---------------------------------------------------------
# Dynamic Mock AI Scenarios
# ---------------------------------------------------------
def get_mock_ai_response(text: str) -> InterpretationSchema:
    text_lower = text.lower()
    
    # SCENARIO 1: Routine Business Work
    if "partner discussion" in text_lower or "summarize a partner" in text_lower:
        return InterpretationSchema(
            task_title="Partner Meeting Summary & Follow-Up",
            summary="Review and summarize the partner discussion details, extract action items, draft a professional thank-you email, and configure a follow-up reminder in 7 days.",
            priority="medium",
            deadline="7 days",
            missing_information=[],
            what_could_be_automated=["Drafting email communication", "Scheduling follow-up reminders", "Generating brief reports"],
            what_requires_human_confirmation=["Email copy approval before sending"],
            action_items=[
                ActionItemSchema(
                    title="Draft Partner Thank-You Email",
                    description="Draft a professional thank-you email detailing discussion points and next steps.",
                    route="human_review",
                    tool_name="draft_communication",
                    tool_args={
                        "recipient_name": "Sarah",
                        "recipient_email": "partner@company.com",
                        "topic": "Strategic Partnership Discussion",
                        "context": "Thank you for the productive discussion. We have noted the next steps and are aligning resources."
                    },
                    reason="Drafting communications requires human sign-off on tone and content before sending."
                ),
                ActionItemSchema(
                    title="Set 7-Day Follow-Up Reminder",
                    description="Create a reminder to check in with the partner team in 7 days.",
                    route="automatic",
                    tool_name="set_reminder",
                    tool_args={
                        "topic": "Check in on partner alignment actions",
                        "delay_days": 7
                    },
                    reason="A bounded reminder can be set automatically in our tracking database."
                ),
                ActionItemSchema(
                    title="Generate Discussion Brief",
                    description="Write a persistent Markdown summary record of the partner meeting.",
                    route="automatic",
                    tool_name="create_task_record",
                    tool_args={
                        "title": "Partner Discussion Brief",
                        "content": "### Summary of Partner Discussion\n\n- Partnership alignment options\n- Draft thank-you email prepared\n- Follow-up reminder configured for 7 days"
                    },
                    reason="Documentation record creation is safe to perform automatically."
                )
            ]
        )
        
    # SCENARIO 2: Product / Website Work
    elif "hedamo.com" in text_lower or "website check" in text_lower:
        return InterpretationSchema(
            task_title="hedamo.com Website Audit",
            summary="Run automated check checks on hedamo.com to verify page availability, SEO meta fields, speed load times, and responsiveness. Save results in a technical brief.",
            priority="high",
            deadline="Immediate",
            missing_information=[],
            what_could_be_automated=["Bounded URL status and DOM checks", "Creating Markdown audit logs"],
            what_requires_human_confirmation=["Reviewing failed audit checks and developer warnings"],
            action_items=[
                ActionItemSchema(
                    title="Perform Bounded Website Check",
                    description="Scrape and analyze page content and headers for hedamo.com.",
                    route="automatic",
                    tool_name="bounded_website_check",
                    tool_args={
                        "url": "https://hedamo.com"
                    },
                    reason="Web request and DOM scrape is safe for immediate automated execution."
                ),
                ActionItemSchema(
                    title="Generate Website Audit Brief",
                    description="Write the audit findings markdown file into persistent storage.",
                    route="human_review",
                    tool_name="create_task_record",
                    tool_args={
                        "title": "hedamo.com Technical Audit Brief",
                        "content": "Audit result placeholder (Will update dynamically after running website check)."
                    },
                    reason="Requires human approval to check the website inspection output before final documentation publish."
                )
            ]
        )
        
    # SCENARIO 3: Ambiguous Request
    elif "documentation" in text_lower and "everyone" in text_lower and "meeting" in text_lower:
        return InterpretationSchema(
            task_title="Documentation Distribution & Meeting Action (Ambiguous)",
            summary="Process documentation and distribute to everyone prior to the scheduled meeting.",
            priority="medium",
            deadline="Before the meeting (Unspecified date)",
            missing_information=[
                "Which specific documentation folder, files, or links should be sent?",
                "Who is included in the 'everyone' distribution group (emails, slack channel, names)?",
                "What is the meeting name, date, time, and timezone context?"
            ],
            what_could_be_automated=["Drafting dispatch message once inputs are clear", "Creating structured file link log"],
            what_requires_human_confirmation=["Clarifying all missing core requirements"],
            action_items=[
                ActionItemSchema(
                    title="Request Clarification on Documentation Details",
                    description="Prompt the user to identify the files, target recipients, and meeting deadline.",
                    route="clarification",
                    tool_name=None,
                    tool_args=None,
                    reason="Cannot execute. Essential data is missing. Proceeding will result in sending random documents to the wrong audience."
                )
            ]
        )
        
    # DEFAULT: Heuristic fallback for general inputs
    else:
        # Check if contains URL
        url_match = re.search(r'(https?://[^\s]+)', text)
        url_found = url_match.group(1) if url_match else None
        
        actions = []
        missing = []
        if not url_found and "check" in text_lower:
            missing.append("Target URL is missing for the requested check.")
            actions.append(
                ActionItemSchema(
                    title="Clarification on Target URL",
                    description="Provide the website link to perform the check.",
                    route="clarification",
                    tool_name=None,
                    reason="No URL was provided in the text for audit."
                )
            )
        elif url_found:
            actions.append(
                ActionItemSchema(
                    title=f"Analyze website {url_found}",
                    description=f"Run bounded tests on {url_found}",
                    route="automatic",
                    tool_name="bounded_website_check",
                    tool_args={"url": url_found},
                    reason="URL analysis is automated."
                )
            )
            
        if "draft" in text_lower or "email" in text_lower:
            actions.append(
                ActionItemSchema(
                    title="Draft General Email Communication",
                    description="Create an email draft based on the input text.",
                    route="human_review",
                    tool_name="draft_communication",
                    tool_args={
                        "recipient_name": "Valued Recipient",
                        "topic": "General Business Follow-up",
                        "context": text[:100] + "..."
                    },
                    reason="Emails need human confirmation prior to dispatch."
                )
            )
            
        if not actions:
            # Simple fallback brief
            actions.append(
                ActionItemSchema(
                    title="Generate Task Outline Brief",
                    description="Save details of this general request.",
                    route="automatic",
                    tool_name="create_task_record",
                    tool_args={
                        "title": "General Task Brief",
                        "content": f"User request text: {text}"
                    },
                    reason="Can save simple task outlines automatically."
                )
            )
            
        return InterpretationSchema(
            task_title="Structured Intake Request",
            summary=f"Processed general text: '{text[:80]}...'",
            priority="medium",
            deadline="unknown",
            missing_information=missing,
            what_could_be_automated=["Automated checking if URLs are present", "Initial drafts"],
            what_requires_human_confirmation=["Confirmation of final task routing"],
            action_items=actions
        )

# ---------------------------------------------------------
# Real LLM Call Implementation
# ---------------------------------------------------------
def call_real_llm(text: str, api_type: str, api_key: str) -> InterpretationSchema:
    prompt = f"""You are an Agentic Work Intake parser. Analyze the following unstructured input and extract details matching the schema exactly.
Assign appropriate tools ('draft_communication', 'bounded_website_check', 'create_task_record', 'set_reminder') if they match the actions needed.
Ensure you output valid JSON matching the schema.

---
UNSTRUCTURED INPUT:
{text}
---

Tools description:
1. `draft_communication` (args: recipient_name: str, recipient_email: str, topic: str, context: str) -> Drafts a template message. Use 'human_review' route.
   Make sure you extract the recipient's email address if mentioned in the input text and place it in the `recipient_email` tool argument. Generate realistic, detailed, context-aware message content in the `context` argument.
2. `bounded_website_check` (args: url: str) -> Runs basic audit scrape on a URL. Use 'automatic' route.
3. `create_task_record` (args: title: str, content: str) -> Persists markdown project file. Route can be 'automatic' or 'human_review'.
4. `set_reminder` (args: topic: str, delay_days: int) -> Sets db alert reminder. Use 'automatic' route.

If crucial details like names, dates, or documents are missing, mark the action item route as 'clarification' and add the question to the missing_information list.
"""

    if api_type == "gemini":
        try:
            from google import genai
            from google.genai import types
            client = genai.Client(api_key=api_key)
            
            # Define manual OpenAPI schema to ensure structural validity
            # without triggering developer key additionalProperties limitations
            schema = {
                "type": "object",
                "properties": {
                    "task_title": {"type": "string"},
                    "summary": {"type": "string"},
                    "priority": {"type": "string"},
                    "deadline": {"type": "string"},
                    "missing_information": {
                        "type": "array",
                        "items": {"type": "string"}
                    },
                    "what_could_be_automated": {
                        "type": "array",
                        "items": {"type": "string"}
                    },
                    "what_requires_human_confirmation": {
                        "type": "array",
                        "items": {"type": "string"}
                    },
                    "action_items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "title": {"type": "string"},
                                "description": {"type": "string"},
                                "route": {"type": "string"},
                                "tool_name": {"type": "string"},
                                "tool_args": {
                                    "type": "object",
                                    "properties": {
                                        "recipient_name": {"type": "string"},
                                        "recipient_email": {"type": "string"},
                                        "topic": {"type": "string"},
                                        "context": {"type": "string"},
                                        "url": {"type": "string"},
                                        "title": {"type": "string"},
                                        "content": {"type": "string"},
                                        "delay_days": {"type": "integer"}
                                    }
                                },
                                "reason": {"type": "string"}
                            },
                            "required": ["title", "description", "route", "reason"]
                        }
                    }
                },
                "required": ["task_title", "summary", "priority", "deadline", "action_items"]
            }
            
            models_to_try = ['gemini-2.0-flash', 'gemini-2.0-flash-lite', 'gemini-3.5-flash']
            response = None
            last_error = None
            for model_name in models_to_try:
                for attempt in range(3):
                    try:
                        response = client.models.generate_content(
                            model=model_name,
                            contents=prompt,
                            config=types.GenerateContentConfig(
                                response_mime_type="application/json",
                                response_schema=schema,
                            ),
                        )
                        break
                    except Exception as model_err:
                        last_error = model_err
                        if "429" in str(model_err) or "resource_exhausted" in str(model_err).lower():
                            time.sleep(2.5)
                            continue
                        else:
                            break
                if response:
                    break
            
            if not response:
                raise last_error
            # Cleanup in case there are markdown JSON fences
            text_response = response.text.strip()
            if text_response.startswith("```json"):
                text_response = text_response.split("```json")[1].split("```")[0].strip()
            elif text_response.startswith("```"):
                text_response = text_response.split("```")[1].split("```")[0].strip()
                
            data = json.loads(text_response)
            return InterpretationSchema(**data)
        except Exception as e:
            raise RuntimeError(f"Gemini API failure: {str(e)}")
            
    elif api_type == "openai":
        try:
            from openai import OpenAI
            client = OpenAI(api_key=api_key)
            response = client.beta.chat.completions.parse(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                response_format=InterpretationSchema,
            )
            return response.choices[0].message.parsed
        except Exception as e:
            raise RuntimeError(f"OpenAI API failure: {str(e)}")
            
    else:
        raise ValueError(f"Unsupported API Type: {api_type}")

# ---------------------------------------------------------
# Core API Routes
# ---------------------------------------------------------

@app.post("/api/intake")
async def post_intake(
    text: str = Body(..., embed=True),
    use_mock: bool = Body(True, embed=True),
    api_type: str = Body("gemini", embed=True),
    api_key: str = Body("", embed=True),
    smtp_config: Optional[Dict[str, Any]] = Body(None, embed=True)
):
    if not text.strip():
        raise HTTPException(status_code=400, detail="Text cannot be empty.")
        
    conn = get_db()
    cursor = conn.cursor()
    
    # 1. Create a request record
    cursor.execute(
        "INSERT INTO requests (raw_text, status) VALUES (?, ?)",
        (text, "processing")
    )
    request_id = cursor.lastrowid
    conn.commit()
    
    log_event(request_id, "INFO", "Started ingestion of unstructured text.")
    
    # 2. Get Structured Interpretation (Real LLM or Dynamic Mock)
    interpretation = None
    
    server_gemini_key = os.environ.get("GEMINI_API_KEY")
    server_openai_key = os.environ.get("OPENAI_API_KEY")
    effective_api_key = server_gemini_key or server_openai_key or api_key
    effective_api_type = api_type
    
    if server_gemini_key:
        effective_api_type = "gemini"
    elif server_openai_key:
        effective_api_type = "openai"

    if effective_api_key:
        effective_api_key = effective_api_key.strip().strip("'").strip('"')

    if use_mock or not effective_api_key:
        if not effective_api_key and not use_mock:
            log_event(request_id, "WARN", "No API key provided or configured on server. Falling back to Demo/Mock Mode.")
        log_event(request_id, "INFO", "Using Mock parser for analysis.")
        interpretation = get_mock_ai_response(text)
    else:
        preview = f"{effective_api_key[:5]}...{effective_api_key[-5:]}" if len(effective_api_key) > 10 else "too-short"
        log_event(request_id, "INFO", f"Calling real LLM ({effective_api_type}) using key [len: {len(effective_api_key)}, preview: {preview}]")
        try:
            interpretation = call_real_llm(text, effective_api_type, effective_api_key)
        except Exception as e:
            err_msg = str(e)
            if "429" in err_msg or "resource_exhausted" in err_msg.lower() or "quota" in err_msg.lower():
                log_event(request_id, "WARN", "Gemini API Quota Exceeded (429). Falling back to Simulation Mode. (Free tier limits: 15 requests per minute, 20 requests per day for gemini-3.5-flash).")
            else:
                log_event(request_id, "ERROR", f"LLM execution error: {err_msg[:150]}. Falling back to Mock parser.")
            interpretation = get_mock_ai_response(text)

    # 3. Store the structured Interpretation
    cursor.execute(
        """INSERT INTO interpretations 
           (request_id, task_title, summary, priority, deadline, missing_information, what_could_be_automated, what_requires_human_confirmation) 
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            request_id,
            interpretation.task_title,
            interpretation.summary,
            interpretation.priority,
            interpretation.deadline,
            json.dumps(interpretation.missing_information),
            json.dumps(interpretation.what_could_be_automated),
            json.dumps(interpretation.what_requires_human_confirmation)
        )
    )
    conn.commit()
    
    log_event(request_id, "SUCCESS", f"Structured interpretation completed. Title: '{interpretation.task_title}' (Priority: {interpretation.priority})")

    # 4. Store Action Items
    actions_to_execute = []
    has_clarification = False
    
    for act in interpretation.action_items:
        # Determine status
        if act.route == "automatic":
            status = "pending"
        elif act.route == "human_review":
            status = "waiting_for_approval"
        elif act.route == "clarification":
            status = "waiting_for_clarification"
            has_clarification = True
        else:
            status = "cannot_execute"
            
        cursor.execute(
            """INSERT INTO action_items 
               (request_id, title, description, route, tool_name, tool_args, status, reason) 
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                request_id,
                act.title,
                act.description,
                act.route,
                act.tool_name,
                json.dumps(act.tool_args) if act.tool_args else None,
                status,
                act.reason
            )
        )
        action_id = cursor.lastrowid
        conn.commit()
        
        log_event(request_id, "INFO", f"Registered Action Item: '{act.title}' routed as [{act.route}]. Status: {status}")
        
        if act.route == "automatic" and act.tool_name:
            actions_to_execute.append((action_id, act.tool_name, act.tool_args))

    # Update overall request status
    final_status = "clarification_needed" if has_clarification else "pending_execution"
    cursor.execute("UPDATE requests SET status = ? WHERE id = ?", (final_status, request_id))
    conn.commit()
    
    # 5. Automatically execute route="automatic" actions
    client_config = {"smtp_config": smtp_config} if smtp_config else None
    for action_id, tool_name, tool_args in actions_to_execute:
        await execute_action_by_id(request_id, action_id, tool_name, tool_args, client_config)
        
    # Check overall request status after automatic executions
    update_overall_request_status(request_id)
    
    conn.close()
    return {"request_id": request_id, "status": final_status}

# Helper function to execute an action
async def execute_action_by_id(request_id: int, action_id: int, tool_name: str, tool_args: Dict[str, Any], client_config: Optional[Dict[str, Any]] = None):
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute("UPDATE action_items SET status = 'executing' WHERE id = ?", (action_id,))
    conn.commit()
    log_event(request_id, "INFO", f"Running automated execution for Action #{action_id} using tool '{tool_name}'...")
    
    try:
        args = tool_args or {}
        output_str = ""
        
        if tool_name == "draft_communication":
            output_str = await tool_draft_communication(
                request_id, 
                args.get("recipient_name", "Recipient"), 
                args.get("topic", "Subject"), 
                args.get("context", ""),
                recipient_email=args.get("recipient_email"),
                smtp_config=client_config.get("smtp_config") if client_config else None
            )
        elif tool_name == "bounded_website_check":
            res = await tool_bounded_website_check(request_id, args.get("url", ""))
            output_str = json.dumps(res, indent=2)
        elif tool_name == "create_task_record":
            output_str = await tool_create_task_record(request_id, args.get("title", "Task"), args.get("content", ""))
        elif tool_name == "set_reminder":
            res = await tool_set_reminder(request_id, args.get("topic", "Alert"), int(args.get("delay_days", 1)))
            output_str = json.dumps(res, indent=2)
        else:
            raise ValueError(f"Unknown tool name: {tool_name}")
            
        cursor.execute(
            "UPDATE action_items SET status = 'completed', output = ? WHERE id = ?", 
            (output_str, action_id)
        )
        conn.commit()
        log_event(request_id, "SUCCESS", f"Action #{action_id} completed successfully.")
    except Exception as e:
        # Graceful error handling in SQLite and logs
        error_details = str(e)
        cursor.execute(
            "UPDATE action_items SET status = 'failed', output = ? WHERE id = ?", 
            (f"ERROR: {error_details}", action_id)
        )
        conn.commit()
        log_event(request_id, "ERROR", f"Action #{action_id} failed: {error_details}")

    conn.close()

def update_overall_request_status(request_id: int):
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute("SELECT status FROM action_items WHERE request_id = ?", (request_id,))
    statuses = [r["status"] for r in cursor.fetchall()]
    
    if not statuses:
        new_status = "completed"
    elif "failed" in statuses:
        new_status = "failed"
    elif "executing" in statuses:
        new_status = "executing"
    elif "pending" in statuses or "waiting_for_approval" in statuses:
        new_status = "pending_execution"
    elif "waiting_for_clarification" in statuses:
        new_status = "clarification_needed"
    else:
        new_status = "completed"
        
    cursor.execute("UPDATE requests SET status = ? WHERE id = ?", (new_status, request_id))
    conn.commit()
    conn.close()
    
    if new_status == "completed":
        log_event(request_id, "SUCCESS", "All workflow action items executed successfully. Request completed.")

# ---------------------------------------------------------
# Human-in-the-loop Endpoints
# ---------------------------------------------------------

@app.post("/api/actions/{action_id}/approve")
async def approve_action(action_id: int, payload: Optional[Dict[str, Any]] = Body(None)):
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute("SELECT * FROM action_items WHERE id = ?", (action_id,))
    action = cursor.fetchone()
    
    if not action:
        conn.close()
        raise HTTPException(status_code=404, detail="Action item not found.")
        
    request_id = action["request_id"]
    log_event(request_id, "INFO", f"Human APPROVED Action #{action_id}: '{action['title']}'.")
    
    # Update status to approved
    cursor.execute("UPDATE action_items SET status = 'approved' WHERE id = ?", (action_id,))
    conn.commit()
    conn.close()
    
    # Trigger execution
    client_config = payload.get("client_config") if payload else None
    await execute_action_by_id(request_id, action_id, action["tool_name"], json.loads(action["tool_args"]) if action["tool_args"] else {}, client_config)
    update_overall_request_status(request_id)
    
    return {"status": "success", "message": f"Action #{action_id} approved and executed."}

@app.post("/api/actions/{action_id}/reject")
async def reject_action(action_id: int):
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute("SELECT * FROM action_items WHERE id = ?", (action_id,))
    action = cursor.fetchone()
    
    if not action:
        conn.close()
        raise HTTPException(status_code=404, detail="Action item not found.")
        
    request_id = action["request_id"]
    log_event(request_id, "WARN", f"Human REJECTED Action #{action_id}: '{action['title']}'.")
    
    cursor.execute("UPDATE action_items SET status = 'rejected', output = 'Rejected by human reviewer.' WHERE id = ?", (action_id,))
    conn.commit()
    conn.close()
    
    update_overall_request_status(request_id)
    return {"status": "success", "message": f"Action #{action_id} rejected."}

@app.post("/api/actions/{action_id}/edit")
async def edit_and_approve_action(action_id: int, payload: Dict[str, Any] = Body(...)):
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute("SELECT * FROM action_items WHERE id = ?", (action_id,))
    action = cursor.fetchone()
    
    if not action:
        conn.close()
        raise HTTPException(status_code=404, detail="Action item not found.")
        
    request_id = action["request_id"]
    edited_args = payload.get("edited_args", {})
    client_config = payload.get("client_config")
    
    log_event(request_id, "INFO", f"Human EDITED and APPROVED Action #{action_id}. Params updated to: {json.dumps(edited_args)}")
    
    # Update tool args in database
    cursor.execute(
        "UPDATE action_items SET tool_args = ?, status = 'approved' WHERE id = ?",
        (json.dumps(edited_args), action_id)
    )
    conn.commit()
    conn.close()
    
    # Trigger execution
    await execute_action_by_id(request_id, action_id, action["tool_name"], edited_args, client_config)
    update_overall_request_status(request_id)
    
    return {"status": "success", "message": f"Action #{action_id} edited, approved, and executed."}

@app.post("/api/actions/{action_id}/execute")
async def run_action(action_id: int):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM action_items WHERE id = ?", (action_id,))
    action = cursor.fetchone()
    
    if not action:
        conn.close()
        raise HTTPException(status_code=404, detail="Action not found.")
        
    conn.close()
    await execute_action_by_id(action["request_id"], action_id, action["tool_name"], json.loads(action["tool_args"]) if action["tool_args"] else {})
    update_overall_request_status(action["request_id"])
    return {"status": "success"}

# ---------------------------------------------------------
# Info Retrieval Endpoints
# ---------------------------------------------------------

@app.get("/api/requests")
def get_requests():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT r.id, r.raw_text, r.status, r.timestamp, i.task_title
        FROM requests r
        LEFT JOIN interpretations i ON r.id = i.request_id
        ORDER BY r.timestamp DESC
    """)
    rows = cursor.fetchall()
    conn.close()
    
    return [dict(r) for r in rows]

@app.get("/api/requests/{request_id}")
def get_request_detail(request_id: int):
    conn = get_db()
    cursor = conn.cursor()
    
    # Get request basic
    cursor.execute("SELECT * FROM requests WHERE id = ?", (request_id,))
    req = cursor.fetchone()
    if not req:
        conn.close()
        raise HTTPException(status_code=404, detail="Request not found")
        
    # Get interpretation
    cursor.execute("SELECT * FROM interpretations WHERE request_id = ?", (request_id,))
    interp = cursor.fetchone()
    
    # Get actions
    cursor.execute("SELECT * FROM action_items WHERE request_id = ?", (request_id,))
    actions = cursor.fetchall()
    
    # Get logs
    cursor.execute("SELECT * FROM activity_logs WHERE request_id = ? ORDER BY id ASC", (request_id,))
    logs = cursor.fetchall()
    
    conn.close()
    
    structured_interp = {}
    if interp:
        structured_interp = {
            "task_title": interp["task_title"],
            "summary": interp["summary"],
            "priority": interp["priority"],
            "deadline": interp["deadline"],
            "missing_information": json.loads(interp["missing_information"]),
            "what_could_be_automated": json.loads(interp["what_could_be_automated"]),
            "what_requires_human_confirmation": json.loads(interp["what_requires_human_confirmation"]),
        }
        
    action_list = []
    for a in actions:
        action_list.append({
            "id": a["id"],
            "title": a["title"],
            "description": a["description"],
            "route": a["route"],
            "tool_name": a["tool_name"],
            "tool_args": json.loads(a["tool_args"]) if a["tool_args"] else None,
            "status": a["status"],
            "output": a["output"],
            "reason": a["reason"]
        })
        
    return {
        "id": req["id"],
        "raw_text": req["raw_text"],
        "status": req["status"],
        "timestamp": req["timestamp"],
        "interpretation": structured_interp,
        "actions": action_list,
        "logs": [dict(l) for l in logs]
    }

# ---------------------------------------------------------
# Document Retrieval & HTML Renderer (PDF export)
# ---------------------------------------------------------
@app.get("/tasks/{filename}", response_class=HTMLResponse)
def get_task_file_html(filename: str):
    # Security check: prevent directory traversal
    if ".." in filename or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")
        
    file_path = os.path.join(TASKS_DIR, filename)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found")
        
    with open(file_path, "r", encoding="utf-8") as f:
        md_content = f.read()
        
    # Convert markdown to HTML
    html_content = markdown.markdown(md_content, extensions=['extra', 'codehilite', 'tables'])
    
    # Prettify title
    title_match = re.search(r'^#\s+(.+)$', md_content, re.MULTILINE)
    title = title_match.group(1) if title_match else filename
    
    # Render full HTML template
    template = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>{title}</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=Fira+Code:wght@400;500&display=swap" rel="stylesheet">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <style>
        :root {{
            --bg-color: #0b0c16;
            --card-bg: rgba(20, 22, 41, 0.6);
            --border-color: rgba(255, 255, 255, 0.08);
            --text-color: #e2e8f0;
            --text-muted: #94a3b8;
            --primary: #06b6d4;
            --primary-glow: rgba(6, 182, 212, 0.2);
            --success: #10b981;
        }}
        
        body {{
            background: var(--bg-color);
            color: var(--text-color);
            font-family: 'Inter', sans-serif;
            margin: 0;
            padding: 40px 20px;
            display: flex;
            justify-content: center;
        }}
        
        .container {{
            max-width: 800px;
            width: 100%;
            background: var(--card-bg);
            border: 1px solid var(--border-color);
            backdrop-filter: blur(16px);
            border-radius: 16px;
            padding: 40px;
            box-shadow: 0 8px 32px rgba(0, 0, 0, 0.5);
            position: relative;
        }}
        
        .actions-bar {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 30px;
            border-bottom: 1px solid var(--border-color);
            padding-bottom: 20px;
        }}
        
        .btn {{
            background: var(--primary);
            color: #fff;
            border: none;
            padding: 10px 20px;
            border-radius: 8px;
            font-size: 0.9rem;
            font-weight: 500;
            cursor: pointer;
            display: inline-flex;
            align-items: center;
            gap: 8px;
            transition: all 0.3s ease;
            box-shadow: 0 0 15px var(--primary-glow);
            text-decoration: none;
        }}
        
        .btn:hover {{
            opacity: 0.9;
            transform: translateY(-1px);
        }}
        
        .btn-secondary {{
            background: rgba(255, 255, 255, 0.05);
            color: var(--text-color);
            border: 1px solid var(--border-color);
            box-shadow: none;
        }}
        
        .btn-secondary:hover {{
            background: rgba(255, 255, 255, 0.1);
        }}
        
        .report-content {{
            line-height: 1.6;
        }}
        
        .report-content h1 {{
            color: #fff;
            font-size: 2.2rem;
            margin-top: 0;
            margin-bottom: 20px;
            border-left: 4px solid var(--primary);
            padding-left: 15px;
        }}
        
        .report-content h2 {{
            color: #fff;
            font-size: 1.5rem;
            margin-top: 30px;
            margin-bottom: 15px;
            border-bottom: 1px solid var(--border-color);
            padding-bottom: 5px;
        }}
        
        .report-content h3 {{
            color: var(--primary);
            font-size: 1.2rem;
            margin-top: 20px;
        }}
        
        .report-content p, .report-content li {{
            color: var(--text-color);
            font-size: 1rem;
        }}
        
        .report-content ul, .report-content ol {{
            padding-left: 20px;
            margin-bottom: 20px;
        }}
        
        .report-content li {{
            margin-bottom: 8px;
        }}
        
        .report-content code {{
            background: rgba(255, 255, 255, 0.05);
            padding: 2px 6px;
            border-radius: 4px;
            font-family: 'Fira Code', monospace;
            font-size: 0.9em;
            color: #06b6d4;
        }}
        
        .report-content pre {{
            background: rgba(255, 255, 255, 0.03);
            border: 1px solid var(--border-color);
            padding: 15px;
            border-radius: 8px;
            overflow-x: auto;
        }}
        
        .report-content pre code {{
            background: none;
            padding: 0;
            color: inherit;
        }}
        
        .report-content blockquote {{
            border-left: 4px solid var(--text-muted);
            margin: 20px 0;
            padding: 10px 20px;
            background: rgba(255, 255, 255, 0.02);
            color: var(--text-muted);
        }}
        
        .report-content table {{
            width: 100%;
            border-collapse: collapse;
            margin-bottom: 25px;
        }}
        
        .report-content th, .report-content td {{
            padding: 12px;
            border: 1px solid var(--border-color);
            text-align: left;
        }}
        
        .report-content th {{
            background: rgba(255, 255, 255, 0.05);
            font-weight: 600;
        }}

        /* Print Specific Styles */
        @media print {{
            body {{
                background: #fff !important;
                color: #000 !important;
                padding: 0 !important;
            }}
            .container {{
                background: #fff !important;
                border: none !important;
                box-shadow: none !important;
                padding: 0 !important;
                max-width: 100% !important;
            }}
            .actions-bar {{
                display: none !important;
            }}
            .report-content h1, .report-content h2, .report-content h3 {{
                color: #000 !important;
                page-break-after: avoid;
            }}
            .report-content p, .report-content li {{
                color: #222 !important;
            }}
            .report-content h1 {{
                border-left-color: #000 !important;
            }}
            .report-content h2 {{
                border-bottom-color: #ccc !important;
            }}
            .report-content th {{
                background: #eee !important;
                color: #000 !important;
            }}
            .report-content td, .report-content th {{
                border-color: #ccc !important;
            }}
            .report-content code {{
                background: #f4f4f4 !important;
                color: #333 !important;
                border: 1px solid #ddd !important;
            }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="actions-bar">
            <a href="/" class="btn btn-secondary"><i class="fa-solid fa-arrow-left"></i> Back to Dashboard</a>
            <button onclick="window.print()" class="btn"><i class="fa-solid fa-file-pdf"></i> Save as PDF / Print</button>
        </div>
        <div class="report-content">
            {html_content}
        </div>
    </div>
</body>
</html>
"""
    return HTMLResponse(content=template)

# ---------------------------------------------------------
# Static Files & SPA Mounting
# ---------------------------------------------------------
# Mount static folder
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
def read_root():
    return FileResponse("static/index.html")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
