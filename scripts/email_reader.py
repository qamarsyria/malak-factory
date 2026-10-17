"""قارئ الإيميل — يستخرج كود تحقق يوتيوب أو رابط التأكيد من Gmail."""
import imaplib
import email
import re
import time
import os
from email.header import decode_header

def _log(msg):
    print(f"[email-reader] {msg}", flush=True)

def _decode_subject(subject):
    try:
        parts = decode_header(subject)
        res = ""
        for content, charset in parts:
            if isinstance(content, bytes):
                res += content.decode(charset or "utf-8", errors="ignore")
            else:
                res += content
        return res
    except Exception:
        return subject

def get_youtube_verification(timeout_seconds=120, check_interval=5):
    """
    يبحث عن الكود أو رابط التأكيد.
    يرجع قاموس: {"type": "otp", "value": "123456"} 
    أو {"type": "link", "value": "https://..."} أو None.
    """
    user = os.getenv("GMAIL_USER", "emadhlaweh5@gmail.com")
    password = os.getenv("GMAIL_APP_PASSWORD", "").replace(" ", "")
    
    if not password:
        _log("❌ GMAIL_APP_PASSWORD غير موجود!")
        return None

    _log(f"🔍 نبحث عن رسالة تأكيد أو كود في {user}...")
    start_time = time.time()

    while time.time() - start_time < timeout_seconds:
        try:
            mail = imaplib.IMAP4_SSL("imap.gmail.com")
            mail.login(user, password)
            
            folders = ['INBOX', '"[Gmail]/Spam"', '"[Gmail]/All Mail"']
            
            for folder in folders:
                try:
                    mail.select(folder)
                    # البحث عن رسائل جوجل/يوتيوب في آخر يوم
                    status, messages = mail.search(None, '(FROM "google.com" OR FROM "youtube.com")')
                    
                    if status != "OK" or not messages[0]:
                        continue
                        
                    msg_ids = messages[0].split()
                    for msg_id in reversed(msg_ids[-5:]):
                        status, data = mail.fetch(msg_id, "(RFC822)")
                        if status != "OK":
                            continue
                            
                        raw_email = data[0][1]
                        msg = email.message_from_bytes(raw_email)
                        subject = _decode_subject(msg.get("Subject", ""))
                        
                        body = ""
                        if msg.is_multipart():
                            for part in msg.walk():
                                if part.get_content_type() in ["text/plain", "text/html"]:
                                    try: body += part.get_payload(decode=True).decode(errors="ignore")
                                    except: pass
                        else:
                            try: body = msg.get_payload(decode=True).decode(errors="ignore")
                            except: pass

                        # 1. البحث عن كود (6 أرقام)
                        match = re.search(r'\b(?:G-)?(\d{6})\b', body + " " + subject)
                        if match and "كود" in subject or "code" in subject.lower():
                            code = match.group(1)
                            _log(f"✅ تم العثور على كود: {code}")
                            mail.logout()
                            return {"type": "otp", "value": code}
                            
                        # 2. البحث عن رابط "نعم، أنا" (Security Alert)
                        if "تحذير أمني" in subject or "Security alert" in subject or "إعدادات" in subject:
                            # نبحث عن رابط يحتوي على كلمة 'Yes' أو 'confirm'
                            links = re.findall(r'https://accounts\.google\.com/[^\s"\'<>]+', body)
                            if links:
                                _log(f"✅ تم العثور على رابط تأكيد!")
                                mail.logout()
                                return {"type": "link", "value": links[0].replace("&amp;", "&")}
                                
                except Exception:
                    pass
            mail.logout()
            time.sleep(check_interval)

        except Exception:
            time.sleep(check_interval)

    _log("❌ انتهت المهلة.")
    return None
