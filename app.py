
import os
import socket


from uuid import uuid4
from flask import jsonify
from werkzeug.utils import secure_filename
from flask import Flask, render_template, request, flash, redirect, url_for, session

from supabase_client import supabase  # your Supabase service key client
from flask_bcrypt import Bcrypt



app = Flask(__name__)  # ✅ fixed typo
app.secret_key = "super-secret-key"  # ⚠ Change in production


ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif"}
MAX_IMAGES = 5
STORAGE_BUCKET = "user_uploads"
bcrypt = Bcrypt(app)


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def generate_issue_token():
    """Generate a unique token for issue sharing"""
    return str(uuid4())[:8].upper()


# ---------------- Home ----------------
@app.route("/")
def home():
    return render_template("index.html")

# ---------------- Register ----------------
@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        # -------- Auth fields --------
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "").strip()

        # -------- Profile fields --------
        full_name = request.form.get("full_name", "").strip()
        phone_number = request.form.get("phone_number", "").strip()
        state = request.form.get("state", "").strip()

        districts_id = request.form.get("districts_id", "").strip()
        constituencies_id = request.form.get("constituencies_id", "").strip()
        place_id = request.form.get("place_id", "").strip()

        try:
            # 🔒 Hash password
            password_hash = bcrypt.generate_password_hash(password).decode("utf-8")

            # 1️⃣ Insert user (✅ NO verification token needed)
            user = supabase.table("users").insert({
                "email": email,
                "password_hash": password_hash,
                "is_verified": True  # ✅ Auto-verified (no email check needed)
            }).execute().data[0]

            # 2️⃣ Insert profile (linked to users.id)
            supabase.table("user_profiles").insert({
                "user_id": user["id"],
                "full_name": full_name,
                "phone_number": phone_number,
                "state": state,
                "districts_id": districts_id,
                "constituencies_id": constituencies_id,
                "place_id": place_id
            }).execute()

            flash("✅ Registration successful! You can now login.")
            return redirect(url_for("login"))

        except Exception as e:
            flash(f"❌ Registration error: {e}")

    # -------- GET: load dropdowns --------
    districts = supabase.table("districts").select("id, name").order("name").execute().data
    constituencies = supabase.table("constituencies").select("id, name, districts_id").order("name").execute().data
    places = supabase.table("places").select("id, name, districts_id, constituencies_id").order("name").execute().data

    return render_template(
        "register.html",
        districts=districts,
        constituencies=constituencies,
        places=places
    )


# ---------------- Login ----------------
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "").strip()

        user = supabase.table("users").select("*").eq("email", email).execute().data
        if not user:
            flash("❌ Invalid credentials")
            return render_template("login.html")

        user = user[0]

        # ✅ NO email verification check needed
        if not bcrypt.check_password_hash(user["password_hash"], password):
            flash("❌ Invalid credentials")
            return render_template("login.html")

        session["user_id"] = user["id"]
        return redirect(url_for("dashboard"))

    return render_template("login.html")


# ---------------- Dashboard ----------------
@app.route("/dashboard")
def dashboard():
    user_id = session.get("user_id")
    if not user_id:
        flash("⚠ Please log in first")
        return redirect(url_for("login"))

    return render_template("dashboard.html")

@app.route("/logout")
def logout():
    session.pop("user_id", None)
    flash("✅ You have been logged out.")
    return redirect(url_for("login"))

def detect_priority(description, department_name=""):
    text = (description + " " + department_name).lower()

    emergency_keywords = [
        "fire", "blast", "explosion", "electric shock",
        "gas leak", "accident", "collapse", "flood",
        "short circuit", "live wire"
    ]

    high_keywords = [
        "water leak", "sewage overflow", "road accident",
        "power outage", "transformer", "pipeline burst",
        "tree fallen", "street light not working"
    ]

    medium_keywords = [
        "pothole", "garbage", "drain", "signal not working",
        "noise", "traffic", "street dog", "mosquito"
    ]

    for word in emergency_keywords:
        if word in text:
            return "Emergency"

    for word in high_keywords:
        if word in text:
            return "High"

    for word in medium_keywords:
        if word in text:
            return "Medium"

    return "Low"

