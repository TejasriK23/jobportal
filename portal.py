import os
from flask import Flask, request, render_template_string
import sqlite3
import requests
import PyPDF2
import io
import json
import subprocess

app = Flask(__name__)

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
RAPIDAPI_KEY = os.environ.get("RAPIDAPI_KEY", "")

def get_all_jobs(search="", company=""):
    conn = sqlite3.connect("jobs.db")
    c = conn.cursor()
    query = "SELECT id, title, company, location, description, apply_link, date_scraped, job_type FROM jobs WHERE 1=1"
    params = []
    if search:
        query += " AND (title LIKE ? OR description LIKE ?)"
        params += [f"%{search}%", f"%{search}%"]
    if company:
        query += " AND company = ?"
        params.append(company)
    query += " ORDER BY date_scraped DESC"
    c.execute(query, params)
    rows = c.fetchall()
    conn.close()
    return [{"id": r[0], "title": r[1], "company": r[2], "location": r[3],
             "description": r[4], "apply_link": r[5], "date": r[6], "type": r[7]} for r in rows]

def get_companies():
    conn = sqlite3.connect("jobs.db")
    c = conn.cursor()
    c.execute("SELECT DISTINCT company FROM jobs ORDER BY company")
    companies = [r[0] for r in c.fetchall()]
    conn.close()
    return companies

def init_tracker_db():
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
    c.execute("""
        CREATE TABLE IF NOT EXISTS applications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_title TEXT, company TEXT, apply_link TEXT,
            status TEXT DEFAULT 'Pending Review',
            date_applied TEXT, notes TEXT
        )
    """)
    conn.commit()
    conn.close()

def save_application(job_title, company, apply_link):
    from datetime import datetime
    conn = sqlite3.connect("jobs.db")
    c = conn.cursor()
    c.execute("""
        INSERT INTO applications (job_title, company, apply_link, status, date_applied)
        VALUES (?, ?, ?, 'Pending Review', ?)
    """, (job_title, company, apply_link, datetime.now().strftime("%Y-%m-%d %H:%M")))
    conn.commit()
    conn.close()

def get_applications():
    conn = sqlite3.connect("jobs.db")
    c = conn.cursor()
    c.execute("SELECT id, job_title, company, apply_link, status, date_applied FROM applications ORDER BY date_applied DESC")
    rows = c.fetchall()
    conn.close()
    return [{"id": r[0], "title": r[1], "company": r[2], "link": r[3],
             "status": r[4], "date": r[5]} for r in rows]

def ask_ai(prompt):
    headers = {"Authorization": "Bearer " + GROQ_API_KEY, "Content-Type": "application/json"}
    body = {"model": "llama-3.3-70b-versatile",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 500}
    r = requests.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, json=body)
    return r.json()["choices"][0]["message"]["content"]

def read_pdf(file_bytes):
    reader = PyPDF2.PdfReader(io.BytesIO(file_bytes))
    return "".join(page.extract_text() or "" for page in reader.pages)

def score_job(resume_text, job):
    prompt = f"""Score how well this resume matches this job from 0-100.
Job: {job['title']} at {job['company']}
Description: {job['description']}
Resume: {resume_text[:800]}
Reply with ONLY a number 0-100."""
    try:
        score = ask_ai(prompt).strip()
        return int(''.join(filter(str.isdigit, score))[:3])
    except:
        return 50

