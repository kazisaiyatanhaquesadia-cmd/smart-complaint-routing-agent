
import os
import json
import uuid
from datetime import datetime, date
from collections import Counter

from flask import (
    Flask, render_template, request, redirect,
    session, url_for
)
from dotenv import load_dotenv
from groq import Groq
from supabase import create_client
from werkzeug.security import (
    check_password_hash,
    generate_password_hash
)
import hmac


# ==========================================
# 1. ENVIRONMENT SETUP
# ==========================================

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY")

app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024

ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}

OFFICES = {
    "RO": "Registrar Office",
    "HO": "Hall",
    "LO": "Library",
    "AO": "Admission",
    "PO": "Proctor",
    "DO": "Department",
    "ASW": "ASW",
    "ACC": "Accounts",
    "EO": "Exam Office",
    "ICT": "ICT Wing"
}

groq_key = os.getenv("GROQ_API_KEY")
groq_client = Groq(api_key=groq_key) if groq_key else None

supabase_url = os.getenv("SUPABASE_URL")
supabase_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

supabase = None

if supabase_url and supabase_key:
    supabase = create_client(supabase_url, supabase_key)


# ==========================================
# 2. PRIORITY CLASSIFICATION
# ==========================================

def determine_priority(complaint, ai_priority="Medium"):

    text = complaint.lower()

    critical_keywords = [
        "fire", "আগুন", "আগুন লাগ",
        "electric shock", "বিদ্যুৎস্পৃষ্ট",
        "sparking", "short circuit",
        "life threatening", "suicide",
        "serious injury", "emergency",
        "বৈদ্যুতিক আগুন", "জীবনের ঝুঁকি"
    ]

    high_keywords = [
        "urgent", "জরুরি",
        "water leakage", "পানি লিক",
        "no electricity", "বিদ্যুৎ নেই",
        "toilet blocked", "severe damage",
        "security threat", "নিরাপত্তা সমস্যা"
    ]

    low_keywords = [
        "suggestion", "পরামর্শ",
        "minor issue", "ছোট সমস্যা",
        "decoration", "সাজসজ্জা"
    ]

    if any(word in text for word in critical_keywords):
        return "Critical"

    if any(word in text for word in high_keywords):
        return "High"

    if any(word in text for word in low_keywords):
        return "Low"

    valid_priorities = ["Low", "Medium", "High", "Critical"]

    if ai_priority in valid_priorities:
        return ai_priority

    return "Medium"


# ==========================================
# 3. AI COMPLAINT ANALYSIS AND OFFICE ROUTING
# ==========================================

def analyze_complaint(complaint):

    if groq_client is None:
        raise ValueError("Groq API key not configured.")

    office_list = "\n".join(
        f"{code}: {name}"
        for code, name in OFFICES.items()
    )

    prompt = f"""
You are a university Smart AI Complaint Routing Agent.

Understand complaints written in:
- Bangla
- English
- Banglish
- Mixed languages

Understand informal spelling and preserve the original meaning.
Do not ask the student to translate.

Available university offices:
{office_list}

Analyze this complaint:
{complaint}

Select exactly ONE most appropriate office from the available list.

Office selection guidance:
- Registrar Office: certificates, official documents,
  student records and registrar services.
- Hall: dormitory rooms, hall facilities, dining,
  water, electricity and hall-related problems.
- Library: books, library access, library facilities
  and library services.
- Admission: admission applications, admission fees
  and admission-related problems.
- Proctor: security, discipline, harassment,
  misconduct and safety complaints.
- Department: classroom, teachers, department facilities,
  academic department-related problems.
- ASW: student welfare and student support services.
- Accounts: tuition fees, payments, receipts,
  scholarships and financial account problems.
- Exam Office: exam schedules, results, registration,
  marksheets and examination issues.
- ICT Wing: internet, Wi-Fi, computer labs,
  software, network and technical problems.

Return ONLY valid JSON:
{{
    "category": "short complaint category",
    "office_code": "one exact code from the available list",
    "priority": "Low, Medium, High, or Critical",
    "summary": "short English summary"
}}

Do not invent an office code.
Choose based on the actual complaint.
"""

    response = groq_client.chat.completions.create(
        model="openai/gpt-oss-120b",
        messages=[
            {"role": "user", "content": prompt}
        ],
        temperature=0.2,
        max_completion_tokens=1024,
        response_format={"type": "json_object"}
    )

    ai_text = response.choices[0].message.content
    analysis = json.loads(ai_text)

    office_code = str(
        analysis.get("office_code", "")
    ).strip().upper()

    if office_code not in OFFICES:
        raise ValueError(
            "AI returned an invalid office. Please try again."
        )

    ai_priority = analysis.get("priority", "Medium")

    final_priority = determine_priority(
        complaint,
        ai_priority
    )

    return {
        "category": analysis.get(
            "category", "General Complaint"
        ),
        "office_code": office_code,
        "department": OFFICES[office_code],
        "priority": final_priority,
        "summary": analysis.get("summary", "")
    }


