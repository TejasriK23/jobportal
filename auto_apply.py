import os
import sys
import time
import json
import requests
from playwright.sync_api import sync_playwright

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")

CANDIDATE = {
    "first_name": "Tejasri",
    "last_name": "Kalipatnapu",
    "email": "tejasri7752@gmail.com",
    "phone": "4252214399",
    "resume_path": "C:\\Projects\\JobApplyBOT\\sample_resume.pdf",
    "years_experience": "2",
    "cover_letter": """I am excited to apply for this position. With 2+ years of experience
in Identity and Access Management, Active Directory, AWS cloud security,
and PowerShell scripting, I am confident I can contribute effectively to your team."""
}

def fill_field(page, selectors, value):
    for selector in selectors:
        try:
            el = page.locator(selector).first
            if el.is_visible(timeout=2000):
                el.fill(value)
                return True
        except:
            pass
    return False

def click_button(page, texts):
    for text in texts:
        try:
            btn = page.get_by_role("button", name=text)
            if btn.is_visible(timeout=3000):
                btn.click()
                return True
        except:
            pass
    for text in texts:
        try:
            el = page.locator(f"text={text}").first
            if el.is_visible(timeout=2000):
                el.click()
                return True
        except:
            pass
    return False

def pre_login_all_sites(page):
    print("\n" + "="*55)
    print("  STEP 1 — LOG IN TO JOB SITES")
    print("="*55)
    print("""
  The browser is now open.
  Please log in to these sites one by one:

  1. Amazon Jobs  -> https://www.amazon.jobs
  2. TCS          -> https://www.tcs.com/careers
  3. Infosys      -> https://career.infosys.com
  4. Wipro        -> https://careers.wipro.com
  5. Accenture    -> https://www.accenture.com/careers
    """)
    print("="*55)
    input("  Press ENTER when logged into all sites...")
    print("  Bot taking over now!\n")

def apply_to_job(page, job_url, job_title, company):
    print(f"\n  Applying: {job_title} at {company}")
    try:
        page.goto(job_url, timeout=30000)
        time.sleep(3)
        click_button(page, ["Apply Now","Apply","Apply for this job",
                            "Easy Apply","Quick Apply","Apply Offsite"])
        time.sleep(3)
        if len(page.context.pages) > 1:
            page = page.context.pages[-1]
            time.sleep(2)

        filled = []
        for step in range(8):
            body_text = page.inner_text("body").lower()
            if any(w in body_text for w in ["application submitted","you applied",
                   "successfully applied","thank you for applying"]):
                print("  Successfully submitted!")
                return "submitted"

            if fill_field(page, ['input[name*="first"]','input[id*="first"]',
                                 'input[placeholder*="First"]'], CANDIDATE["first_name"]):
                filled.append("first name")
            if fill_field(page, ['input[name*="last"]','input[id*="last"]',
                                 'input[placeholder*="Last"]'], CANDIDATE["last_name"]):
                filled.append("last name")
            fill_field(page, ['input[name="name"]','input[placeholder*="Full name"]',
                              'input[name="applicant.name"]'],
                      CANDIDATE["first_name"]+" "+CANDIDATE["last_name"])
            if fill_field(page, ['input[type="email"]','input[name*="email"]'],
                         CANDIDATE["email"]):
                filled.append("email")
            if fill_field(page, ['input[type="tel"]','input[name*="phone"]',
                                 'input[aria-label*="Phone"]'], CANDIDATE["phone"]):
                filled.append("phone")
            fill_field(page, ['input[name*="location"]','input[placeholder*="City"]'],
                      "Seattle, WA")
            fill_field(page, ['input[name*="experience"]','input[aria-label*="years"]'],
                      CANDIDATE["years_experience"])
            if fill_field(page, ['textarea[name*="cover"]','textarea[id*="cover"]',
                                 'textarea[name*="message"]'], CANDIDATE["cover_letter"]):
                filled.append("cover letter")
            try:
                upload = page.locator('input[type="file"]').first
                if upload.is_visible(timeout=1500):
                    upload.set_input_files(CANDIDATE["resume_path"])
                    filled.append("resume")
            except:
                pass
            try:
                for btn in page.locator('button:has-text("No")').all()[:5]:
                    btn.click()
                    time.sleep(0.3)
            except:
                pass
            if not click_button(page, ["Continue","Next","Submit your application","Submit"]):
                break
            time.sleep(2)

        if filled:
            print(f"  Filled: {', '.join(filled)}")
        else:
            print("  Could not auto-fill — please apply manually")
        input("  Press ENTER when done (submitted? y/n): ")
        return "submitted"

    except Exception as e:
        print(f"  Error: {e}")
        input("  Press ENTER to continue...")
        return "error"

def run_auto_apply(jobs):
    print("\n" + "="*55)
    print("  AUTO-APPLY BOT")
    print("="*55)
    results = {"submitted": 0, "error": 0}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=500)
        context = browser.new_context()
        page = context.new_page()
        page.goto("https://www.google.com")
        pre_login_all_sites(page)
        for i, job in enumerate(jobs):
            print(f"\n  Job {i+1} of {len(jobs)}")
            result = apply_to_job(page, job["apply_link"], job["title"], job["company"])
            results[result] = results.get(result, 0) + 1
        print("\n  DONE!", results)
        input("  Press ENTER to close browser...")
        browser.close()

if __name__ == "__main__":
    if "--from-portal" in sys.argv:
        try:
            with open("pending_apply.json") as f:
                jobs = json.load(f)
        except:
            jobs = []
    else:
        jobs = [{"title": "IAM Engineer", "company": "Accenture",
                 "apply_link": "https://www.accenture.com/in-en/careers"}]
    if jobs:
        run_auto_apply(jobs)