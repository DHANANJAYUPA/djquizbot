import os
import asyncio
import re
import pdfplumber
import psycopg2
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

user_sessions = {}

# ---------------- DATABASE ---------------- #

def init_db():
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS results (
            id SERIAL PRIMARY KEY,
            user_id BIGINT,
            username TEXT,
            score INT
        );
    """)
    conn.commit()
    cur.close()
    conn.close()

def save_result(user_id, username, score):
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO results (user_id, username, score) VALUES (%s, %s, %s)",
        (user_id, username, score),
    )
    conn.commit()
    cur.close()
    conn.close()

def get_leaderboard():
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    cur.execute(
        "SELECT username, MAX(score) FROM results GROUP BY username ORDER BY MAX(score) DESC LIMIT 10"
    )
    data = cur.fetchall()
    cur.close()
    conn.close()
    return data

# ---------------- PDF PARSER ---------------- #

def parse_pdf(path):
    questions = []
    with pdfplumber.open(path) as pdf:
        text = ""
        for page in pdf.pages:
            text += page.extract_text()

    pattern = r'\d+\..*?Answer:\s*[A-D]'
    matches = re.findall(pattern, text, re.DOTALL)

    for match in matches:
        parts = match.split("Answer:")
        question = parts[0].strip()
        correct = parts[1].strip()

        questions.append({
            "question": question,
            "correct": correct
        })

    return questions

# ---------------- BOT COMMANDS ---------------- #

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Welcome to DJ Quiz Bot 🎯\n\nUpload your MCQ PDF to start quiz."
    )

async def leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = get_leaderboard()
    if not data:
        await update.message.reply_text("No leaderboard data yet.")
        return

    text = "🏆 Top 10 Leaderboard:\n\n"
    for i, row in enumerate(data, start=1):
        text += f"{i}. {row[0]} - {row[1]}\n"

    await update.message.reply_text(text)

# ---------------- HANDLE PDF ---------------- #

async def handle_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username or "User"

    file = await update.message.document.get_file()
    await file.download_to_drive("quiz.pdf")

    questions = parse_pdf("quiz.pdf")

    if not questions:
        await update.message.reply_text("Invalid PDF format.")
        return

    user_sessions[user_id] = {
        "questions": questions,
        "score": 0,
        "current": 0,
        "answered": False,
        "username": username
    }

    await send_question(user_id, context)

# ---------------- SEND QUESTION ---------------- #

async def send_question(user_id, context):
    session = user_sessions[user_id]

    if session["current"] >= len(session["questions"]):
        score = session["score"]
        save_result(user_id, session["username"], score)

        await context.bot.send_message(
            chat_id=user_id,
            text=f"Quiz Finished 🎉\nFinal Score: {score}"
        )
        return

    session["answered"] = False
    q = session["questions"][session["current"]]

    keyboard = [
        [InlineKeyboardButton("A", callback_data="A"),
         InlineKeyboardButton("B", callback_data="B")],
        [InlineKeyboardButton("C", callback_data="C"),
         InlineKeyboardButton("D", callback_data="D")]
    ]

    await context.bot.send_message(
        chat_id=user_id,
        text=q["question"],
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

    asyncio.create_task(timer(user_id, context))

# ---------------- TIMER ---------------- #

async def timer(user_id, context):
    await asyncio.sleep(30)
    session = user_sessions.get(user_id)

    if session and not session["answered"]:
        session["score"] -= 1
        session["current"] += 1
        await send_question(user_id, context)

# ---------------- ANSWER ---------------- #

async def answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    session = user_sessions.get(user_id)

    if not session or session["answered"]:
        return

    session["answered"] = True
    selected = query.data
    correct = session["questions"][session["current"]]["correct"]

    if selected == correct:
        session["score"] += 4
    else:
        session["score"] -= 1

    session["current"] += 1
    await send_question(user_id, context)

# ---------------- MAIN ---------------- #

def main():
    init_db()
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("leaderboard", leaderboard))
    app.add_handler(MessageHandler(filters.Document.PDF, handle_pdf))
    app.add_handler(CallbackQueryHandler(answer))

    app.run_polling()

if __name__ == "__main__":
    main()
