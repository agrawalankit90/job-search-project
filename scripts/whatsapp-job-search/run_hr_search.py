#!/usr/bin/env python3
"""
Automated HR Contact Search Script
Triggered by GitHub Actions when WhatsApp "run hr search" is received.
Finds hiring managers / recruiters for all P1+P2 unapplied jobs,
updates the HR_repository sheet in recommended_jobs.xlsx, pushes to GitHub.
"""

import os
import json
import re
import subprocess
from datetime import datetime
import anthropic
from tavily import TavilyClient
from openpyxl import load_workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from twilio.rest import Client as TwilioClient


# ── Config ─────────────────────────────────────────────────────────────────────
REPO_ROOT    = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
XLSX_PATH    = os.path.join(REPO_ROOT, "job-search", "recommended_jobs.xlsx")
TWILIO_FROM  = "whatsapp:+14155238886"


# ── Helpers ────────────────────────────────────────────────────────────────────
def get_unapplied_p1_p2(xlsx_path):
    """Return list of dicts for P1/P2 jobs with no 'applied' status."""
    wb  = load_workbook(xlsx_path)
    ws  = wb["recommended_jobs"]
    jobs = []
    headers = [cell.value for cell in ws[1]]
    # Column indices (0-based)
    idx = {h: i for i, h in enumerate(headers) if h}

    for row in ws.iter_rows(min_row=2, values_only=True):
        priority = str(row[idx.get("Priority", 1)] or "").strip()
        status_idx = idx.get("Status", 13)
        status   = str(row[status_idx] if status_idx < len(row) else "").strip().lower()
        if priority in ("P1", "P2") and status != "applied":
            jobs.append({
                "company":   str(row[idx.get("Company Name", 2)] or "").strip(),
                "title":     str(row[idx.get("Job Title",    3)] or "").strip(),
                "location":  str(row[idx.get("Location",     4)] or "").strip(),
                "priority":  priority,
            })
    return jobs

def get_existing_hr_contacts(xlsx_path):
    """Return set of 'Company|Title' already in HR_repository."""
    try:
        wb = load_workbook(xlsx_path)
        if "HR_repository" not in wb.sheetnames:
            return set()
        ws = wb["HR_repository"]
        existing = set()
        for row in ws.iter_rows(min_row=2, values_only=True):
            if row[0] and row[1]:
                existing.add(f"{str(row[0]).strip()}|{str(row[1]).strip()}")
        return existing
    except Exception:
        return set()

def search_contacts(tavily, company, title):
    """Search for recruiter / hiring manager for a specific company+role."""
    queries = [
        f'"{company}" recruiter "talent acquisition" product manager LinkedIn',
        f'"{company}" "hiring manager" product manager site:linkedin.com',
        f'"{company}" email format "@{company.lower().replace(" ", "")}.com" employee',
    ]
    results = []
    for q in queries:
        try:
            res = tavily.search(query=q, search_depth="basic", max_results=3)
            for r in res.get("results", []):
                results.append({
                    "title":   r.get("title", ""),
                    "url":     r.get("url", ""),
                    "snippet": r.get("content", "")[:500],
                })
        except Exception as e:
            print(f"Search error [{q}]: {e}")
    return results

def upsert_hr_row(wb, row_data):
    """Insert or update a row in HR_repository sheet."""
    if "HR_repository" not in wb.sheetnames:
        _create_hr_sheet(wb)
    ws = wb["HR_repository"]

    # Check if company|title combo already exists (col A = title, col B = company)
    key = f"{row_data[0]}|{row_data[1]}"
    for ri, row in enumerate(ws.iter_rows(min_row=2), start=2):
        existing_key = f"{str(row[0].value or '').strip()}|{str(row[1].value or '').strip()}"
        if existing_key == key:
            # Update existing row
            for ci, val in enumerate(row_data, 1):
                ws.cell(row=ri, column=ci, value=val)
            _style_hr_row(ws, ri, row_data[9])
            return

    # Append new row
    ws.append(row_data)
    _style_hr_row(ws, ws.max_row, row_data[9])

def _create_hr_sheet(wb):
    ws = wb.create_sheet("HR_repository")
    headers   = ["Job Title","Company","Priority","Location","Contact Name",
                 "Contact Role","Email Address","LinkedIn URL","Source","Confidence Level","Notes"]
    col_widths= [35,16,8,22,22,32,32,50,28,16,45]
    hfill  = PatternFill("solid", start_color="1F4E79")
    hfont  = Font(name="Arial", bold=True, color="FFFFFF", size=10)
    thin   = Side(style="thin", color="AAAAAA")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    from openpyxl.utils import get_column_letter
    for ci, (h, w) in enumerate(zip(headers, col_widths), 1):
        cell = ws.cell(row=1, column=ci, value=h)
        cell.font      = hfont
        cell.fill      = hfill
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border    = border
        ws.column_dimensions[get_column_letter(ci)].width = w
    ws.row_dimensions[1].height = 30
    ws.freeze_panes = "A2"

