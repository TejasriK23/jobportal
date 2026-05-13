import os
import PyPDF2
import requests

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")

def read_pdf(file_path):
    with open(file_path, "rb") as file:
        reader = PyPDF2.PdfReader(file)
        text = ""
        for page in reader.pages:
            text += page.extract_text() or ""
    return text

def analyze_resume(file_path):
    print("Reading resume...")
    resume_text = read_pdf(file_path)
    print("Sending to AI for analysis...")

    headers = {
        "Authorization": "Bearer " + GROQ_API_KEY,
        "Content-Type": "application/json"
    }
    body = {
        "model": "llama-3.3-70b-versatile",
        "messages": [{
            "role": "user",
            "content": f"""Analyze this resume and extract:
1. Full name
2. Email address
3. Phone number
4. Top 5 skills
5. Years of experience
6. Most recent job title
7. What kind of jobs should this person apply for?

Resume:
{resume_text}

Reply in a clear structured format."""
        }],
        "max_tokens": 1024
    }

    response = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers=headers,
        json=body
    )

    if response.status_code == 200:
        return response.json()["choices"][0]["message"]["content"]
    else:
        return f"Error: {response.status_code} - {response.text}"

if __name__ == "__main__":
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else "sample_resume.pdf"
    print(analyze_resume(path))