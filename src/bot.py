import logging
import sys
from typing import List

# Reconfigure stdout/stderr for Windows console unicode support
if sys.platform == "win32":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

from telegram import Update
from telegram.error import Conflict
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    ConversationHandler,
    filters,
)
from src.config import (
    TELEGRAM_BOT_TOKEN,
    update_twitter_auth_token,
    register_cookie_account,
    restore_cookie_account_from_env,
    clear_twitter_auth_token,
)
from src.health import start_health_server
from src.services.enrichment import EnrichmentService
from src.services.auth_service import TwitterAuthService

# Configure logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
# httpx logs every request URL at INFO, and the Telegram API embeds the bot
# token in the path - so INFO here prints the token on every poll.
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# Initialize Services
enrichment_service = EnrichmentService()
auth_service = TwitterAuthService()

# Conversation states for /login
USERNAME, PASSWORD, EMAIL = range(3)

# Telegram caps a message at 4096 characters.
MAX_MESSAGE_CHARS = 3800


def build_username_messages(usernames: List[str]) -> List[str]:
    """Render usernames as one plain, copy-friendly list split into messages."""
    header = f"\U0001F50D <b>{len(usernames)} username(s) extracted:</b>\n"
    messages: List[str] = []
    current = [header]
    length = len(header)

    for idx, username in enumerate(usernames, 1):
        line = f"{idx}. <code>@{username}</code>"
        if length + len(line) + 1 > MAX_MESSAGE_CHARS and len(current) > 1:
            messages.append("\n".join(current))
            current = []
            length = 0
        current.append(line)
        length += len(line) + 1

    if current:
        messages.append("\n".join(current))
    return messages


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start command."""
    welcome_text = (
        "🚀 <b>Welcome to Lead Enrichment Bot!</b>\n\n"
        "Send me any Twitter/X post link, profile link, or paste/forward comments, "
        "and I will reply with the usernames I find — nothing else.\n\n"
        "<b>Key Commands:</b>\n"
        "• <code>/login</code> - Interactive login to your Twitter / X account\n"
        "• <code>/auth &lt;auth_token&gt; &lt;ct0&gt;</code> - Cookie auth\n"
        "• <code>/auth_status</code> - Check active Twitter login status\n"
        "• <code>/logout</code> - Clear stored session\n"
        "• <code>/help</code> - View usage examples\n\n"
        "<b>Try sending:</b>\n"
        "• <code>https://x.com/jack/status/20</code>\n"
        "• <code>@elonmusk @sama</code>"
    )
    await update.message.reply_text(welcome_text, parse_mode="HTML")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /help command."""
    help_text = (
        "ℹ️ <b>Lead Enrichment Bot Help</b>\n\n"
        "<b>How to extract usernames &amp; commenters:</b>\n"
        "1. <b>Account Login:</b> Send <code>/login</code> to log in with your Twitter account.\n"
        "2. <b>Cookie Auth:</b> Send <code>/auth &lt;auth_token&gt; &lt;ct0&gt;</code> — both cookies "
        "are required; X rejects a session whose ct0 does not match.\n"
        "3. <b>Post Extraction:</b> Send any post link: "
        "<code>https://x.com/username/status/12345</code>. While authenticated the bot returns the "
        "author plus every commenter's username.\n"
        "4. <b>Comment Text Parsing:</b> Copy-paste or forward comments directly into this chat.\n\n"
        "The reply is a plain numbered list of usernames — tap one to copy it."
    )
    await update.message.reply_text(help_text, parse_mode="HTML")


# Interactive Login Handlers
async def login_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Start interactive login conversation."""
    await update.message.reply_text(
        "🔐 <b>Twitter / X Interactive Login</b>\n\n"
        "<b>Step 1/3:</b> Please send your Twitter / X Username (e.g. <code>myhandle</code>).\n"
        "<i>(Send /cancel to abort at any time)</i>",
        parse_mode="HTML"
    )
    return USERNAME


async def login_username(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receive username and prompt for password."""
    context.user_data["tw_username"] = update.message.text.strip()
    await update.message.reply_text(
        "🔒 <b>Step 2/3:</b> Please send your Twitter / X Password.\n"
        "<i>(Note: Your password message will be immediately deleted for privacy)</i>",
        parse_mode="HTML"
    )
    return PASSWORD