# ==========================================
# 4. COMPLAINT ID GENERATION
# ==========================================

def generate_complaint_id(office_code):

    if office_code not in OFFICES:
        raise ValueError("Invalid office code.")

    if supabase is None:
        raise ValueError("Supabase connection not available.")

    today = date.today()

    response = supabase.rpc(
        "get_next_complaint_serial",
        {
            "p_office_code": office_code,
            "p_complaint_date": today.isoformat()
        }
    ).execute()

    serial_number = int(response.data)

    date_part = today.strftime("%Y%m%d")

    complaint_id = (
        f"CMP-{office_code}-{date_part}-{serial_number:03d}"
    )

    return complaint_id, serial_number


# ==========================================
# 5. IMAGE UPLOAD HELPER
# ==========================================

def upload_complaint_image(image_file):

    if not image_file or not image_file.filename:
        return None

    filename = image_file.filename

    if "." not in filename:
        raise ValueError("Invalid image file.")

    extension = filename.rsplit(".", 1)[1].lower()

    if extension not in ALLOWED_EXTENSIONS:
        raise ValueError(
            "Only JPG, JPEG, PNG and WEBP images are allowed."
        )

    allowed_mime_types = {
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "png": "image/png",
        "webp": "image/webp"
    }

    image_bytes = image_file.read()

    if not image_bytes:
        raise ValueError("The selected image is empty.")

    if len(image_bytes) > 5 * 1024 * 1024:
        raise ValueError("Image size must be less than 5 MB.")

    valid_image = False

    if extension in ["jpg", "jpeg"]:
        valid_image = image_bytes.startswith(b"\xff\xd8\xff")

    elif extension == "png":
        valid_image = image_bytes.startswith(
            b"\x89PNG\r\n\x1a\n"
        )

    elif extension == "webp":
        valid_image = (
            len(image_bytes) >= 12
            and image_bytes[:4] == b"RIFF"
            and image_bytes[8:12] == b"WEBP"
        )

    if not valid_image:
        raise ValueError("The uploaded file is not a valid image.")

    image_name = f"{uuid.uuid4().hex}.{extension}"
    image_path = f"complaints/{image_name}"

    try:
        supabase.storage.from_(
            "complaint-images"
        ).upload(
            image_path,
            image_bytes,
            file_options={
                "content-type": allowed_mime_types[extension],
                "upsert": "false"
            }
        )

        return image_path

    except Exception as e:
        print("Image Upload Error:", str(e))
        raise ValueError("Image upload failed. Please try again.")


# ==========================================
# 6. PRIVATE IMAGE URL
# ==========================================

def get_complaint_image_url(image_path):

    if not image_path or supabase is None:
        return None

    try:
        response = supabase.storage.from_(
            "complaint-images"
        ).create_signed_url(image_path, 3600)

        signed_url = (
            response.get("signedURL")
            or response.get("signedUrl")
            or response.get("signed_url")
        )

        if signed_url and signed_url.startswith("/"):
            signed_url = (
                supabase_url.rstrip("/")
                + "/storage/v1"
                + signed_url
            )

        return signed_url

    except Exception as e:
        print("Image URL Error:", str(e))
        return None


# ==========================================
# 7. NOTIFICATION SYSTEM
# ==========================================

def create_notification(
    complaint_id,
    message,
    notification_type
):

    if supabase is None:
        return False

    try:
        notification_data = {
            "complaint_id": complaint_id,
            "message": message,
            "notification_type": notification_type
        }

        supabase.table("notifications").insert(
            notification_data
        ).execute()

        return True

    except Exception as e:
        print("Notification error:", str(e))
        return False


# ==========================================
# 8. ADMIN AUTHENTICATION HELPER
# ==========================================

def admin_is_logged_in():

    return (
        session.get("admin_logged_in") is True
        and session.get("admin_office_code") in OFFICES
    )


# ==========================================
# 9. HOME PAGE AND COMPLAINT SUBMISSION
# ==========================================

