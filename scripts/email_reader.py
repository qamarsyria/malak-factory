"""قارئ الإيميل — يستخرج كود تحقق يوتيوب من Gmail."""
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

def get_youtube_otp(timeout_seconds=120, check_interval=5):
    """
    يبحث عن كود التحقق في الإيميل.
    ينتظر حتى timeout_seconds إذا لم يجده فوراً.
    """
    user = os.getenv("GMAIL_USER", "emadhlaweh5@gmail.com")
    password = os.getenv("GMAIL_APP_PASSWORD", "").replace(" ", "")
    
    if not password:
        _log("❌ GMAIL_APP_PASSWORD غير موجود في المتغيرات!")
        return None

    _log(f"🔍 نبحث عن كود يوتيوب في إيميل {user}...")
    start_time = time.time()

    while time.time() - start_time < timeout_seconds:
        try:
            # الاتصال بـ Gmail
            mail = imaplib.IMAP4_SSL("imap.gmail.com")
            mail.login(user, password)
            
            # البحث في الصندوق الوارد والـ Spam
            folders = ['INBOX', '"[Gmail]/Spam"', '"[Gmail]/All Mail"']
            
            for folder in folders:
                try:
                    mail.select(folder)
                    # البحث عن رسائل من يوتيوب في آخر يومين
                    status, messages = mail.search(None, '(FROM "youtube.com")')
                    
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
                        
                        # استخراج النص
                        body = ""
                        if msg.is_multipart():
                            for part in msg.walk():
                                if part.get_content_type() in ["text/plain", "text/html"]:
                                    try:
                                        body += part.get_payload(decode=True).decode(errors="ignore")
                                    except:
                                        pass
                        else:
                            try:
                                body = msg.get_payload(decode=True).decode(errors="ignore")
                            except:
                                pass

                        # البحث عن كود مكون من 6 أرقام
                        # يوتيوب عادة يبعث الكود بصيغة "G-123456" أو "123456"
                        match = re.search(r'\b(?:G-)?(\d{6})\b', body + " " + subject)
                        
                        if match:
                            code = match.group(1)
                            _log(f"✅ تم العثور على الكود: {code}")
                            mail.logout()
                            return code
                            
                except Exception as e:
                    _log(f"⚠️ خطأ في مجلد {folder}: {e}")
                    
            mail.logout()
            _log(f"⏳ لم يصل الكود بعد... انتظار {check_interval} ثواني.")
            time.sleep(check_interval)

        except Exception as e:
            _log(f"⚠️ خطأ في الاتصال بالإيميل: {e}")
            time.sleep(check_interval)

    _log("❌ انتهت المهلة ولم نجد الكود.")
    return None
