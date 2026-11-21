
import os
import json
import uuid
from datetime import datetime
from collections import Counter

from flask import Flask, render_template, request, redirect, session, url_for
from dotenv import load_dotenv
from groq import Groq
from supabase import create_client

# ----------------------------------
# Environment Setup
# ----------------------------------

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY")

app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024

ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}

groq_key = os.getenv("GROQ_API_KEY")
groq_client = Groq(api_key=groq_key) if groq_key else None

supabase_url = os.getenv("SUPABASE_URL")
supabase_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

supabase = None

if supabase_url and supabase_key:
    supabase = create_client(supabase_url, supabase_key)


# ----------------------------------
# Image Upload Helper
# ----------------------------------

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

    content_type = allowed_mime_types[extension]

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
                "content-type": content_type,
                "upsert": "false"
            }
        )

        print("Image uploaded successfully:", image_path)

        return image_path

    except Exception as e:
        print("Image Upload Error:", str(e))
        raise ValueError("Image upload failed. Please try again.")


# ----------------------------------
# Generate Private Image URL
# ----------------------------------

def get_complaint_image_url(image_path):

    if not image_path or supabase is None:
        print("No image path found.")
        return None

    try:
        response = supabase.storage.from_(
            "complaint-images"
        ).create_signed_url(
            image_path,
            3600
        )

        print("Signed URL Response:", response)

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

        print("Image Path:", image_path)
        print(
            "Generated Image URL:",
            "URL CREATED" if signed_url else "URL NOT CREATED"
        )

        return signed_url

    except Exception as e:
        print("Image URL Error:", str(e))
        return None


# ----------------------------------
# Notification System
# ----------------------------------

def create_notification(complaint_id, message, notification_type):

    if supabase is None:
        return False

    try:
        notification_data = {
            "complaint_id": complaint_id,
            "message": message,
            "notification_type": notification_type
        }

        response = (
            supabase.table("notifications")
            .insert(notification_data)
            .execute()
        )

        print("Notification saved:", response.data)

        return True

    except Exception as e:
        print("Notification error:", str(e))
        return False


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
        image_file = request.files.get("image")

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

                # Analyze complaint
                analysis = analyze_complaint(complaint)

                # Upload optional image
                image_path = None

                if image_file and image_file.filename:
                    image_path = upload_complaint_image(image_file)

                # Prepare complaint data
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
                    ),
                    "image_path": image_path
                }

                # Save complaint in Supabase
                supabase.table("complaints").insert(
                    complaint_data
                ).execute()

                # Create submission notification
                create_notification(
                    complaint_data["complaint_id"],
                    "Your complaint has been submitted successfully.",
                    "Complaint Submitted"
                )

                result = {
                    "id": complaint_data["complaint_id"],
                    "complaint": complaint,
                    "category": complaint_data["category"],
                    "department": complaint_data["department"],
                    "priority": complaint_data["priority"],
                    "summary": complaint_data["summary"],
                    "status": complaint_data["status"],
                    "date_time": complaint_data["date_time"],
                    "image_attached": bool(image_path)
                }

            except Exception as e:
                error = f"Complaint processing failed: {str(e)}"

    # Load complaint history
    complaint_history = []

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
        if not error:
            error = f"Database loading failed: {str(e)}"

    return render_template(
        "index.html",
        result=result,
        error=error,
        complaint_history=complaint_history
    )


# ----------------------------------
# Complaint Tracking and Notifications
# ----------------------------------

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
                        .execute()
                    )

                    all_notifications = notification_response.data or []

                    notifications = [
                        n for n in all_notifications
                        if str(n.get("complaint_id", "")).strip().upper()
                        == complaint_id
                    ]

                    notifications.sort(
                        key=lambda n: n.get("created_at") or "",
                        reverse=True
                    )

                    image_path = complaint.get("image_path")

                    print("Tracking Complaint:", complaint_id)
                    print("Image Path from Database:", image_path)

                    image_url = get_complaint_image_url(image_path)

                    print(
                        "Image URL Status:",
                        "Available" if image_url else "Not Available"
                    )

                    print("Notifications Found:", len(notifications))

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
# Admin Dashboard with Analytics and Images
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

    complaints = response.data or []

    # Generate temporary private image URL for each complaint
    for complaint in complaints:

        image_path = complaint.get("image_path")

        if image_path:
            complaint["image_url"] = get_complaint_image_url(
                image_path
            )
        else:
            complaint["image_url"] = None

    # Statistics
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

    # Category Analytics
    category_counts = Counter(
        c.get("category") or "Unknown"
        for c in complaints
    )

    category_labels = list(category_counts.keys())
    category_values = list(category_counts.values())

    # Priority Analytics
    priority_counts = Counter(
        c.get("priority") or "Unknown"
        for c in complaints
    )

    priority_labels = list(priority_counts.keys())
    priority_values = list(priority_counts.values())

    # Department Analytics
    department_counts = Counter(
        c.get("department") or "Unknown"
        for c in complaints
    )

    department_labels = list(department_counts.keys())
    department_values = list(department_counts.values())

    # Monthly Analytics
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
        department_labels=department_labels,
        department_values=department_values,
        monthly_labels=monthly_labels,
        monthly_values=monthly_values
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

    try:
        response = (
            supabase.table("complaints")
            .select("status")
            .eq("complaint_id", complaint_id)
            .execute()
        )

        if not response.data:
            return "Complaint not found", 404

        old_status = response.data[0].get("status")

        supabase.table("complaints").update(
            {"status": new_status}
        ).eq(
            "complaint_id", complaint_id
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


# ----------------------------------
# Delete Complaint
# ----------------------------------

@app.route("/delete_complaint/<complaint_id>", methods=["POST"])
def delete_complaint(complaint_id):

    if not session.get("admin_logged_in"):
        return redirect(url_for("admin_login"))

    if supabase is None:
        return "Supabase connection not available", 500

    try:
        # Find complaint
        response = (
            supabase.table("complaints")
            .select("complaint_id, image_path")
            .eq("complaint_id", complaint_id)
            .execute()
        )

        if not response.data:
            return "Complaint not found", 404

        complaint = response.data[0]
        image_path = complaint.get("image_path")

        # Delete related notifications first
        supabase.table("notifications").delete().eq(
            "complaint_id", complaint_id
        ).execute()

        # Delete attached image
        if image_path:
            supabase.storage.from_(
                "complaint-images"
            ).remove([image_path])

        # Delete complaint
        supabase.table("complaints").delete().eq(
            "complaint_id", complaint_id
        ).execute()

        print("Complaint deleted:", complaint_id)

    except Exception as e:
        print("Complaint deletion error:", str(e))
        return "Unable to delete complaint.", 500

    return redirect(url_for("admin_dashboard"))


# ----------------------------------
# Admin Logout
# ----------------------------------

@app.route("/admin/logout")
def admin_logout():

    session.pop("admin_logged_in", None)

    return redirect(url_for("admin_login"))


# ----------------------------------
# File Too Large Error
# ----------------------------------

@app.errorhandler(413)
def file_too_large(error):
    return "Image is too large. Maximum allowed size is 5 MB.", 413


# ----------------------------------
# Run Flask Application
# ----------------------------------

if __name__ == "__main__":
    app.run(debug=True)