@app.route("/", methods=["GET", "POST"])
def home():

    result = None
    error = None
    complaint_history = []

    if request.method == "POST":

        student_name = request.form.get(
            "student_name", ""
        ).strip()

        student_id = request.form.get(
            "student_id", ""
        ).strip()

        complaint = request.form.get(
            "complaint", ""
        ).strip()

        image_file = request.files.get("image")

        if not student_name or not student_id or not complaint:
            error = "Please provide your name, student ID and complaint."

        else:
            try:
                if groq_client is None:
                    raise ValueError("Groq API key not found.")

                if supabase is None:
                    raise ValueError("Supabase connection not available.")

                analysis = analyze_complaint(complaint)

                office_code = analysis["office_code"]
                office_name = OFFICES[office_code]

                complaint_id, serial_number = generate_complaint_id(
                    office_code
                )

                image_path = None

                if image_file and image_file.filename:
                    image_path = upload_complaint_image(image_file)

                now = datetime.now()

                complaint_data = {
                    "complaint_id": complaint_id,
                    "student_name": student_name,
                    "student_id": student_id,
                    "original_complaint": complaint,
                    "category": analysis["category"],
                    "department": office_name,
                    "office": office_name,
                    "office_code": office_code,
                    "serial_number": serial_number,
                    "priority": analysis["priority"],
                    "summary": analysis["summary"],
                    "status": "Pending",
                    "date_time": now.strftime(
                        "%d-%m-%Y %I:%M %p"
                    ),
                    "image_path": image_path
                }

                supabase.table("complaints").insert(
                    complaint_data
                ).execute()

                create_notification(
                    complaint_id,
                    "Your complaint has been submitted successfully.",
                    "Complaint Submitted"
                )

                result = {
                    "id": complaint_id,
                    "complaint": complaint,
                    "student_name": student_name,
                    "student_id": student_id,
                    "category": analysis["category"],
                    "department": office_name,
                    "office": office_name,
                    "priority": analysis["priority"],
                    "summary": analysis["summary"],
                    "status": "Pending",
                    "date_time": complaint_data["date_time"],
                    "image_attached": bool(image_path)
                }

            except Exception as e:
                print("Complaint submission error:", str(e))
                error = f"Complaint processing failed: {str(e)}"

    try:
        if supabase:
            response = (
                supabase.table("complaints")
                .select("*")
                .order("id", desc=True)
                .execute()
            )

            complaint_history = response.data or []

    except Exception as e:
        print("Complaint history error:", str(e))

        if not error:
            error = "Unable to load complaint history."

    return render_template(
        "index.html",
        result=result,
        error=error,
        complaint_history=complaint_history,
        offices=OFFICES
    )


# ==========================================
# 10. COMPLAINT TRACKING
# ==========================================

@app.route("/track", methods=["GET", "POST"])
def track_complaint():

    complaint = None
    notifications = []
    image_url = None
    error = None

    if request.method == "POST":

        complaint_id = request.form.get(
            "complaint_id", ""
        ).strip().upper()

        if not complaint_id:
            error = "Please enter your Complaint ID."

        elif supabase is None:
            error = "Database connection not available."

        else:
            try:
                response = (
                    supabase.table("complaints")
                    .select("*")
                    .eq("complaint_id", complaint_id)
                    .execute()
                )

                if response.data:

                    complaint = response.data[0]

                    notification_response = (
                        supabase.table("notifications")
                        .select("*")
                        .eq("complaint_id", complaint_id)
                        .execute()
                    )

                    notifications = notification_response.data or []

                    notifications.sort(
                        key=lambda n: n.get("created_at") or "",
                        reverse=True
                    )

                    image_url = get_complaint_image_url(
                        complaint.get("image_path")
                    )

                else:
                    error = "No complaint found with this ID."

            except Exception as e:
                print("Tracking error:", str(e))
                error = "Unable to track complaint. Please try again."

    return render_template(
        "tracking.html",
        complaint=complaint,
        notifications=notifications,
        image_url=image_url,
        error=error
    )


# ==========================================
# 11. MASTER ADMIN AUTHENTICATION
# ==========================================

def master_admin_is_logged_in():
    return session.get("master_admin_logged_in") is True


@app.route("/admin/manage/login", methods=["GET", "POST"])
def master_admin_login():

    error = None

    if request.method == "POST":

        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        master_username = "admin"
        master_password = "admin123"

        username_valid = (
            bool(master_username)
            and hmac.compare_digest(username, master_username)
        )

        password_valid = (
            bool(master_password)
            and hmac.compare_digest(password, master_password)
        )

        if username_valid and password_valid:
            session.clear()
            session["master_admin_logged_in"] = True
            session["master_admin_username"] = username

            return redirect(url_for("admin_management"))

        error = "Invalid Master Admin username or password."

    return render_template(
        "master_admin_login.html",
        error=error
    )


