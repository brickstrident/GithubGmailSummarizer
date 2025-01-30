import base64
import os
import json  # Need to add this
import datetime
import vertexai
from vertexai.generative_models import GenerativeModel
from email.mime.text import MIMEText
from google.oauth2 import service_account
from googleapiclient.discovery import build

# Updated Gmail API scopes
SCOPES = [
    'https://www.googleapis.com/auth/gmail.readonly',
    'https://www.googleapis.com/auth/gmail.modify',
    'https://www.googleapis.com/auth/gmail.send'
]

# Add this template variable before send_summary_email function
summary_template = """
<div style="margin-bottom: 48px;">
    <h2 style="font-size: 24px; margin-bottom: 24px; color: #1e293b; font-weight: 600;">{subject}</h2>
    <ul style="list-style-type: disc; margin: 0 0 20px 0; padding-left: 24px; line-height: 1.8; color: #334155;">
        {summary}
    </ul>
    <a href="{link}" style="color: #3b82f6; text-decoration: none; font-size: 14px;">View Original Email</a>
</div>
"""


def get_gmail_service():
    creds = service_account.Credentials.from_service_account_info(
        json.loads(os.environ['GOOGLE_CREDENTIALS']),
        scopes=SCOPES
    )
    return build('gmail', 'v1', credentials=creds)

def clean_summary_text(text):
    """Clean and filter out unnecessary headers or artifacts."""
    lines = text.split('\n')
    cleaned_lines = []
    for line in lines:
        # Remove lines that are headers or redundant artifacts
        if line.strip().lower().startswith(("key insights", "4-bullet summary", "_note", "##")):
            continue
        if line.strip() == "":  # Skip empty lines
            continue
        cleaned_lines.append(line.strip())
    return '\n'.join(cleaned_lines)

def summarize_email(model, content):
    prompt = (
        "Summarize this content in exactly 4 bullet points. "
        "Start each bullet point immediately with the key information - no headers or labels. "
        "Each bullet must be 15 words or less and focus on the main points. "
        "Format: • [point]\n• [point]\n• [point]\n• [point]"
        f"\n\nContent: {content}"
    )
    try:
        response = model.generate_content(prompt)
        summary = response.text.strip()
        
        # Split into lines and clean thoroughly
        lines = [line.strip() for line in summary.split('\n')]
        # Remove any headers or labels
        lines = [line for line in lines if not any(word in line.lower() for word in ['summary:', 'key', '##', 'information:', 'summary', 'note:'])]
        # Remove bullet points and numbers
        lines = [line.lstrip('•-*123456789. ') for line in lines]
        # Remove empty lines
        lines = [line for line in lines if line.strip()]
        # Take exactly 4 points
        lines = lines[:4]
        
        # Convert to HTML bullets
        return '\n'.join(f"<li>{line}</li>" for line in lines)
    except Exception as e:
        return "<li>Error processing email content</li>"

def create_email_link(message_id):
    """Create Gmail URL for the email."""
    return f"https://mail.google.com/mail/u/0/#inbox/{message_id}"

def send_summary_email(service, summaries, to_email):
    today = datetime.datetime.now().strftime("%m/%d/%Y")

    # Read the HTML template from the file
    with open('email_template.html', 'r') as f:
        email_template = f.read()

    # Format the summaries into the HTML template
    try:
        email_content = email_template.format(
            today=today,
            summaries='\n'.join(
                summary_template.format(
                    subject=summary.get('subject', 'No Subject'),
                    summary=summary.get('summary', 'No Summary'),
                    link=summary.get('link', '#')
                )
                for summary in summaries
            )
        )
    except KeyError as e:
        raise ValueError(f"Missing required placeholder in email template: {e}")

    # Create the email message and send it
    message = MIMEText(email_content, 'html', 'utf-8')
    message['to'] = to_email
    message['subject'] = f"Your Newsletter Summaries - {today}"

    raw = base64.urlsafe_b64encode(message.as_bytes()).decode('utf-8')
    service.users().messages().send(userId='me', body={'raw': raw}).execute()

def archive_email(service, message_id):
    """Move email to archive and mark as read."""
    service.users().messages().modify(
        userId='me',
        id=message_id,
        body={'removeLabelIds': ['INBOX', 'UNREAD']}
    ).execute()

#@functions_framework.http
#def process_emails(request):
#    main()
#    return 'Emails processed successfully'

def main():
    # Initialize Gmail API
    service = get_gmail_service()
    
    # Initialize Vertex AI and Gemini
    project_id = os.getenv('PROJECT_ID')
    vertexai.init(project=project_id, location='us-central1')
    model = GenerativeModel("gemini-pro")
    
    # Get unread emails
    results = service.users().messages().list(userId='me', maxResults=2).execute()
    print("Gmail API Response:", results)

    messages = results.get('messages', [])  # Extract messages safely
    summaries = []
    for message in messages:
        msg = service.users().messages().get(userId='me', id=message['id']).execute()
        
        subject = next((header['value'] for header in msg['payload']['headers'] if header['name'] == 'Subject'), 'No Subject')
        
        payload = msg['payload']
        email_body = ''
        if 'parts' in payload:
            for part in payload['parts']:
                if part.get('mimeType') == 'text/plain' and 'data' in part['body']:
                    email_body = base64.urlsafe_b64decode(part['body']['data']).decode('utf-8')
                    break
        elif 'data' in payload['body']:
            email_body = base64.urlsafe_b64decode(payload['body']['data']).decode('utf-8')

        summary = summarize_email(model, email_body)
        email_link = create_email_link(message['id'])
        
        summaries.append({
            'subject': subject,
            'summary': summary,
            'link': email_link
        })
        
        archive_email(service, message['id'])
    
    if summaries:
        send_summary_email(service, summaries, os.getenv('EMAIL_TO'))
        print(f"Processed and summarized {len(summaries)} emails")
    else:
        print("No unread messages found")

