import os
import sqlite3
import requests
import PyPDF2
import io
import json
from datetime import datetime, timedelta
from flask import Flask, request, render_template_string
import threading
import time

app = Flask(__name__)

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
RAPIDAPI_KEY = os.environ.get("RAPIDAPI_KEY", "")

def setup_database():
    conn = sqlite3.connect("jobs.db")
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT, company TEXT, location TEXT,
            description TEXT, apply_link TEXT UNIQUE,
            date_scraped TEXT, job_type TEXT,
            last_checked TEXT, is_active INTEGER DEFAULT 1
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS applications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_title TEXT, company TEXT, apply_link TEXT,
            status TEXT DEFAULT 'Pending Review',
            date_applied TEXT, notes TEXT
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS scrape_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_date TEXT,
            jobs_added INTEGER,
            jobs_removed INTEGER,
            status TEXT
        )
    """)
    conn.commit()
    conn.close()

setup_database()

def get_all_jobs(search="", company="", job_type=""):
    conn = sqlite3.connect("jobs.db")
    c = conn.cursor()
    query = """SELECT id, title, company, location, description,
               apply_link, date_scraped, job_type
               FROM jobs WHERE is_active=1"""
    params = []
    if search:
        query += " AND (title LIKE ? OR description LIKE ? OR company LIKE ?)"
        params += [f"%{search}%", f"%{search}%", f"%{search}%"]
    if company:
        query += " AND company=?"
        params.append(company)
    if job_type:
        query += " AND job_type=?"
        params.append(job_type)
    query += " ORDER BY date_scraped DESC"
    c.execute(query, params)
    rows = c.fetchall()
    conn.close()
    return [{"id": r[0], "title": r[1], "company": r[2], "location": r[3],
             "description": r[4], "apply_link": r[5], "date": r[6],
             "type": r[7], "score": 0} for r in rows]

def get_companies():
    conn = sqlite3.connect("jobs.db")
    c = conn.cursor()
    c.execute("SELECT DISTINCT company FROM jobs WHERE is_active=1 ORDER BY company")
    companies = [r[0] for r in c.fetchall()]
    conn.close()
    return companies

def get_job_stats():
    conn = sqlite3.connect("jobs.db")
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM jobs WHERE is_active=1")
    total = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM jobs WHERE is_active=1 AND job_type='Remote'")
    remote = c.fetchone()[0]
    c.execute("SELECT MAX(run_date) FROM scrape_log WHERE status='success'")
    last_run = c.fetchone()[0] or "Never"
    conn.close()
    return {"total": total, "remote": remote, "last_run": last_run}

def save_application(job_title, company, apply_link):
    conn = sqlite3.connect("jobs.db")
    c = conn.cursor()
    c.execute("""INSERT INTO applications
        (job_title,company,apply_link,status,date_applied)
        VALUES(?,?,?,'Pending Review',?)""",
        (job_title, company, apply_link,
         datetime.now().strftime("%Y-%m-%d %H:%M")))
    conn.commit()
    conn.close()

def get_applications():
    conn = sqlite3.connect("jobs.db")
    c = conn.cursor()
    c.execute("""SELECT id,job_title,company,apply_link,status,date_applied
                 FROM applications ORDER BY date_applied DESC""")
    rows = c.fetchall()
    conn.close()
    return [{"id": r[0], "title": r[1], "company": r[2], "link": r[3],
             "status": r[4], "date": r[5]} for r in rows]

def ask_ai(prompt):
    if not GROQ_API_KEY:
        return "AI unavailable"
    headers = {"Authorization": "Bearer " + GROQ_API_KEY,
               "Content-Type": "application/json"}
    body = {"model": "llama-3.3-70b-versatile",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 500}
    try:
        r = requests.post("https://api.groq.com/openai/v1/chat/completions",
                         headers=headers, json=body, timeout=30)
        return r.json()["choices"][0]["message"]["content"]
    except:
        return "AI unavailable"

def read_pdf(file_bytes):
    reader = PyPDF2.PdfReader(io.BytesIO(file_bytes))
    return "".join(page.extract_text() or "" for page in reader.pages)

def score_job(resume_text, job):
    prompt = f"""Score 0-100 how well this resume matches this job.