# ---------------- Submit a New Issue ----------------
MAX_BYTES_PER_IMAGE = 5 * 1024 * 1024  # 5 MB

@app.route("/new_issue", methods=["GET", "POST"])
def new_issue():
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"status": "error", "message": "Not authenticated"}), 401

    # -------- GET --------
    if request.method == "GET":
        districts = supabase.table("districts").select("id, name").order("name").execute().data
        constituencies = supabase.table("constituencies").select("id, name, districts_id").order("name").execute().data
        departments = supabase.table("departments").select("id, name, district_id").order("name").execute().data
        return render_template(
            "new_issue.html",
            districts=districts,
            constituencies=constituencies,
            departments=departments
        )

    # -------- POST --------
    district_id = request.form.get("district_id")
    constituency_id = request.form.get("constituency_id")
    department_id = request.form.get("department_id")
    places_id = request.form.get("places_id")
    address = request.form.get("address", "").strip()
    description = request.form.get("description", "").strip()
    latitude = request.form.get("latitude", "").strip()
    longitude = request.form.get("longitude", "").strip()

    if not (description and latitude and longitude):
        return jsonify({"status": "error", "message": "Missing required fields"}), 400

    try:
        lat_val = float(latitude)
        lng_val = float(longitude)
    except ValueError:
        return jsonify({"status": "error", "message": "Invalid latitude/longitude"}), 400

    if not (-90 <= lat_val <= 90 and -180 <= lng_val <= 180):
        return jsonify({"status": "error", "message": "Latitude/longitude out of range"}), 400

    # -------- FETCH DEPARTMENT NAME (for priority) --------
    dept_name = ""
    if department_id:
        dept = supabase.table("departments").select("name").eq("id", department_id).execute().data
        if dept:
            dept_name = dept[0]["name"]

    # -------- DETECT PRIORITY --------
    priority = detect_priority(description, dept_name)

    # -------- IMAGE UPLOAD --------
    media_urls = []

    try:
        for i in range(1, 6):
            file = request.files.get(f"image{i}")
            if not file:
                continue

            filename_orig = secure_filename(file.filename or "")
            if not filename_orig:
                continue

            if not allowed_file(filename_orig):
                continue

            content = file.read()
            if not content:
                continue
            if len(content) > MAX_BYTES_PER_IMAGE:
                if media_urls:
                    supabase.storage.from_(STORAGE_BUCKET).remove(media_urls)
                return jsonify({"status": "error", "message": "File too large"}), 400

            unique_name = f"{uuid4()}_{filename_orig}"
            upload_res = supabase.storage.from_(STORAGE_BUCKET).upload(unique_name, content)

            if getattr(upload_res, "error", None):
                if media_urls:
                    supabase.storage.from_(STORAGE_BUCKET).remove(media_urls)
                return jsonify({"status": "error", "message": "Upload failed"}), 500

            media_urls.append(unique_name)

        # -------- INSERT ISSUE --------
        # ✅ Updated to match Supabase schema
        record = {
            "user_id": user_id,
            "districts_id": district_id,
            "constituencies_id": constituency_id,
            "department_id": department_id,
            "places_id": places_id,
            "address": address,
            "description": description,
            "latitude": lat_val,
            "longitude": lng_val,
            "media_urls": media_urls,
            "status": "Pending",
            "priority": priority,
            "issue_token": generate_issue_token(),
            "upvote_count": 0,
            "updates": None
        }

        resp = supabase.table("problems").insert(record).execute()

        if getattr(resp, "error", None):
            if media_urls:
                supabase.storage.from_(STORAGE_BUCKET).remove(media_urls)
            return jsonify({"status": "error", "message": "DB insert failed"}), 500

        return jsonify({
            "status": "success",
            "message": "Issue created",
            "priority": priority
        }), 201

    except Exception as e:
        if media_urls:
            supabase.storage.from_(STORAGE_BUCKET).remove(media_urls)
        return jsonify({"status": "error", "message": "Server error", "detail": str(e)}), 500