# ==========================================
# 12. OFFICE ADMIN MANAGEMENT
# ==========================================

@app.route("/admin/manage", methods=["GET", "POST"])
def admin_management():

    if not master_admin_is_logged_in():
        return redirect(url_for("master_admin_login"))

    if supabase is None:
        return "Database connection not available.", 500

    message = None
    error = None

    if request.method == "POST":

        office_code = request.form.get("office_code", "").strip().upper()
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        if office_code not in OFFICES:
            error = "Please select a valid office."

        elif not username:
            error = "Username is required."

        elif len(password) < 8:
            error = "Password must be at least 8 characters."

        elif password != confirm_password:
            error = "Passwords do not match."

        else:
            try:
                existing_office = (
                    supabase.table("office_admins")
                    .select("id")
                    .eq("office_code", office_code)
                    .execute()
                )

                if existing_office.data:
                    error = (
                        f"{OFFICES[office_code]} already has an admin account."
                    )

                else:
                    existing_username = (
                        supabase.table("office_admins")
                        .select("id")
                        .eq("username", username)
                        .execute()
                    )

                    if existing_username.data:
                        error = "This username is already taken."

                    else:
                        admin_data = {
                            "username": username,
                            "password_hash": generate_password_hash(password),
                            "office_code": office_code,
                            "is_active": True
                        }

                        supabase.table("office_admins").insert(
                            admin_data
                        ).execute()

                        message = (
                            f"Admin account created for {OFFICES[office_code]}."
                        )

            except Exception as e:
                print("Master Admin account creation error:", str(e))
                error = "Unable to create account. Please try again."

    admins = []

    try:
        response = (
            supabase.table("office_admins")
            .select("id, username, office_code, is_active, created_at")
            .order("id")
            .execute()
        )

        admins = response.data or []

    except Exception as e:
        print("Admin list error:", str(e))
        error = error or "Unable to load admin accounts."

    return render_template(
        "admin_management.html",
        offices=OFFICES,
        admins=admins,
        message=message,
        error=error
    )


# ==========================================
# 13. MASTER ADMIN LOGOUT
# ==========================================

@app.route("/admin/manage/logout")
def master_admin_logout():

    session.clear()

    return redirect(url_for("home"))


# ==========================================
# 14. OFFICE-WISE ADMIN LOGIN
# ==========================================

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():

    error = None

    if request.method == "POST":

        username = request.form.get(
            "username", ""
        ).strip()

        password = request.form.get(
            "password", ""
        )

        try:
            if supabase is None:
                raise ValueError("Database connection not available.")

            response = (
                supabase.table("office_admins")
                .select("*")
                .eq("username", username)
                .eq("is_active", True)
                .execute()
            )

            if response.data:

                admin = response.data[0]

                password_valid = check_password_hash(
                    admin["password_hash"],
                    password
                )

                office_code = admin["office_code"]

                if password_valid and office_code in OFFICES:

                    session.clear()
                    session["admin_logged_in"] = True
                    session["admin_username"] = username
                    session["admin_office_code"] = office_code
                    session["admin_office"] = OFFICES[office_code]

                    return redirect(url_for("admin_dashboard"))

            error = "Invalid username or password."

        except Exception as e:
            print("Admin login error:", str(e))
            error = "Unable to login. Please try again."

    return render_template("login.html", error=error)


# ==========================================
# 15. OFFICE-WISE ADMIN DASHBOARD
# ==========================================