HTML = """<!DOCTYPE html>
<html>
<head>
<title>JobPortal — AI Job Matching</title>
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
.hero p{font-size:18px;opacity:.9;margin-bottom:32px}
.upload-card{background:#fff;border-radius:16px;padding:32px;max-width:500px;margin:0 auto;box-shadow:0 4px 20px rgba(0,0,0,.15)}
.upload-card label{display:block;font-weight:600;margin-bottom:8px;color:#4a5568}
.upload-card input[type=file]{width:100%;padding:12px;border:2px dashed #cbd5e0;border-radius:8px;margin-bottom:16px;cursor:pointer}
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
.tag{background:#ebf4ff;color:#3182ce;padding:4px 10px;border-radius:20px;font-size:12px;font-weight:600}
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
</style>
</head>
<body>
<div class="navbar">
  <h1>JobPortal</h1>
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
  <div class="empty"><p>No applications yet. <a href="/" style="color:#667eea">Find Jobs</a> and click Auto Apply!</p></div>
  {% endif %}
</div>

{% elif not jobs and not profile %}
<div class="hero">
  <h2>Find Your Perfect Job with AI</h2>
  <p>Upload your resume — AI matches and finds Seattle & Remote USA jobs for you</p>
  <div class="upload-card">
    <form method="POST" enctype="multipart/form-data"
          onsubmit="document.getElementById('loading').style.display='block';this.querySelector('button').style.display='none'">
      <label>Upload your resume (PDF)</label>
      <input type="file" name="resume" accept=".pdf" required>
      <button type="submit" class="btn">Analyze Resume & Find Jobs</button>
    </form>
    <div id="loading" style="display:none;color:#667eea;margin-top:16px;font-weight:600">
      Analyzing resume and scoring all jobs... please wait
    </div>
  </div>
</div>

{% else %}
<div class="container">
  {% if profile %}
  <div class="profile-box">
    <h3>Your AI Profile</h3>
    <p><strong>Name:</strong> {{ profile.name }} &nbsp;|&nbsp;
       <strong>Skills:</strong> {{ profile.skills }} &nbsp;|&nbsp;
       <strong>Best roles:</strong> {{ profile.roles }}</p>
  </div>
  {% endif %}

  <div class="stats-bar">
    <div class="stat"><div class="stat-num">{{ jobs|length }}</div><div class="stat-label">Total Jobs</div></div>
    <div class="stat"><div class="stat-num">{{ jobs|selectattr('score','ge',70)|list|length }}</div><div class="stat-label">Strong Matches</div></div>
    <div class="stat"><div class="stat-num">{{ companies|length }}</div><div class="stat-label">Companies</div></div>
    <div class="stat"><div class="stat-num">{{ jobs|selectattr('score','ge',50)|list|length }}</div><div class="stat-label">Good Matches</div></div>
  </div>

  <div class="filters">
    <form method="GET" style="display:flex;gap:12px;flex:1;flex-wrap:wrap">
      <input type="text" name="search" placeholder="Search jobs..." value="{{ search }}">
      <select name="company">
        <option value="">All companies</option>
        {% for c in companies %}<option value="{{ c }}" {{ 'selected' if c==selected_company }}>{{ c }}</option>{% endfor %}
      </select>
      <button type="submit">Search</button>
    </form>
    <a href="/" style="color:#718096;font-size:14px;text-decoration:none;padding:10px">New resume</a>
  </div>

  <div class="jobs-grid">
    {% for job in jobs %}
    {% set cc = 'high-match' if job.score>=70 else ('medium-match' if job.score>=50 else '') %}
    <div class="job-card {{ cc }}">
      <div class="job-header">
        <div class="job-title">{{ job.title }}</div>
        {% if job.score %}
        {% set bc = 'score-high' if job.score>=70 else ('score-medium' if job.score>=50 else 'score-low') %}
        <span class="score-badge {{ bc }}">{{ job.score }}% match</span>
        {% endif %}
      </div>
      <div class="job-company">{{ job.company }}</div>
      <div class="job-meta">{{ job.location }} · {{ job.type }} · Posted {{ job.date }}</div>
      {% if job.description %}<div class="job-desc">{{ job.description[:180] }}{% if job.description|length>180 %}...{% endif %}</div>{% endif %}
      <div class="job-actions">
        <form method="POST" action="/autoapply" style="display:inline">
          <input type="hidden" name="job_link" value="{{ job.apply_link }}">
          <input type="hidden" name="job_title" value="{{ job.title }}">
          <input type="hidden" name="company" value="{{ job.company }}">
          <button type="submit" class="apply-btn">Auto Apply</button>
        </form>
        <a href="{{ job.apply_link }}" target="_blank" class="view-btn">View Job</a>
        <span class="tag">{{ job.company }}</span>
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
    companies = get_companies()
    init_tracker_db()

    if request.method == "POST":
        file = request.files.get("resume")
        if file:
            resume_text = read_pdf(file.read())
            name = ask_ai(f"Full name in this resume? Reply ONLY the name.\n\n{resume_text[:1000]}")
            skills = ask_ai(f"Top 6 skills comma-separated. Reply ONLY skills.\n\n{resume_text[:1000]}")
            roles = ask_ai(f"3 job titles this person should apply for, comma-separated. ONLY titles.\n\n{resume_text[:1000]}")
            profile = {"name": name.strip(), "skills": skills.strip(), "roles": roles.strip()}
            all_jobs = get_all_jobs()
            for job in all_jobs:
                job["score"] = score_job(resume_text, job)
            all_jobs.sort(key=lambda x: x["score"], reverse=True)
            return render_template_string(HTML, jobs=all_jobs, profile=profile,
                companies=companies, search="", selected_company="",
                page="home", applications=[])

    jobs = get_all_jobs(search, selected_company) if (search or selected_company) else []
    for job in jobs:
        job["score"] = 0
    return render_template_string(HTML,
        jobs=jobs if (search or selected_company) else None,
        profile=None, companies=companies, search=search,
        selected_company=selected_company, page="home", applications=[])

@app.route("/autoapply", methods=["POST"])
def autoapply():
    job_link = request.form.get("job_link")
    job_title = request.form.get("job_title")
    company = request.form.get("company")
    save_application(job_title, company, job_link)
    return f"""<!DOCTYPE html>
<html><head><title>Applying...</title>
<style>body{{font-family:'Segoe UI',sans-serif;background:#f0f4f8;text-align:center;padding:80px 20px}}
.card{{background:#fff;border-radius:16px;padding:48px;max-width:500px;margin:0 auto;box-shadow:0 4px 20px rgba(0,0,0,.1)}}
h2{{color:#48bb78;margin-bottom:16px}}p{{color:#718096;line-height:1.6;margin-bottom:8px}}
.btn{{display:inline-block;margin-top:24px;padding:12px 28px;border-radius:8px;text-decoration:none;font-weight:600;color:#fff}}
.b1{{background:#667eea}}.b2{{background:#48bb78;margin-left:12px}}</style></head>
<body><div class="card">
<h2>Application Logged!</h2>
<p><strong>{job_title}</strong> at <strong>{company}</strong></p>
<p>This job has been added to your tracker.</p>
<p>Click View Job to apply directly on the company website.</p>
<a href="{job_link}" target="_blank" class="btn b1">View & Apply</a>
<a href="/tracker" class="btn b2">My Tracker</a>
</div></body></html>"""

@app.route("/tracker")
def tracker():
    init_tracker_db()
    applications = get_applications()
    companies = get_companies()
    return render_template_string(HTML, jobs=None, profile=None,
        companies=companies, search="", selected_company="",
        page="tracker", applications=applications)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    app.run(host="0.0.0.0", port=port, debug=False)