# Get departments by district_id
@app.route("/departments_by_district/<district_id>")
def departments_by_district(district_id):
    try:
        departments = supabase.table("departments") \
            .select("id, name") \
            .eq("district_id", district_id) \
            .order("name") \
            .execute().data
        return jsonify(departments)
    except Exception as e:
        return jsonify([])

@app.route("/districts")
def get_districts():
    try:
        districts = supabase.table("districts").select("id, name").order("name").execute().data
        return jsonify(districts)
    except Exception as e:
        print("Error fetching districts:", e)
        return jsonify([])

@app.route("/constituencies/<district_id>")
def get_constituencies(district_id):
    try:
        data = supabase.table("constituencies") \
            .select("id, name") \
            .eq("districts_id", district_id) \
            .order("name") \
            .execute().data
        return jsonify(data)
    except Exception as e:
        print("Error fetching constituencies:", e)
        return jsonify([])

@app.route("/places/<district_id>/<constituency_id>")
def get_places(district_id, constituency_id):
    try:
        data = supabase.table("places") \
            .select("id, name") \
            .eq("districts_id", district_id) \
            .eq("constituencies_id", constituency_id) \
            .order("name") \
            .execute().data
        return jsonify(data)
    except Exception as e:
        print("Error fetching places:", e)
        return jsonify([])


# ---------------- Submitted Issues ----------------
PUBLIC_BUCKET_URL = "https://rcrbazstbgqfmhzubmrg.supabase.co/storage/v1/object/public/user_uploads/"

@app.route("/submitted_issues")
def submitted_issues():
    user_id = session.get("user_id")
    if not user_id:
        flash("⚠ Please log in first")
        return redirect(url_for("login"))

    try:
        issues = (
            supabase.table("problems")
            .select("*, districts(name), constituencies(name), departments(name), places(name)")
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .execute()
            ).data

        # 🔥 FIX: Build full URLs for media_urls
        for issue in issues:
            final_urls = []
            if issue.get("media_urls"):
                for filename in issue["media_urls"]:
                    full_url = PUBLIC_BUCKET_URL + filename
                    final_urls.append(full_url)

            issue["media_urls"] = final_urls
            # ✅ SAFETY: ensure priority always exists
            if not issue.get("priority"):
                issue["priority"] = "Medium"

        return render_template("submitted_issues.html", issues=issues)

    except Exception as e:
        print("Error fetching submitted issues:", e)
        return render_template("submitted_issues.html", issues=[])

@app.route("/tracking")
def tracking():
    user_id = session.get("user_id")
    if not user_id:
        flash("⚠ Please log in first")
        return redirect(url_for("login"))

    try:
        # ✅ Updated to match Supabase schema
        # Fetch all issues for the user
        issues = supabase.table("problems") \
            .select("""
                id,
                created_at,
                status,
                priority,
                upvote_count,
                updates,
                issue_token,
                departments(name),
                places(name)
            """) \
            .eq("user_id", user_id) \
            .order("created_at", desc=True) \
            .execute() \
            .data

        # Status counts
        pending_count = sum(1 for i in issues if i["status"] == "Pending")
        progress_count = sum(1 for i in issues if i["status"] == "In Progress")
        resolved_count = sum(1 for i in issues if i["status"] == "Resolved")

        return render_template(
            "tracking.html",
            issues=issues,
            pending_count=pending_count,
            progress_count=progress_count,
            resolved_count=resolved_count
        )

    except Exception as e:
        print("Error fetching issues for tracking:", e)
        return render_template(
            "tracking.html",
            issues=[],
            pending_count=0,
            progress_count=0,
            resolved_count=0
        )


# ---------------- Run App ----------------
if __name__ == "__main__":
    print(f"Local: http://127.0.0.1:5000\nNetwork: http://{socket.gethostbyname(socket.gethostname())}:5000")
    app.run("0.0.0.0", 5000, debug=True)