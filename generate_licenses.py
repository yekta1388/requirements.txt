import sqlite3
import secrets
import string

DB_NAME = "academy_bot.db"

def make_code(prefix: str, length: int = 6) -> str:
    chars = string.ascii_uppercase + string.digits
    clean_chars = ''.join([c for c in chars if c not in 'OI01'])
    return f"{prefix}-{''.join(secrets.choice(clean_chars) for _ in range(length))}"

def generate_all():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS licenses (
            code TEXT PRIMARY KEY,
            role TEXT,
            is_used INTEGER DEFAULT 0
        )
    """)

    roles_count = {
        "teacher": (5, "TCH"),
        "supporter": (10, "SUP"),
        "student": (60, "STU")
    }

    generated_data = []

    for role, (count, prefix) in roles_count.items():
        for _ in range(count):
            while True:
                code = make_code(prefix)
                try:
                    cursor.execute("INSERT INTO licenses (code, role, is_used) VALUES (?, ?, 0)", (code, role))
                    generated_data.append((role, code))
                    break
                except sqlite3.IntegrityError:
                    continue

    conn.commit()
    conn.close()

    with open("licenses_list.txt", "w", encoding="utf-8") as f:
        f.write("=== لایسنس‌های اساتید (۵ عدد) ===\n")
        for role, code in generated_data:
            if role == "teacher":
                f.write(f"{code}\n")

        f.write("\n=== لایسنس‌های پشتیبانان (۱۰ عدد) ===\n")
        for role, code in generated_data:
            if role == "supporter":
                f.write(f"{code}\n")

        f.write("\n=== لایسنس‌های فراگیران (۶۰ عدد) ===\n")
        for role, code in generated_data:
            if role == "student":
                f.write(f"{code}\n")

    print("تمام ۷۵ لایسنس با موفقیت ساخته و ذخیره شدند.")

if __name__ == "__main__":
    generate_all()
