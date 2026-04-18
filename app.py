import os
import tempfile
import uuid
from flask import Flask, render_template, request, send_file, redirect, url_for, flash
from annotator import generate_annotated_pdf

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-secret-key")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
RESULT_DIR = os.path.join(BASE_DIR, "results")
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(RESULT_DIR, exist_ok=True)


@app.route("/", methods=["GET"])
def index():
    return render_template("index.html")


@app.route("/process", methods=["POST"])
def process():
    pdf_file = request.files.get("pdf_file")
    transcript = request.form.get("transcript", "").strip()

    if not pdf_file or not pdf_file.filename:
        flash("PDF 파일을 업로드해 주세요.")
        return redirect(url_for("index"))
    if not transcript:
        flash("전사문 텍스트를 붙여넣어 주세요.")
        return redirect(url_for("index"))

    job_id = uuid.uuid4().hex[:10]
    safe_name = pdf_file.filename
    pdf_path = os.path.join(UPLOAD_DIR, f"{job_id}_{safe_name}")
    output_dir = os.path.join(RESULT_DIR, job_id)
    os.makedirs(output_dir, exist_ok=True)
    pdf_file.save(pdf_path)

    try:
        output_pdf, precheck, finalcheck = generate_annotated_pdf(pdf_path, transcript, output_dir)
    except Exception as e:
        flash(f"생성 중 오류가 발생했어요: {e}")
        return redirect(url_for("index"))

    return render_template(
        "result.html",
        download_url=url_for("download_file", job_id=job_id, filename=os.path.basename(output_pdf)),
        precheck_url=url_for("download_file", job_id=job_id, filename=os.path.basename(precheck)),
        finalcheck_url=url_for("download_file", job_id=job_id, filename=os.path.basename(finalcheck)),
    )


@app.route("/download/<job_id>/<path:filename>")
def download_file(job_id: str, filename: str):
    file_path = os.path.join(RESULT_DIR, job_id, filename)
    return send_file(file_path, as_attachment=True)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)), debug=True)
