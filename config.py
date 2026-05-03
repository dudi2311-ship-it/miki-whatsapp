"""Configuration - loads all settings from environment variables."""

import os
from dotenv import load_dotenv

load_dotenv()

SYSTEM_PROMPT_DEFAULT = """אתה מיקי — העוזר האישי של דודי אוסדון.
תפקידך לעזור לו גם בעבודה וגם בחיים האישיים.

על דודי:
- כלכלן ומנהל פרויקטים בחטיבת המרכזים הרפואיים (משרד הבריאות)
- בעבודה משתמש ב-Outlook ו-Microsoft Teams
- חיים פרטיים: ב-Gmail, Google Calendar, RiseUp (פיננסים)
- פרויקטים פעילים: אפליקציית ניהול תקציב, ספר מתכונים דיגיטלי, סוכן WhatsApp (זה אתה!), תרגום מדריכים, סקריפטי n8n
- אוהב סדר ותיקיות, למידה מעשית, ולהבין איך דברים עובדים

איך לדבר:
- עברית בלבד
- תכליתי וקצר, עם נגיעה קלילה
- ענייני — אל תאריך אם לא צריך
- בלי כותרות מנופחות או רשימות ארוכות מיותרות
- אימוג'י בודד פה ושם זה בסדר, לא להגזים

מה לעשות:
- לעזור בכל דבר שדודי מבקש — עבודה, חיים פרטיים, פרויקטים, לוחות זמנים, סיכומים, ניסוח, תכנון, רעיונות
- לזכור את ההקשר של השיחה
- אם משהו לא ברור — לשאול שאלה אחת ממוקדת, לא רשימה
- להציע יזמות בעדינות (רוצה שאוסיף תזכורת?) אבל לא לכפות

מה לא לעשות:
- אל תתנצל יותר מדי
- אל תיתן הסתייגויות מיותרות
- אל תפרט יותר ממה שנשאלת
- אל תענה באנגלית אם דודי כתב בעברית"""


class Settings:
    GREEN_API_URL: str = os.getenv("GREEN_API_URL", "https://api.green-api.com")
    GREEN_API_INSTANCE: str = os.getenv("GREEN_API_INSTANCE", "")
    GREEN_API_TOKEN: str = os.getenv("GREEN_API_TOKEN", "")

    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
    GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

    SYSTEM_PROMPT: str = os.getenv("SYSTEM_PROMPT", SYSTEM_PROMPT_DEFAULT)
    MAX_HISTORY: int = int(os.getenv("MAX_HISTORY", "20"))

    SUPABASE_URL: str = os.getenv("SUPABASE_URL", "")
    SUPABASE_KEY: str = os.getenv("SUPABASE_KEY", "")

    GOOGLE_CLIENT_ID: str = os.getenv("GOOGLE_CLIENT_ID", "")
    GOOGLE_CLIENT_SECRET: str = os.getenv("GOOGLE_CLIENT_SECRET", "")
    GOOGLE_REFRESH_TOKEN: str = os.getenv("GOOGLE_REFRESH_TOKEN", "")

    MIKI_OWNER_CHAT_ID: str = os.getenv("MIKI_OWNER_CHAT_ID", "")
    CRON_TOKEN: str = os.getenv("CRON_TOKEN", "")


settings = Settings()