@app.route("/admin")
def admin_dashboard():

    if not admin_is_logged_in():
        return redirect(url_for("admin_login"))

    if supabase is None:
        return "Supabase connection not available", 500

    office_code = session["admin_office_code"]
    office_name = OFFICES[office_code]

    try:
        response = (
            supabase.table("complaints")
            .select("*")
            .eq("office_code", office_code)
            .order("id", desc=True)
            .execute()
        )

        complaints = response.data or []

        for complaint in complaints:
            image_path = complaint.get("image_path")

            complaint["image_url"] = (
                get_complaint_image_url(image_path)
                if image_path else None
            )

        total_complaints = len(complaints)

        pending_count = sum(
            1 for c in complaints
            if c.get("status") == "Pending"
        )

        progress_count = sum(
            1 for c in complaints
            if c.get("status") == "In Progress"
        )

        resolved_count = sum(
            1 for c in complaints
            if c.get("status") == "Resolved"
        )

        category_counts = Counter(
            c.get("category") or "Unknown"
            for c in complaints
        )

        category_labels = list(category_counts.keys())
        category_values = list(category_counts.values())

        priority_counts = Counter(
            c.get("priority") or "Unknown"
            for c in complaints
        )

        priority_labels = list(priority_counts.keys())
        priority_values = list(priority_counts.values())

        monthly_counts = Counter()

        for complaint in complaints:

            date_value = complaint.get("date_time", "")

            try:
                complaint_date = datetime.strptime(
                    date_value, "%d-%m-%Y %I:%M %p"
                )

                month_key = complaint_date.strftime("%Y-%m")
                monthly_counts[month_key] += 1

            except (ValueError, TypeError):
                continue

        sorted_months = sorted(monthly_counts.keys())

        monthly_labels = [
            datetime.strptime(month, "%Y-%m").strftime("%b %Y")
            for month in sorted_months
        ]

        monthly_values = [
            monthly_counts[month]
            for month in sorted_months
        ]

        return render_template(
            "admin.html",
            complaints=complaints,
            total_complaints=total_complaints,
            pending_count=pending_count,
            progress_count=progress_count,
            resolved_count=resolved_count,
            category_labels=category_labels,
            category_values=category_values,
            priority_labels=priority_labels,
            priority_values=priority_values,
            department_labels=[office_name],
            department_values=[total_complaints],
            monthly_labels=monthly_labels,
            monthly_values=monthly_values,
            office_name=office_name,
            office_code=office_code,
            admin_username=session.get("admin_username")
        )

    except Exception as e:
        print("Dashboard error:", str(e))
        return "Unable to load dashboard.", 500


# ==========================================
# 16. UPDATE COMPLAINT STATUS
# ==========================================

@app.route("/update_status/<complaint_id>", methods=["POST"])
def update_status(complaint_id):

    if not admin_is_logged_in():
        return redirect(url_for("admin_login"))

    if supabase is None:
        return "Supabase connection not available", 500

    new_status = request.form.get("status")

    allowed_statuses = [
        "Pending",
        "In Progress",
        "Resolved"
    ]

    if new_status not in allowed_statuses:
        return "Invalid status", 400

    office_code = session["admin_office_code"]

    try:
        response = (
            supabase.table("complaints")
            .select("status")
            .eq("complaint_id", complaint_id)
            .eq("office_code", office_code)
            .execute()
        )

        if not response.data:
            return "Complaint not found in your office.", 404

        old_status = response.data[0].get("status")

        supabase.table("complaints").update(
            {"status": new_status}
        ).eq(
            "complaint_id", complaint_id
        ).eq(
            "office_code", office_code
        ).execute()

        if old_status != new_status:
            create_notification(
                complaint_id,
                f"Your complaint status has been updated to {new_status}.",
                "Status Update"
            )

    except Exception as e:
        print("Status update error:", str(e))
        return "Unable to update complaint status.", 500

    return redirect(url_for("admin_dashboard"))


# ==========================================
# 17. DELETE COMPLAINT
# ==========================================

@app.route("/delete_complaint/<complaint_id>", methods=["POST"])
def delete_complaint(complaint_id):

    if not admin_is_logged_in():
        return redirect(url_for("admin_login"))

    if supabase is None:
        return "Supabase connection not available", 500

    office_code = session["admin_office_code"]

    try:
        response = (
            supabase.table("complaints")
            .select("complaint_id, image_path")
            .eq("complaint_id", complaint_id)
            .eq("office_code", office_code)
            .execute()
        )

        if not response.data:
            return "Complaint not found in your office.", 404

        complaint = response.data[0]
        image_path = complaint.get("image_path")

        supabase.table("notifications").delete().eq(
            "complaint_id", complaint_id
        ).execute()

        if image_path:
            supabase.storage.from_(
                "complaint-images"
            ).remove([image_path])

        supabase.table("complaints").delete().eq(
            "complaint_id", complaint_id
        ).eq(
            "office_code", office_code
        ).execute()

        print("Complaint deleted:", complaint_id)

    except Exception as e:
        print("Complaint deletion error:", str(e))
        return "Unable to delete complaint.", 500

    return redirect(url_for("admin_dashboard"))


# ==========================================
# 18. OFFICE ADMIN LOGOUT
# ==========================================

@app.route("/admin/logout")
def admin_logout():

    session.clear()

    return redirect(url_for("admin_login"))


# ==========================================
# 19. FILE TOO LARGE ERROR
# ==========================================

@app.errorhandler(413)
def file_too_large(error):

    return (
        "Image is too large. Maximum allowed size is 5 MB.",
        413
    )


# ==========================================
# 20. RUN FLASK APPLICATION
# ==========================================

if __name__ == "__main__":
    app.run(debug=True)