Job: {job['title']} at {job['company']}
Description: {job['description']}
Resume: {resume_text[:600]}
Reply ONLY a number 0-100."""
    try:
        score = ask_ai(prompt).strip()
        return int(''.join(filter(str.isdigit, score))[:3])
    except:
        return 50

def fetch_jsearch(query, location="Seattle WA", remote=False, num=5):
    if not RAPIDAPI_KEY:
        return []
    headers = {
        "X-RapidAPI-Key": RAPIDAPI_KEY,
        "X-RapidAPI-Host": "jsearch.p.rapidapi.com"
    }
    params = {
        "query": f"{query} remote USA" if remote else f"{query} in {location}",
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

def parse_jsearch_job(job):
    is_remote = job.get("job_is_remote", False)
    city = job.get("job_city", "") or ""
    state = job.get("job_state", "") or ""
    return {
        "title": job.get("job_title", ""),
        "company": job.get("employer_name", ""),
        "location": "Remote — USA" if is_remote else (f"{city}, {state}" if city else "Seattle, WA"),
        "description": job.get("job_description", "")[:500],
        "apply_link": job.get("job_apply_link", ""),
        "job_type": "Remote" if is_remote else "Onsite",
        "date_scraped": datetime.now().strftime("%Y-%m-%d"),
        "last_checked": datetime.now().strftime("%Y-%m-%d"),
        "is_active": 1
    }

def save_job_to_db(job):
    conn = sqlite3.connect("jobs.db")
    c = conn.cursor()
    try:
        c.execute("""INSERT OR IGNORE INTO jobs
            (title,company,location,description,apply_link,
             date_scraped,job_type,last_checked,is_active)
            VALUES(?,?,?,?,?,?,?,?,1)""",
            (job["title"], job["company"], job["location"],
             job["description"], job["apply_link"],
             job["date_scraped"], job["job_type"], job["last_checked"]))
        conn.commit()
        return c.rowcount > 0
    except:
        return False
    finally:
        conn.close()

def check_job_still_active(apply_link):
    try:
        r = requests.head(apply_link, timeout=10,
                         allow_redirects=True,
                         headers={"User-Agent": "Mozilla/5.0"})
        return r.status_code < 400
    except:
        return True  # assume active if check fails

def remove_expired_jobs():
    conn = sqlite3.connect("jobs.db")
    c = conn.cursor()
    # Remove jobs older than 30 days
    cutoff = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    c.execute("UPDATE jobs SET is_active=0 WHERE date_scraped < ?", (cutoff,))
    removed = c.rowcount
    conn.commit()
    conn.close()
    print(f"  Removed {removed} expired jobs (older than 30 days)")
    return removed

def run_daily_scraper():
    print("\n" + "="*50)
    print("  DAILY JOB REFRESH STARTING")
    print(f"  Time: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("="*50)

    seattle_roles = [
        "Software Engineer", "Cloud Engineer", "DevOps Engineer",
        "Cybersecurity Engineer", "IAM Engineer", "Data Engineer",
        "Backend Engineer", "Systems Administrator", "Network Engineer",
        "Cloud Security Engineer", "Active Directory Engineer",
        "IT Security Engineer", "PowerShell Engineer"
    ]

    remote_roles = [
        "IAM Engineer", "Cloud Security Engineer",
        "Identity Access Management", "DevOps Engineer",
        "Software Engineer", "Cybersecurity Analyst",
        "Systems Administrator", "Cloud Architect",
        "Active Directory Engineer", "PowerShell Automation"
    ]

    mnc_seattle = [
        ("Software Engineer", "Seattle, WA"),
        ("Cloud Engineer", "Redmond, WA"),
        ("Security Engineer", "Seattle, WA"),
        ("DevOps Engineer", "Bellevue, WA"),
        ("Data Engineer", "Kirkland, WA"),
    ]

    jobs_added = 0

    # Seattle onsite jobs
    print("\n  Fetching Seattle jobs...")
    for role in seattle_roles:
        jobs = fetch_jsearch(role, "Seattle WA", remote=False, num=3)
        for j in jobs:
            parsed = parse_jsearch_job(j)
            if parsed["title"] and parsed["apply_link"]:
                if save_job_to_db(parsed):
                    jobs_added += 1
        time.sleep(0.3)

    # Remote USA jobs
    print("  Fetching Remote USA jobs...")
    for role in remote_roles:
        jobs = fetch_jsearch(role, remote=True, num=3)
        for j in jobs:
            parsed = parse_jsearch_job(j)
            if parsed["title"] and parsed["apply_link"]:
                if save_job_to_db(parsed):
                    jobs_added += 1
        time.sleep(0.3)

    # Nearby cities
    print("  Fetching nearby city jobs...")
    for role, city in mnc_seattle:
        jobs = fetch_jsearch(role, city, remote=False, num=3)
        for j in jobs:
            parsed = parse_jsearch_job(j)
            if parsed["title"] and parsed["apply_link"]:
                if save_job_to_db(parsed):
                    jobs_added += 1
        time.sleep(0.3)

    # Remove expired jobs
    jobs_removed = remove_expired_jobs()

    # Log the run
    conn = sqlite3.connect("jobs.db")
    c = conn.cursor()
    c.execute("""INSERT INTO scrape_log
        (run_date,jobs_added,jobs_removed,status)
        VALUES(?,?,?,'success')""",
        (datetime.now().strftime("%Y-%m-%d %H:%M"),
         jobs_added, jobs_removed))
    conn.commit()
    conn.close()

    print(f"\n  Done! Added: {jobs_added} | Removed: {jobs_removed}")
    print("="*50)

def should_run_daily_scraper():
    conn = sqlite3.connect("jobs.db")
    c = conn.cursor()
    c.execute("SELECT MAX(run_date) FROM scrape_log WHERE status='success'")
    last_run = c.fetchone()[0]
    conn.close()
    if not last_run:
        return True
    last_run_dt = datetime.strptime(last_run[:16], "%Y-%m-%d %H:%M")
    hours_since = (datetime.now() - last_run_dt).total_seconds() / 3600
    return hours_since >= 24

def background_scheduler():
    # Wait 30 seconds after startup then check
    time.sleep(30)
    while True:
        try:
            if should_run_daily_scraper():
                print("  24 hours passed — running daily job refresh...")
                run_daily_scraper()
            else:
                print("  Scraper already ran today — skipping")
        except Exception as e:
            print(f"  Scheduler error: {e}")
        # Check every hour
        time.sleep(3600)

def seed_sample_jobs():
    conn = sqlite3.connect("jobs.db")
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM jobs WHERE is_active=1")
    count = c.fetchone()[0]
    conn.close()
    if count == 0:
        today = datetime.now().strftime("%Y-%m-%d")
        sample_jobs = [
            ("Software Engineer", "Amazon", "Seattle, WA", "Build and scale distributed systems on AWS. Python, Java, distributed systems experience required.", "https://www.amazon.jobs/en/search?base_query=software+engineer&loc_query=seattle", today, "Onsite"),
            ("Cloud Security Engineer", "Microsoft", "Redmond, WA", "Secure Azure cloud infrastructure. IAM, security policies, cloud architecture experience.", "https://jobs.careers.microsoft.com/global/en/search?q=cloud+security+seattle", today, "Hybrid"),
            ("IAM Engineer", "Accenture", "Seattle, WA", "Identity and Access Management. Active Directory, Okta, CyberArk experience preferred.", "https://www.accenture.com/us-en/careers/jobsearch?jk=iam+seattle", today, "Onsite"),
            ("DevOps Engineer", "Google", "Seattle, WA", "CI/CD pipelines, Kubernetes, Docker, GCP. Automate infrastructure and deployment.", "https://careers.google.com/jobs/results/?q=devops+engineer&location=Seattle", today, "Hybrid"),
            ("Cybersecurity Analyst", "Boeing", "Seattle, WA", "Protect aerospace systems. Security monitoring, incident response, SIEM tools.", "https://jobs.boeing.com/search-jobs/cybersecurity/Seattle", today, "Onsite"),
            ("Backend Engineer", "Expedia", "Seattle, WA", "Build travel platform APIs. Java, Spring Boot, microservices, AWS.", "https://lifeatexpediagroup.com/jobs?keyword=backend+engineer&location=Seattle", today, "Hybrid"),
            ("IAM Specialist", "Wipro", "Remote — USA", "Remote IAM role. SailPoint, Okta, Active Directory. Work from anywhere in USA.", "https://careers.wipro.com/careers-home/jobs?keyword=iam+remote", today, "Remote"),
            ("Cloud Architect", "Infosys", "Remote — USA", "Design cloud solutions on AWS/Azure. Remote position. 5+ years experience.", "https://career.infosys.com/joblist?type=search&searchText=cloud+architect+remote", today, "Remote"),
            ("Systems Administrator", "Zillow", "Seattle, WA", "Manage IT infrastructure. Windows Server, Linux, networking experience.", "https://www.zillow.com/careers/", today, "Hybrid"),
            ("Network Engineer", "T-Mobile", "Bellevue, WA", "Design and maintain network infrastructure. CCNA/CCNP, routing protocols.", "https://careers.t-mobile.com/search-jobs/network+engineer/bellevue", today, "Onsite"),
            ("PowerShell Engineer", "TCS", "Remote — USA", "Automate enterprise IT using PowerShell and Python. Remote work available.", "https://www.tcs.com/careers/tcs-careers-apply-now?role=powershell", today, "Remote"),
            ("IT Security Engineer", "Tableau", "Seattle, WA", "Security operations, vulnerability management, penetration testing.", "https://www.salesforce.com/company/careers/seattle/", today, "Hybrid"),
            ("Active Directory Engineer", "Accenture", "Remote — USA", "Manage AD infrastructure remotely. Group Policy, DNS, LDAP, identity federation.", "https://www.accenture.com/us-en/careers/jobsearch?jk=active+directory+remote", today, "Remote"),
            ("Cloud Engineer AWS", "Amazon", "Seattle, WA", "Deploy and manage AWS infrastructure. EC2, S3, Lambda, CloudFormation, Terraform.", "https://www.amazon.jobs/en/search?base_query=cloud+engineer&loc_query=seattle", today, "Onsite"),
            ("Cybersecurity Engineer", "Microsoft", "Remote — USA", "Remote security engineering. Azure Security Center, Defender, identity protection.", "https://jobs.careers.microsoft.com/global/en/search?q=cybersecurity+remote", today, "Remote"),
        ]
        conn = sqlite3.connect("jobs.db")
        c = conn.cursor()
        for job in sample_jobs:
            try:
                c.execute("""INSERT OR IGNORE INTO jobs
                    (title,company,location,description,apply_link,
                     date_scraped,job_type,last_checked,is_active)
                    VALUES(?,?,?,?,?,?,?,?,1)""",
                    (*job, today, 1))
            except:
                pass
        conn.commit()
        conn.close()
        print(f"  Seeded {len(sample_jobs)} sample jobs")
        # Run initial scrape in background
        threading.Thread(target=run_daily_scraper, daemon=True).start()

seed_sample_jobs()

# Start background scheduler
scheduler_thread = threading.Thread(target=background_scheduler, daemon=True)
scheduler_thread.start()
print("  Daily job scheduler started (runs every 24 hours)")

HTML = """<!DOCTYPE html>
<html>
<head>
<title>JobPortal — AI Job Matching Seattle & Remote</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Segoe UI',sans-serif;background:#f0f4f8;color:#2d3748}
.navbar{background:#1a202c;padding:16px 32px;display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px}
.navbar h1{color:#fff;font-size:22px;font-weight:700}
.navbar-links a{color:#a0aec0;text-decoration:none;font-size:14px;padding:6px 14px;border-radius:6px;margin-left:8px}
.navbar-links a:hover,.navbar-links a.active{background:#667eea;color:#fff}
.hero{background:linear-gradient(135deg,#667eea,#764ba2);padding:60px 32px;text-align:center;color:#fff}
.hero h2{font-size:36px;margin-bottom:12px}
.hero p{font-size:18px;opacity:.9;margin-bottom:8px}
.hero .sub{font-size:14px;opacity:.7;margin-bottom:32px}
.upload-card{background:#fff;border-radius:16px;padding:32px;max-width:500px;margin:0 auto;box-shadow:0 4px 20px rgba(0,0,0,.15)}
.upload-card label{display:block;font-weight:600;margin-bottom:8px;color:#4a5568;font-size:15px}
.file-wrapper{border:2px dashed #cbd5e0;border-radius:8px;padding:20px;text-align:center;margin-bottom:8px;cursor:pointer;transition:all .2s;background:#f7fafc}
.file-wrapper:hover{border-color:#667eea;background:#ebf4ff}
.file-wrapper input[type=file]{display:none}
.file-wrapper .upload-icon{font-size:32px;margin-bottom:8px}
.file-wrapper .upload-text{color:#718096;font-size:14px}
.file-wrapper .upload-btn{display:inline-block;background:#667eea;color:#fff;padding:8px 20px;border-radius:6px;font-size:14px;font-weight:600;margin-top:8px}
.file-name{font-size:14px;font-weight:600;color:#a0aec0;margin-bottom:16px;min-height:22px;text-align:center}
.btn{background:#667eea;color:#fff;border:none;padding:14px 28px;border-radius:8px;font-size:16px;font-weight:600;cursor:pointer;width:100%}
.btn:hover{background:#5a67d8}
.container{max-width:1100px;margin:0 auto;padding:32px 16px}
.filters{background:#fff;border-radius:12px;padding:20px;margin-bottom:24px;display:flex;gap:12px;flex-wrap:wrap;align-items:center;box-shadow:0 2px 8px rgba(0,0,0,.06)}
.filters input,.filters select{padding:10px 14px;border:1px solid #e2e8f0;border-radius:8px;font-size:14px;flex:1;min-width:150px}
.filters button{background:#667eea;color:#fff;border:none;padding:10px 20px;border-radius:8px;cursor:pointer;font-weight:600}
.stats-bar{display:flex;gap:16px;margin-bottom:24px;flex-wrap:wrap}
.stat{background:#fff;border-radius:10px;padding:16px 24px;flex:1;min-width:120px;text-align:center;box-shadow:0 2px 8px rgba(0,0,0,.06)}
.stat-num{font-size:28px;font-weight:700;color:#667eea}
.stat-label{font-size:13px;color:#718096;margin-top:4px}
.jobs-grid{display:grid;gap:16px}
.job-card{background:#fff;border-radius:12px;padding:24px;box-shadow:0 2px 8px rgba(0,0,0,.06);border-left:4px solid #667eea;transition:transform .2s}
.job-card:hover{transform:translateY(-2px);box-shadow:0 4px 16px rgba(0,0,0,.1)}
.job-card.high-match{border-left-color:#48bb78}
.job-card.medium-match{border-left-color:#ed8936}
.job-header{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:8px;flex-wrap:wrap;gap:8px}
.job-title{font-size:18px;font-weight:700;color:#2d3748}
.score-badge{padding:6px 14px;border-radius:20px;font-size:13px;font-weight:700}
.score-high{background:#c6f6d5;color:#276749}
.score-medium{background:#feebc8;color:#744210}
.score-low{background:#e2e8f0;color:#4a5568}
.job-company{color:#667eea;font-weight:600;margin-bottom:4px}
.job-meta{color:#718096;font-size:13px;margin-bottom:12px}
.job-desc{color:#4a5568;font-size:14px;line-height:1.6;margin-bottom:16px}
.job-actions{display:flex;gap:10px;flex-wrap:wrap}
.apply-btn{background:#48bb78;color:#fff;padding:10px 20px;border-radius:8px;font-weight:600;font-size:14px;border:none;cursor:pointer}
.apply-btn:hover{background:#38a169}
.view-btn{background:#667eea;color:#fff;padding:10px 20px;border-radius:8px;text-decoration:none;font-weight:600;font-size:14px}
.tag-remote{background:#e9d8fd;color:#553c9a;padding:4px 10px;border-radius:20px;font-size:12px;font-weight:600}
.tag-onsite{background:#ebf4ff;color:#3182ce;padding:4px 10px;border-radius:20px;font-size:12px;font-weight:600}
.tag-hybrid{background:#fefcbf;color:#744210;padding:4px 10px;border-radius:20px;font-size:12px;font-weight:600}
.profile-box{background:#ebf8ff;border:1px solid #bee3f8;border-radius:12px;padding:20px;margin-bottom:24px}
.profile-box h3{color:#2b6cb0;margin-bottom:8px}
.profile-box p{color:#2d3748;font-size:14px;line-height:1.8}
.tracker-table{width:100%;border-collapse:collapse;background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,.06)}
.tracker-table th{background:#667eea;color:#fff;padding:14px 16px;text-align:left;font-size:14px}
.tracker-table td{padding:14px 16px;border-bottom:1px solid #e2e8f0;font-size:14px}
.tracker-table tr:hover td{background:#f7fafc}
.status-badge{padding:4px 12px;border-radius:20px;font-size:12px;font-weight:700}
.status-applied{background:#c6f6d5;color:#276749}
.status-pending{background:#feebc8;color:#744210}
.page-title{font-size:24px;font-weight:700;margin-bottom:24px;color:#2d3748}
.empty{text-align:center;padding:60px;color:#718096}
.refresh-bar{background:#ebf8ff;border:1px solid #bee3f8;border-radius:8px;padding:10px 16px;margin-bottom:16px;font-size:13px;color:#2b6cb0;display:flex;justify-content:space-between;align-items:center}
</style>
</head>
<body>
<div class="navbar">
  <h1>🚀 JobPortal</h1>
  <div class="navbar-links">
    <a href="/" class="{{ 'active' if page=='home' }}">Find Jobs</a>
    <a href="/tracker" class="{{ 'active' if page=='tracker' }}">My Applications</a>
  </div>
</div>

{% if page == 'tracker' %}
<div class="container">
  <div class="page-title">My Applications ({{ applications|length }} total)</div>
  {% if applications %}
  <table class="tracker-table">
    <thead><tr><th>Job Title</th><th>Company</th><th>Status</th><th>Date Applied</th><th>Link</th></tr></thead>
    <tbody>
    {% for app in applications %}
    <tr>
      <td><strong>{{ app.title }}</strong></td>
      <td>{{ app.company }}</td>
      <td><span class="status-badge {{ 'status-applied' if app.status=='Bot Applied' else 'status-pending' }}">{{ app.status }}</span></td>
      <td>{{ app.date }}</td>
      <td><a href="{{ app.link }}" target="_blank" style="color:#667eea">View</a></td>
    </tr>
    {% endfor %}
    </tbody>
  </table>
  {% else %}
  <div class="empty">
    <p style="font-size:18px;margin-bottom:12px">No applications yet</p>
    <p><a href="/" style="color:#667eea">Upload your resume</a> and click Apply on any job!</p>
  </div>
  {% endif %}
</div>

{% elif not jobs and not profile %}
<div class="hero">
  <h2>Find Your Perfect Job with AI</h2>
  <p>Seattle & Remote USA — Amazon, Microsoft, Google, Boeing and more</p>
  <p class="sub">Jobs refreshed daily · Upload your resume for personalized matches</p>
  <div class="upload-card">
    <form method="POST" enctype="multipart/form-data" id="uploadForm"
          onsubmit="document.getElementById('loading').style.display='block';document.getElementById('submitBtn').style.display='none'">
      <label>Upload your resume (PDF)</label>
      <div class="file-wrapper" onclick="document.getElementById('resumeInput').click()">
        <div class="upload-icon">📄</div>
        <div class="upload-text" id="uploadText">Click to browse or drag your PDF here</div>
        <div class="upload-btn">Choose File</div>
        <input type="file" id="resumeInput" name="resume" accept=".pdf" required
               onchange="
                 var f=this.files[0];
                 if(f){
                   document.getElementById('fname').innerHTML='✅ '+f.name;
                   document.getElementById('fname').style.color='#48bb78';
                   document.getElementById('uploadText').innerText='File selected!';
                   document.querySelector('.file-wrapper').style.borderColor='#48bb78';
                   document.querySelector('.file-wrapper').style.background='#f0fff4';
                 }
               ">
      </div>
      <div class="file-name" id="fname">No file selected</div>
      <button type="submit" class="btn" id="submitBtn">🔍 Analyze Resume & Find Jobs</button>
    </form>
    <div id="loading" style="display:none;color:#667eea;margin-top:16px;font-weight:600;text-align:center">
      ⏳ Analyzing your resume and finding matching jobs...<br>
      <small style="color:#a0aec0">This takes about 30 seconds</small>
    </div>
  </div>
</div>

{% else %}
<div class="container">
  {% if profile %}
  <div class="profile-box">
    <h3>✅ Your AI Profile</h3>
    <p>
      <strong>Name:</strong> {{ profile.name }} &nbsp;|&nbsp;
      <strong>Skills:</strong> {{ profile.skills }} &nbsp;|&nbsp;
      <strong>Best roles:</strong> {{ profile.roles }}
    </p>
  </div>
  {% endif %}

  <div class="refresh-bar">
    <span>🔄 Jobs auto-refresh daily &nbsp;·&nbsp; Last updated: {{ stats.last_run }}</span>
    <span>{{ stats.total }} active jobs · {{ stats.remote }} remote</span>
  </div>

  <div class="stats-bar">
    <div class="stat"><div class="stat-num">{{ jobs|length }}</div><div class="stat-label">Matched Jobs</div></div>
    <div class="stat"><div class="stat-num">{{ jobs|selectattr('score','ge',70)|list|length }}</div><div class="stat-label">Strong Matches</div></div>
    <div class="stat"><div class="stat-num">{{ jobs|selectattr('type','equalto','Remote')|list|length }}</div><div class="stat-label">Remote Jobs</div></div>
    <div class="stat"><div class="stat-num">{{ companies|length }}</div><div class="stat-label">Companies</div></div>
  </div>

  <div class="filters">
    <form method="GET" style="display:flex;gap:12px;flex:1;flex-wrap:wrap">
      <input type="text" name="search" placeholder="Search jobs..." value="{{ search }}">
      <select name="company">
        <option value="">All companies</option>
        {% for c in companies %}<option value="{{ c }}" {{ 'selected' if c==selected_company }}>{{ c }}</option>{% endfor %}
      </select>
      <select name="job_type">
        <option value="">All types</option>
        <option value="Remote" {{ 'selected' if job_type=='Remote' }}>🌐 Remote</option>
        <option value="Onsite" {{ 'selected' if job_type=='Onsite' }}>📍 Onsite</option>
        <option value="Hybrid" {{ 'selected' if job_type=='Hybrid' }}>🔄 Hybrid</option>
      </select>
      <button type="submit">Search</button>
    </form>
    <a href="/" style="color:#718096;font-size:14px;text-decoration:none;padding:10px">New resume</a>
  </div>

  <div class="jobs-grid">
    {% for job in jobs %}
    {% set cc='high-match' if job.score>=70 else ('medium-match' if job.score>=50 else '') %}
    <div class="job-card {{ cc }}">
      <div class="job-header">
        <div class="job-title">{{ job.title }}</div>
        {% if job.score %}
        {% set bc='score-high' if job.score>=70 else ('score-medium' if job.score>=50 else 'score-low') %}
        <span class="score-badge {{ bc }}">{{ job.score }}% match</span>
        {% endif %}
      </div>
      <div class="job-company">{{ job.company }}</div>
      <div class="job-meta">📍 {{ job.location }} &nbsp;·&nbsp; 📅 {{ job.date }}</div>
      {% if job.description %}
      <div class="job-desc">{{ job.description[:200] }}{% if job.description|length>200 %}...{% endif %}</div>
      {% endif %}
      <div class="job-actions">
        <form method="POST" action="/autoapply" style="display:inline">
          <input type="hidden" name="job_link" value="{{ job.apply_link }}">
          <input type="hidden" name="job_title" value="{{ job.title }}">
          <input type="hidden" name="company" value="{{ job.company }}">
          <button type="submit" class="apply-btn">⚡ Apply Now</button>
        </form>
        <a href="{{ job.apply_link }}" target="_blank" class="view-btn">👁 View Job</a>
        {% if job.type=='Remote' %}<span class="tag-remote">🌐 Remote</span>
        {% elif job.type=='Hybrid' %}<span class="tag-hybrid">🔄 Hybrid</span>
        {% else %}<span class="tag-onsite">📍 Onsite</span>{% endif %}
      </div>
    </div>
    {% endfor %}
  </div>
</div>
{% endif %}
</body></html>"""

@app.route("/", methods=["GET", "POST"])
def index():
    search = request.args.get("search", "")
    selected_company = request.args.get("company", "")
    job_type = request.args.get("job_type", "")
    companies = get_companies()
    stats = get_job_stats()

    if request.method == "POST":
        file = request.files.get("resume")
        if file:
            resume_text = read_pdf(file.read())
            name = ask_ai(f"Full name in this resume? Reply ONLY the name.\n\n{resume_text[:1000]}")
            skills = ask_ai(f"Top 6 skills comma-separated. Reply ONLY skills.\n\n{resume_text[:1000]}")
            roles = ask_ai(f"3 job titles this person should apply for, comma-separated. ONLY titles.\n\n{resume_text[:1000]}")
            profile = {"name": name.strip(), "skills": skills.strip(), "roles": roles.strip()}

            # Fetch fresh jobs for this specific resume
            fresh_jobs = []
            role_list = [r.strip() for r in roles.split(",")][:3]
            for role in role_list:
                for j in fetch_jsearch(role, "Seattle WA", remote=False, num=4):
                    parsed = parse_jsearch_job(j)
                    if parsed["title"] and parsed["apply_link"]:
                        save_job_to_db(parsed)
                        parsed["score"] = 0
                        fresh_jobs.append(parsed)
                for j in fetch_jsearch(role, remote=True, num=4):
                    parsed = parse_jsearch_job(j)
                    if parsed["title"] and parsed["apply_link"]:
                        save_job_to_db(parsed)
                        parsed["score"] = 0
                        fresh_jobs.append(parsed)

            # Use fresh jobs if found, else fall back to DB
            all_jobs = fresh_jobs if fresh_jobs else get_all_jobs()

            for job in all_jobs:
                job["score"] = score_job(resume_text, job)
            all_jobs.sort(key=lambda x: x["score"], reverse=True)

            return render_template_string(HTML, jobs=all_jobs, profile=profile,
                companies=get_companies(), search="", selected_company="",
                job_type="", page="home", applications=[], stats=stats)

    if search or selected_company or job_type:
        jobs = get_all_jobs(search, selected_company, job_type)
        return render_template_string(HTML, jobs=jobs, profile=None,
            companies=companies, search=search, selected_company=selected_company,
            job_type=job_type, page="home", applications=[], stats=stats)

    return render_template_string(HTML, jobs=None, profile=None,
        companies=companies, search="", selected_company="",
        job_type="", page="home", applications=[], stats=stats)

@app.route("/autoapply", methods=["POST"])
def autoapply():
    job_link = request.form.get("job_link")
    job_title = request.form.get("job_title")
    company = request.form.get("company")
    save_application(job_title, company, job_link)
    return f"""<!DOCTYPE html>
<html><head><title>Applied!</title>
<style>
body{{font-family:'Segoe UI',sans-serif;background:#f0f4f8;text-align:center;padding:80px 20px}}
.card{{background:#fff;border-radius:16px;padding:48px;max-width:500px;margin:0 auto;box-shadow:0 4px 20px rgba(0,0,0,.1)}}
h2{{color:#48bb78;margin-bottom:16px;font-size:28px}}
p{{color:#718096;line-height:1.6;margin-bottom:8px}}
.btn{{display:inline-block;margin-top:24px;padding:12px 28px;border-radius:8px;text-decoration:none;font-weight:600;color:#fff}}
.b1{{background:#667eea}}.b2{{background:#48bb78;margin-left:12px}}
</style></head>
<body><div class="card">
<h2>✅ Application Logged!</h2>
<p><strong>{job_title}</strong> at <strong>{company}</strong></p>
<p>Added to your application tracker.</p>
<p>Click below to apply directly on the company website.</p>
<a href="{job_link}" target="_blank" class="btn b1">🔗 Apply on Company Site</a>
<a href="/tracker" class="btn b2">📋 My Tracker</a>
</div></body></html>"""

@app.route("/tracker")
def tracker():
    applications = get_applications()
    companies = get_companies()
    stats = get_job_stats()
    return render_template_string(HTML, jobs=None, profile=None,
        companies=companies, search="", selected_company="",
        job_type="", page="tracker", applications=applications, stats=stats)

@app.route("/refresh")
def manual_refresh():
    threading.Thread(target=run_daily_scraper, daemon=True).start()
    return """<html><body style="font-family:sans-serif;text-align:center;padding:60px">
    <h2 style="color:#48bb78">🔄 Job refresh started!</h2>
    <p>New jobs are being fetched in the background.</p>
    <a href="/" style="color:#667eea">Back to portal</a>
    </body></html>"""

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    app.run(host="0.0.0.0", port=port, debug=False)