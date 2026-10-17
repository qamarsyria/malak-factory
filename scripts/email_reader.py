"""قارئ الإيميل — يستخرج رابط تأكيد يوتيوب من Gmail."""
import imaplib
import email
import re
import time
import os
from email.header import decode_header
from bs4 import BeautifulSoup

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

def get_youtube_verification(timeout_seconds=180, check_interval=5):
    """
    يبحث عن الكود أو رابط التأكيد. ينتظر حتى 3 دقائق.
    """
    user = os.getenv("GMAIL_USER", "emadhlaweh5@gmail.com")
    password = os.getenv("GMAIL_APP_PASSWORD", "").replace(" ", "")
    
    if not password:
        _log("❌ GMAIL_APP_PASSWORD غير موجود!")
        return None

    _log(f"🔍 نبحث عن رسالة تأكيد في {user} (ننتظر حتى {timeout_seconds}ث)...")
    start_time = time.time()

    while time.time() - start_time < timeout_seconds:
        try:
            mail = imaplib.IMAP4_SSL("imap.gmail.com")
            mail.login(user, password)
            
            folders = ['INBOX', '"[Gmail]/Spam"']
            
            for folder in folders:
                try:
                    mail.select(folder)
                    # نبحث عن رسائل جوجل (بدون شرط UNSEEN عشان نلاقيها حتى لو فتحتها على جوالك)
                    status, messages = mail.search(None, '(FROM "google.com")')
                    
                    if status != "OK" or not messages[0]:
                        continue
                        
                    msg_ids = messages[0].split()
                    # نفحص آخر 5 رسائل فقط
                    for msg_id in reversed(msg_ids[-5:]):
                        status, data = mail.fetch(msg_id, "(RFC822)")
                        if status != "OK":
                            continue
                            
                        raw_email = data[0][1]
                        msg = email.message_from_bytes(raw_email)
                        subject = _decode_subject(msg.get("Subject", ""))
                        
                        # نتحقق إذا كانت الرسالة لها علاقة بيوتيوب
                        if "YouTube" not in subject and "يوتيوب" not in subject:
                            continue
                            
                        _log(f"📩 فحص رسالة: {subject}")
                        
                        body = ""
                        if msg.is_multipart():
                            for part in msg.walk():
                                if part.get_content_type() == "text/html":
                                    try: body += part.get_payload(decode=True).decode(errors="ignore")
                                    except: pass
                        else:
                            try: body = msg.get_payload(decode=True).decode(errors="ignore")
                            except: pass

                        # 1. البحث عن كود OTP
                        match = re.search(r'\b(?:G-)?(\d{6})\b', body)
                        if match:
                            code = match.group(1)
                            _log(f"✅ كود OTP: {code}")
                            mail.logout()
                            return {"type": "otp", "value": code}
                            
                        # 2. البحث عن زر "نعم، أنا" (Yes, it's me)
                        soup = BeautifulSoup(body, 'html.parser')
                        for a in soup.find_all('a'):
                            text = a.get_text().strip().lower()
                            if "نعم، أنا" in text or "yes, it's me" in text or "yes, it was me" in text:
                                link = a.get('href')
                                if link and link.startswith('https://accounts.google.com'):
                                    _log(f"✅ تم العثور على رابط زر 'نعم أنا'!")
                                    mail.logout()
                                    return {"type": "link", "value": link.replace("&amp;", "&")}
                                    
                except Exception as e:
                    _log(f"⚠️ خطأ في مجلد {folder}: {e}")
            mail.logout()
            _log(f"⏳ لم تصل رسالة مطابقة بعد... انتظار {check_interval} ثواني.")
            time.sleep(check_interval)

        except Exception as e:
            _log(f"⚠️ خطأ اتصال: {e}")
            time.sleep(check_interval)

    _log("❌ انتهت المهلة.")
    return None
