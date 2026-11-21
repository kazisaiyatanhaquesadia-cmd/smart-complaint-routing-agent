
import os
import json
import uuid
from datetime import datetime

from flask import Flask, render_template, request, redirect, session, url_for
from dotenv import load_dotenv
from groq import Groq
from supabase import create_client

# Load environment variables
load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY")

# Groq AI connection
groq_key = os.getenv("GROQ_API_KEY")
groq_client = Groq(api_key=groq_key) if groq_key else None

# Supabase database connection
supabase_url = os.getenv("SUPABASE_URL")
supabase_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

supabase = None

if supabase_url and supabase_key:
    supabase = create_client(supabase_url, supabase_key)


# ----------------------------------
# AI Complaint Analysis
# ----------------------------------

def analyze_complaint(complaint):

    prompt = f"""
You are a Smart AI Complaint Routing Agent for a university.

Understand complaints written in:
1. Bangla (বাংলা)
2. English
3. Banglish (Romanized Bangla)
4. Mixed languages.

Understand informal spelling and preserve the original meaning.
Do not ask users to translate.

Analyze this complaint:
{complaint}

Return ONLY valid JSON with these fields:
{{
    "category": "short complaint category",
    "department": "responsible university department",
    "priority": "Low, Medium, High, or Critical",
    "summary": "short English summary"
}}

Choose the department based on the actual complaint.
Do not guess if the meaning is unclear.
"""

    response = groq_client.chat.completions.create(
        model="openai/gpt-oss-120b",
        messages=[
            {"role": "user", "content": prompt}
        ],
        temperature=0.3,
        max_completion_tokens=1024,
        response_format={"type": "json_object"}
    )

    ai_text = response.choices[0].message.content

    return json.loads(ai_text)


# ----------------------------------
# Home Page and Complaint Submission
# ----------------------------------

@app.route("/", methods=["GET", "POST"])
def home():

    result = None
    error = None

    if request.method == "POST":

        complaint = request.form.get("complaint", "").strip()

        if complaint:

            try:
                if groq_client is None:
                    raise ValueError(
                        "Groq API key not found. Check .env file."
                    )

                if supabase is None:
                    raise ValueError(
                        "Supabase credentials not found. Check .env file."
                    )

                # AI complaint analysis
                analysis = analyze_complaint(complaint)

                complaint_data = {
                    "complaint_id": f"CMP-{uuid.uuid4().hex[:8].upper()}",
                    "original_complaint": complaint,
                    "category": analysis.get(
                        "category", "General Complaint"
                    ),
                    "department": analysis.get(
                        "department", "Admin Department"
                    ),
                    "priority": analysis.get("priority", "Medium"),
                    "summary": analysis.get("summary", ""),
                    "status": "Pending",
                    "date_time": datetime.now().strftime(
                        "%d-%m-%Y %I:%M %p"
                    )
                }

                # Save complaint to Supabase
                supabase.table("complaints").insert(
                    complaint_data
                ).execute()

                result = {
                    "id": complaint_data["complaint_id"],
                    "complaint": complaint,
                    "category": complaint_data["category"],
                    "department": complaint_data["department"],
                    "priority": complaint_data["priority"],
                    "summary": complaint_data["summary"],
                    "status": complaint_data["status"],
                    "date_time": complaint_data["date_time"]
                }

            except Exception as e:
                error = f"Complaint processing failed: {str(e)}"

    # Load complaint history from Supabase
    complaint_history = []

    try:
        if supabase:
            response = (
                supabase.table("complaints")
                .select("*")
                .order("id", desc=True)
                .execute()
            )

            complaint_history = response.data

    except Exception as e:
        if not error:
            error = f"Database loading failed: {str(e)}"

    return render_template(
        "index.html",
        result=result,
        error=error,
        complaint_history=complaint_history
    )


# ----------------------------------
# Admin Login
# ----------------------------------

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():

    error = None

    if request.method == "POST":

        username = request.form.get("username")
        password = request.form.get("password")

        if (
            username == os.getenv("ADMIN_USERNAME")
            and password == os.getenv("ADMIN_PASSWORD")
        ):

            session["admin_logged_in"] = True

            return redirect(url_for("admin_dashboard"))

        error = "Invalid username or password!"

    return render_template("login.html", error=error)


# ----------------------------------
# Admin Dashboard with Statistics
# ----------------------------------

@app.route("/admin")
def admin_dashboard():

    if not session.get("admin_logged_in"):
        return redirect(url_for("admin_login"))

    if supabase is None:
        return "Supabase connection not available", 500

    response = (
        supabase.table("complaints")
        .select("*")
        .order("id", desc=True)
        .execute()
    )

    complaints = response.data

    # Calculate total complaints
    total_complaints = len(complaints)

    # Calculate pending complaints
    pending_count = sum(
        1 for c in complaints
        if c["status"] == "Pending"
    )

    # Calculate in-progress complaints
    progress_count = sum(
        1 for c in complaints
        if c["status"] == "In Progress"
    )

    # Calculate resolved complaints
    resolved_count = sum(
        1 for c in complaints
        if c["status"] == "Resolved"
    )

    return render_template(
        "admin.html",
        complaints=complaints,
        total_complaints=total_complaints,
        pending_count=pending_count,
        progress_count=progress_count,
        resolved_count=resolved_count
    )


# ----------------------------------
# Update Complaint Status
# ----------------------------------

@app.route("/update_status/<complaint_id>", methods=["POST"])
def update_status(complaint_id):

    if not session.get("admin_logged_in"):
        return redirect(url_for("admin_login"))

    new_status = request.form.get("status")

    allowed_statuses = [
        "Pending",
        "In Progress",
        "Resolved"
    ]

    if new_status not in allowed_statuses:
        return "Invalid status", 400

    if supabase is None:
        return "Supabase connection not available", 500

    supabase.table("complaints").update(
        {"status": new_status}
    ).eq(
        "complaint_id", complaint_id
    ).execute()

    return redirect(url_for("admin_dashboard"))


# ----------------------------------
# Admin Logout
# ----------------------------------

@app.route("/admin/logout")
def admin_logout():

    session.pop("admin_logged_in", None)

    return redirect(url_for("admin_login"))


# ----------------------------------
# Run Flask Application
# ----------------------------------

if __name__ == "__main__":
    app.run(debug=True)