async def login_password(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receive password, delete message for privacy, and prompt for email."""
    context.user_data["tw_password"] = update.message.text.strip()
    chat = update.effective_chat
    try:
        await update.message.delete()
    except Exception:
        pass

    # Reply to the deleted message would fail, so send into the chat directly.
    await chat.send_message(
        "📧 <b>Step 3/3:</b> Please send the Email address associated with your Twitter account.",
        parse_mode="HTML"
    )
    return EMAIL


async def login_email(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receive email, perform login, and report status."""
    email = update.message.text.strip()
    username = context.user_data.get("tw_username", "")
    password = context.user_data.get("tw_password", "")

    status_msg = await update.message.reply_text(
        "⏳ <i>Authenticating with Twitter... Please wait...</i>", parse_mode="HTML"
    )

    success, msg = await auth_service.login_account(username, password, email)

    if success:
        # The pool now holds a live session. Do NOT write a placeholder token
        # here: doing so used to overwrite these real cookies and silently
        # break every extraction that followed a successful login.
        await status_msg.edit_text(
            f"🎉 <b>Login Successful!</b>\n\n"
            f"Logged in as <code>@{username.lstrip('@')}</code>.\n"
            "Send a post link and the bot will return the author plus every commenter's username.",
            parse_mode="HTML"
        )
    else:
        await status_msg.edit_text(
            f"❌ <b>Login Failed:</b>\n<code>{msg}</code>\n\n"
            "Please check your credentials and try again using <code>/login</code>, "
            "or send <code>/auth &lt;auth_token&gt; &lt;ct0&gt;</code>.",
            parse_mode="HTML"
        )

    context.user_data.clear()
    return ConversationHandler.END


async def login_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancel login conversation."""
    context.user_data.clear()
    await update.message.reply_text("🚫 Login cancelled.", parse_mode="HTML")
    return ConversationHandler.END


# Cookie Auth
async def auth_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /auth to register a Twitter auth_token + ct0 cookie pair."""
    if len(context.args) < 2:
        reply = (
            "🔐 <b>Twitter Cookie Authentication</b>\n\n"
            "Send both cookies from your logged-in browser session:\n"
            "<code>/auth &lt;auth_token&gt; &lt;ct0&gt;</code>\n\n"
            "<i>Both are required — X rejects requests whose ct0 does not match "
            "the auth_token. Or use <code>/login</code> instead.</i>"
        )
        await update.message.reply_text(reply, parse_mode="HTML")
        return

    auth_token, ct0 = context.args[0].strip(), context.args[1].strip()
    chat = update.effective_chat
    try:
        await update.message.delete()
    except Exception:
        pass

    update_twitter_auth_token(auth_token, ct0)
    try:
        await register_cookie_account(auth_token, ct0)
    except Exception as e:
        logger.error(f"Could not register cookie account: {e}")
        await chat.send_message(
            f"❌ <b>Could not register that session:</b>\n<code>{e}</code>", parse_mode="HTML"
        )
        return

    await chat.send_message(
        "✅ <b>Twitter session saved!</b>\n\n"
        "Send a post link and the bot will return the author plus every commenter's username.",
        parse_mode="HTML"
    )


async def auth_status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Check active Twitter authentication status."""
    checking = await update.message.reply_text(
        "⏳ <i>Checking session...</i>", parse_mode="HTML"
    )
    account_count = await auth_service.get_active_accounts_count()
    verified = await auth_service.verify_session()

    if account_count > 0 and not verified:
        await checking.edit_text(
            "🔒 <b>Twitter Authentication Status:</b> <code>SESSION EXPIRED</code>\n"
            "The stored login no longer works. Re-authenticate with "
            "<code>/auth &lt;auth_token&gt; &lt;ct0&gt;</code>.",
            parse_mode="HTML"
        )
        return

    if verified:
        reply = (
            "🔑 <b>Twitter Authentication Status:</b> <code>CONNECTED</code>\n"
            f"Active accounts: <b>{account_count}</b>\n\n"
            "Post links will return the author plus every commenter's username."
        )
    else:
        reply = (
            "🔒 <b>Twitter Authentication Status:</b> <code>NOT AUTHENTICATED</code>\n\n"
            "Use <code>/login</code>, or send <code>/auth &lt;auth_token&gt; &lt;ct0&gt;</code>."
        )
    await checking.edit_text(reply, parse_mode="HTML")


async def logout_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /logout command."""
    await clear_twitter_auth_token()
    await update.message.reply_text(
        "🚪 <b>Twitter session cleared.</b> The bot is now in unauthenticated mode.",
        parse_mode="HTML"
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle incoming text messages and reply with the usernames found."""
    if not update.message or not update.message.text:
        return

    text = update.message.text
    result = await enrichment_service.extract(text)

    if not result.leads:
        await update.message.reply_text(
            "⚠️ <b>No usernames found!</b>\n\n"
            "Send a Twitter/X post link, profile link, @handle, or paste comment text.\n"
            "<i>Example: https://x.com/jack/status/20</i>",
            parse_mode="HTML"
        )
        return

    usernames = [lead["username"] for lead in result.leads]
    for message in build_username_messages(usernames):
        await update.message.reply_text(
            message, parse_mode="HTML", disable_web_page_preview=True
        )

    # An expired session and an unauthenticated bot both yield no commenters.
    # Say which one happened, so a dead login is never mistaken for a post
    # that simply has no replies.
    if result.session_expired:
        await update.message.reply_text(
            "🔒 <b>Your Twitter session expired.</b>\n"
            "The list above is missing this post's commenters. Re-authenticate with "
            "<code>/auth &lt;auth_token&gt; &lt;ct0&gt;</code> and send the link again.",
            parse_mode="HTML"
        )
    elif "status/" in text.lower() and not await auth_service.is_authenticated():
        await update.message.reply_text(
            "💡 <b>Commenters were not included.</b>\n"
            "Log in with <code>/login</code> or <code>/auth &lt;auth_token&gt; &lt;ct0&gt;</code> "
            "to pull every commenter on a post link.",
            parse_mode="HTML"
        )


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log errors concisely instead of dumping a traceback per failed poll."""
    error = context.error

    if isinstance(error, Conflict):
        logger.error(
            "Another instance of this bot is polling with the same token. "
            "Stop the other process - only one instance may run at a time."
        )
        return

    logger.error("Error while handling an update: %s", error, exc_info=error)

    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "⚠️ <b>Something went wrong handling that message.</b> Please try again.",
                parse_mode="HTML"
            )
        except Exception:
            pass


async def restore_session(application) -> None:
    """Rebuild the X session from the environment before polling starts."""
    if await restore_cookie_account_from_env():
        logger.info("Twitter session restored; commenter extraction is available")


def main() -> None:
    """Start the Telegram bot."""
    if not TELEGRAM_BOT_TOKEN or TELEGRAM_BOT_TOKEN == "your_telegram_bot_token_here":
        logger.error("TELEGRAM_BOT_TOKEN is not set in .env file!")
        print("\n❌ ERROR: TELEGRAM_BOT_TOKEN is missing!")
        print("Please edit the .env file in your project directory and set TELEGRAM_BOT_TOKEN.")
        print("Example: TELEGRAM_BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrsTUVwxyz\n")
        sys.exit(1)

    logger.info("Initializing Telegram Bot Application...")

    # Bind $PORT when the host sets one. Platforms that sleep idle instances
    # require this to deploy at all, and the endpoint is what wakes the bot.
    start_health_server()

    app = (
        ApplicationBuilder()
        .token(TELEGRAM_BOT_TOKEN)
        .post_init(restore_session)
        .build()
    )

    # Login Conversation Handler
    login_conv = ConversationHandler(
        entry_points=[CommandHandler("login", login_start)],
        states={
            USERNAME: [MessageHandler(filters.TEXT & (~filters.COMMAND), login_username)],
            PASSWORD: [MessageHandler(filters.TEXT & (~filters.COMMAND), login_password)],
            EMAIL: [MessageHandler(filters.TEXT & (~filters.COMMAND), login_email)],
        },
        fallbacks=[CommandHandler("cancel", login_cancel)],
    )

    app.add_handler(login_conv)
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("auth", auth_command))
    app.add_handler(CommandHandler("auth_status", auth_status_command))
    app.add_handler(CommandHandler("logout", logout_command))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    app.add_error_handler(error_handler)

    logger.info("Bot is starting polling...")
    print("🤖 Lead Enrichment Bot is running! Press Ctrl+C to stop.")
    app.run_polling()


if __name__ == "__main__":
    main()
