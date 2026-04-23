from flask import Flask, request, jsonify
from flask_cors import CORS
from google.oauth2 import service_account
from googleapiclient.discovery import build
from openai import OpenAI
import os, json, datetime, pytz, qrcode, io, base64, smtplib
from email.mime.text import MIMEText

app = Flask(__name__)
CORS(app)

SCOPES    = ['https://www.googleapis.com/auth/spreadsheets']
TORONTO_TZ = pytz.timezone('America/Toronto')

_sheets_service = None
_openai_client  = None

# ── CLIENT CONFIG ─────────────────────────────────────────────────────────────
# Add a new entry here for each client
CLIENTS = {
    'marcos': {
        'name': "Marco's Italian Kitchen",
        'spreadsheet_id': '1u9uBqjwCGhdTnXajI1da8WZZFmwIUHjYDAN1TTmkKrY',
        'google_review_url': 'https://g.page/r/PLACEHOLDER/review',
        'owner_email': 'everixautomation@gmail.com'
    },
    'enoteca_rossio': {
        'name': 'Enoteca Rossio',
        'spreadsheet_id': '1oN09HIGaxDa776e3hSVrQqYSMjXIzosDj3y5as1iuuw',
        'google_review_url': 'https://www.google.com/maps/place/Enoteca+Rossio/@43.6661417,-79.4524444,17z/data=!4m8!3m7!1s0x882b35346e18b64f:0xc5763de939138646!8m2!3d43.6661417!4d-79.4498695!9m1!1b1!16s%2Fg%2F11n3qf3b2l?entry=ttu',
        'owner_email': 'everixautomation@gmail.com'
    }
}

# ── CACHED SERVICES ───────────────────────────────────────────────────────────
def get_sheets():
    global _sheets_service
    if _sheets_service is None:
        creds_dict = json.loads(os.environ.get('GOOGLE_CREDENTIALS'))
        creds = service_account.Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
        _sheets_service = build('sheets', 'v4', credentials=creds)
    return _sheets_service

def get_openai():
    global _openai_client
    if _openai_client is None:
        _openai_client = OpenAI(api_key=os.environ.get('OPENAI_API_KEY'))
    return _openai_client

# ── EMAIL ALERT ───────────────────────────────────────────────────────────────
def send_alert_email(to, subject, body):
    gmail_user = os.environ.get('GMAIL_USER')
    gmail_pass = os.environ.get('GMAIL_APP_PASSWORD')
    if not gmail_user or not gmail_pass:
        print("Email alert skipped: GMAIL_USER or GMAIL_APP_PASSWORD not set")
        return
    msg = MIMEText(body)
    msg['Subject'] = subject
    msg['From']    = gmail_user
    msg['To']      = to
    with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
        smtp.login(gmail_user, gmail_pass)
        smtp.sendmail(gmail_user, to, msg.as_string())

# ── ROUTES ────────────────────────────────────────────────────────────────────

@app.route('/feedback', methods=['POST'])
def submit_feedback():
    data      = request.get_json(silent=True) or {}
    client_id = data.get('client_id', 'marcos')
    name      = data.get('name', '')
    email     = data.get('email', '')
    rating    = int(data.get('rating', 0))
    feedback  = data.get('feedback', '')
    source    = data.get('source', 'unknown')
    timestamp = datetime.datetime.now(TORONTO_TZ).strftime('%Y-%m-%d %H:%M:%S')

    client   = CLIENTS.get(client_id, CLIENTS['marcos'])
    sheet_id = client['spreadsheet_id']

    try:
        svc = get_sheets()
        svc.spreadsheets().values().append(
            spreadsheetId=sheet_id,
            range='Reviews!A:F',
            valueInputOption='RAW',
            body={'values': [[name, email, timestamp, rating, feedback, source]]}
        ).execute()
    except Exception as e:
        print(f"Sheets error: {e}")

    is_happy = rating >= 4

    if not is_happy:
        try:
            send_alert_email(
                to=client['owner_email'],
                subject=f"⚠️ {rating}★ Review — {client['name']}",
                body=(
                    f"A {rating}-star review was submitted.\n\n"
                    f"Name:     {name or 'Anonymous'}\n"
                    f"Source:   {source}\n"
                    f"Feedback: {feedback or '(no comment left)'}\n\n"
                    f"Timestamp: {timestamp}"
                )
            )
        except Exception as e:
            print(f"Email alert error: {e}")

    return jsonify({
        'status': 'ok',
        'happy': is_happy,
        'review_url': client['google_review_url'] if is_happy else None
    })


@app.route('/generate_email', methods=['POST'])
def generate_email():
    data = request.get_json(silent=True) or {}

    if data.get('password') != os.environ.get('EMAIL_TOOL_PASSWORD', 'Email$123'):
        return jsonify({'status': 'error', 'message': 'Unauthorized'}), 401

    business_name = data.get('business_name', '')
    promotion     = data.get('promotion', '')
    offer         = data.get('offer', '')
    valid_dates   = data.get('valid_dates', '')
    tone          = data.get('tone', 'friendly')
    notes         = data.get('notes', '')

    prompt = f"""Write a promotional email for {business_name}.

Promotion type: {promotion}
Specific offer: {offer}
Valid dates: {valid_dates}
Tone: {tone}
Extra notes: {notes}

Return a JSON object with:
- subject: compelling subject line under 50 characters
- preview: preview text under 90 characters
- body: short plain text email body, exactly 2 paragraphs, under 80 words total. Warm, direct reminder — no fluff. End with a clear one-line call to action.

Do not include placeholders. Write it as if ready to send."""

    try:
        client   = get_openai()
        response = client.chat.completions.create(
            model='gpt-4o',
            messages=[
                {"role": "system", "content": "You are an expert email marketing copywriter. Always return valid JSON only."},
                {"role": "user", "content": prompt}
            ],
            response_format={"type": "json_object"}
        )
        result = json.loads(response.choices[0].message.content)
        return jsonify({'status': 'ok', 'email': result})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/qr/<client_id>', methods=['GET'])
def generate_qr(client_id):
    source   = request.args.get('source', 'table')
    base_url = os.environ.get('FRONTEND_URL', 'https://everixautomation.com')
    url      = f"{base_url}/{client_id}?source={source}"

    img    = qrcode.make(url)
    buffer = io.BytesIO()
    img.save(buffer, format='PNG')
    buffer.seek(0)
    img_b64 = base64.b64encode(buffer.getvalue()).decode()

    return jsonify({'status': 'ok', 'url': url, 'qr_base64': img_b64, 'source': source})


@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'live', 'service': 'Growth Engine Backend'})


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