def _style_hr_row(ws, row_num, confidence):
    thin   = Side(style="thin", color="AAAAAA")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    nfont  = Font(name="Arial", size=9)
    conf_fills = {
        "High":   PatternFill("solid", start_color="C6EFCE"),
        "Medium": PatternFill("solid", start_color="FFEB9C"),
        "Low":    PatternFill("solid", start_color="FFC7CE"),
    }
    for ci in range(1, 12):
        cell = ws.cell(row=row_num, column=ci)
        cell.font      = nfont
        cell.alignment = Alignment(vertical="top", wrap_text=True)
        cell.border    = border
        if ci == 10:
            cell.fill = conf_fills.get(str(confidence), PatternFill())
    ws.row_dimensions[row_num].height = 60

def git_commit_push(message):
    subprocess.run(["git", "config", "user.email", "ankit.sam.agrawal@gmail.com"], cwd=REPO_ROOT, check=True)
    subprocess.run(["git", "config", "user.name",  "Ankit Agrawal"],              cwd=REPO_ROOT, check=True)
    subprocess.run(["git", "add", "job-search/recommended_jobs.xlsx"],            cwd=REPO_ROOT, check=True)
    diff = subprocess.run(["git", "diff", "--staged", "--quiet"],                  cwd=REPO_ROOT)
    if diff.returncode != 0:
        subprocess.run(["git", "commit", "-m", message[:72]],                     cwd=REPO_ROOT, check=True)
        subprocess.run(["git", "push",   "origin", "main"],                       cwd=REPO_ROOT, check=True)
        print("Pushed to GitHub.")
    else:
        print("No changes to commit.")

def send_whatsapp(to_number, body):
    client = TwilioClient(os.environ["TWILIO_ACCOUNT_SID"], os.environ["TWILIO_AUTH_TOKEN"])
    client.messages.create(from_=TWILIO_FROM, to=f"whatsapp:{to_number}", body=body)
    print(f"WhatsApp sent to {to_number}.")


# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    to_number = os.environ.get("MY_WHATSAPP_NUMBER", "")

    print("Reading P1/P2 unapplied jobs from xlsx...")
    jobs     = get_unapplied_p1_p2(XLSX_PATH)
    existing = get_existing_hr_contacts(XLSX_PATH)
    print(f"Found {len(jobs)} target roles.")

    tavily = TavilyClient(api_key=os.environ["TAVILY_API_KEY"])
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    wb     = load_workbook(XLSX_PATH)

    updated = 0
    for job in jobs:
        key = f"{job['title']}|{job['company']}"
        print(f"Searching contacts for: {job['company']} — {job['title']}")
        search_results = search_contacts(tavily, job["company"], job["title"])

        prompt = f"""Find the recruiter or hiring manager for this job role.

Role: {job['title']} at {job['company']} ({job['location']})

Search results:
{json.dumps(search_results, indent=2)}

Return ONLY valid JSON — no markdown, no explanation:
{{
  "contact_name": "Full Name",
  "contact_role": "Their exact title (e.g. Lead Talent Acquisition, Head of Product)",
  "email": "their.email@company.com",
  "linkedin_url": "https://linkedin.com/in/...",
  "source": "LinkedIn / ZoomInfo / RocketReach / email pattern",
  "confidence": "High | Medium | Low",
  "notes": "One sentence on outreach approach or email confidence basis"
}}

Confidence guide:
- High: email confirmed by 2+ sources (ZoomInfo + ContactOut)
- Medium: email pattern confirmed at 85%+ for domain, at least 1 masked source
- Low: email constructed from domain pattern only — LinkedIn InMail preferred

If no contact found: {{"contact_name": "Not found", "contact_role": "", "email": "", "linkedin_url": "", "source": "Search", "confidence": "Low", "notes": "Apply directly via company careers page."}}"""

        response = client.messages.create(
            model="claude-haiku-4-5-20251001",   # Haiku is faster + cheaper for structured extraction
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}]
        )
        result_text = response.content[0].text.strip()
        json_match  = re.search(r"\{.*\}", result_text, re.DOTALL)
        if not json_match:
            print(f"  ⚠ Could not parse response for {job['company']}")
            continue

        contact = json.loads(json_match.group())
        row_data = [
            job["title"],
            job["company"],
            job["priority"],
            job["location"],
            contact.get("contact_name", ""),
            contact.get("contact_role", ""),
            contact.get("email", ""),
            contact.get("linkedin_url", ""),
            contact.get("source", ""),
            contact.get("confidence", "Low"),
            contact.get("notes", ""),
        ]
        upsert_hr_row(wb, row_data)
        updated += 1
        print(f"  ✓ {contact.get('contact_name', 'N/A')} — {contact.get('confidence', 'Low')} confidence")

    wb.save(XLSX_PATH)
    git_commit_push(f"Auto HR search: updated contacts for {updated} roles")

    summary = f"Found/updated contacts for {updated} of {len(jobs)} roles."
    if to_number:
        msg = (
            f"📋 *HR Contact Search Complete!*\n\n"
            f"{summary}\n\n"
            f"📊 View HR_repository: https://github.com/agrawalankit90/job-search-project/blob/main/job-search/recommended_jobs.xlsx"
        )
        send_whatsapp(to_number, msg)

    print(f"HR search complete. {summary}")


if __name__ == "__main__":
    main()
