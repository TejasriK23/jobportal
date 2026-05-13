import os
import requests
from bs4 import BeautifulSoup
import sqlite3
import time
from datetime import datetime

RAPIDAPI_KEY = os.environ.get("RAPIDAPI_KEY", "")

def init_db():
    conn = sqlite3.connect("jobs.db")
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT, company TEXT, location TEXT,
            description TEXT, apply_link TEXT UNIQUE,
            date_scraped TEXT, job_type TEXT
        )
    """)
    conn.commit()
    conn.close()

def clear_old_jobs():
    conn = sqlite3.connect("jobs.db")
    c = conn.cursor()
    c.execute("DELETE FROM jobs")
    conn.commit()
    conn.close()

def save_job(title, company, location, description, apply_link, job_type="Full-time"):
    conn = sqlite3.connect("jobs.db")
    c = conn.cursor()
    try:
        c.execute("""INSERT OR IGNORE INTO jobs
            (title,company,location,description,apply_link,date_scraped,job_type)
            VALUES(?,?,?,?,?,?,?)""",
            (title,company,location,description,apply_link,
             datetime.now().strftime("%Y-%m-%d"),job_type))
        conn.commit()
        return c.rowcount > 0
    except:
        return False
    finally:
        conn.close()

def search_jsearch(query, location="Seattle, WA", num=5, remote=False):
    if not RAPIDAPI_KEY:
        return []
    headers = {
        "X-RapidAPI-Key": RAPIDAPI_KEY,
        "X-RapidAPI-Host": "jsearch.p.rapidapi.com"
    }
    params = {
        "query": f"{query} in {location}" if not remote else f"{query} remote USA",
        "page": "1",
        "num_pages": "1",
        "date_posted": "month",
        "remote_jobs_only": "true" if remote else "false"
    }
    try:
        r = requests.get("https://jsearch.p.rapidapi.com/search",
                        headers=headers, params=params, timeout=15)
        return r.json().get("data", [])[:num]
    except:
        return []

def process_and_save(jobs):
    added = 0
    for job in jobs:
        title = job.get("job_title","")
        company = job.get("employer_name","")
        is_remote = job.get("job_is_remote", False)
        city = job.get("job_city","") or ""
        state = job.get("job_state","") or ""
        location = "Remote — USA" if is_remote else (f"{city}, {state}" if city else "Seattle, WA")
        description = job.get("job_description","")[:500]
        apply_link = job.get("job_apply_link","")
        job_type = "Remote" if is_remote else "Onsite"
        if title and apply_link:
            if save_job(title, company, location, description, apply_link, job_type):
                added += 1
                print(f"  + {title} | {company} | {location}")
    return added

def run_all_scrapers():
    print("="*50)
    print("  SCRAPING SEATTLE & REMOTE USA JOBS")
    print("="*50)
    init_db()
    clear_old_jobs()
    total = 0

    seattle_roles = [
        "Software Engineer","Cloud Engineer","DevOps Engineer",
        "Cybersecurity Engineer","IAM Engineer","Data Engineer",
        "Backend Engineer","Systems Administrator","Network Engineer",
        "IT Support Engineer","Cloud Security Engineer"
    ]
    print("\n--- Seattle Onsite Jobs ---")
    for role in seattle_roles:
        jobs = search_jsearch(role, "Seattle, WA", num=5)
        total += process_and_save(jobs)
        time.sleep(0.5)

    remote_roles = [
        "IAM Engineer remote","Cloud Security Engineer remote",
        "Identity Access Management remote","DevOps Engineer remote",
        "Software Engineer remote","Cybersecurity Analyst remote",
        "Systems Administrator remote","Cloud Architect remote",
        "IT Security Engineer remote","PowerShell Engineer remote"
    ]
    print("\n--- Remote USA Jobs ---")
    for role in remote_roles:
        jobs = search_jsearch(role, remote=True, num=5)
        total += process_and_save(jobs)
        time.sleep(0.5)

    nearby = ["Bellevue, WA","Redmond, WA","Kirkland, WA","Tacoma, WA"]
    print("\n--- Nearby Seattle Cities ---")
    for city in nearby:
        for role in ["Software Engineer","Cloud Engineer","IAM Engineer"]:
            jobs = search_jsearch(role, city, num=3)
            total += process_and_save(jobs)
            time.sleep(0.5)

    print(f"\nTotal jobs scraped: {total}")

if __name__ == "__main__":
    run_all_scrapers()