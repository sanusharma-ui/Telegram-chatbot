# AI Gmail Assistant for Telegram

> A natural-language Gmail assistant inside Telegram that can read emails, search inbox, summarize threads, create drafts, manage labels, and handle email workflows through conversation — not just rigid commands.

<p align="center">
  <img alt="Python" src="https://img.shields.io/badge/Python-3.10%2B-blue">
  <img alt="FastAPI" src="https://img.shields.io/badge/FastAPI-Backend-green">
  <img alt="Telegram Bot" src="https://img.shields.io/badge/Telegram-Bot-2CA5E0">
  <img alt="Groq" src="https://img.shields.io/badge/Groq-LLM-orange">
  <img alt="Gmail API" src="https://img.shields.io/badge/Gmail-API-red">
  <img alt="OAuth" src="https://img.shields.io/badge/OAuth-Google-yellow">
</p>

---

## Overview

This project is a production-oriented Gmail assistant that lives inside Telegram and behaves like a real conversational agent.

Instead of forcing users to memorize slash commands for every email task, the system allows natural requests like:

- “Show my latest emails”
- “Read the first one”
- “Draft a reply saying I’m available on Monday”
- “Create a label called internships”
- “Archive those mails”

The assistant interprets intent, selects the correct Gmail tool, executes the action, and responds naturally.

This makes the bot more practical, more human-friendly, and far more valuable for real users, founders, teams, and businesses.

---

## Why This Project Matters

Most bots can only do one of these two things:

1. **Chat well**
2. **Perform useful actions**

Very few can do both in a clean and reliable way.

This system combines:

- **Conversational AI**
- **Real Gmail actions**
- **OAuth-based secure access**
- **Natural-language tool calling**
- **Draft-first workflows**
- **Safety-aware action handling**
- **Telegram-native interaction**

The result is a bot that feels closer to a real assistant than a traditional command bot.

---

## Core Features

### Natural-Language Gmail Actions
Users can talk normally instead of relying only on slash commands.

Examples:
- “Meri latest mails dikhao”
- “Inbox me kya aaya hai”
- “Pehli wali kholo”
- “HR ko reply draft karo ki Monday ko available hu”

### Gmail Inbox Access
- Fetch recent emails
- Read full message bodies
- Search inbox using Gmail search operators
- Summarize email threads

### Drafting Workflow
- Create email drafts from plain instructions
- Update existing drafts
- Preview drafts before sending

### Gmail Management
- Mark read / unread
- Star emails
- Archive messages
- Delete messages
- Create and delete labels
- List attachments

### OAuth Integration
- Secure Gmail connection using Google OAuth
- Token storage and refresh handling
- Reconnect flow when session expires or is revoked

### Conversational Agent Layer
A dedicated Gmail agent decides:
- whether the message is a normal chat request
- or a Gmail action
- which tool to call
- how to present results naturally

### Safety-Oriented Design
- Confirmation-based action flow for sensitive operations
- Draft-first behavior for email composition
- Reconnect handling when Gmail tokens expire
- Safer tool execution with structured function routing

---

## Architecture

```text
User (Telegram)
        |
        v
   FastAPI Webhook
        |
        v
  process_update()
        |
        +--------------------------+
        |                          |
        |                          |
        v                          v
 /gmail command flow        Natural-language Gmail agent
                                   |
                                   v
                          Tool selection via LLM
                                   |
                                   v
                      Gmail tool execution layer
                                   |
                                   v
                         Gmail API / OAuth / Drafts

Tech Stack
Python
FastAPI
Aiogram
Groq API
Google Gmail API
Google OAuth
JSON / File-based memory
Optional Redis support
Telegram Bot API
What Makes It Valuable for Clients

This is not just a toy chatbot.

It demonstrates the kind of architecture needed for real AI productivity tools:

AI assistant with real-world actions
external API orchestration
safe automation workflows
secure auth handling
conversational UX over operational systems
LLM + tools integration
production-focused backend design

This pattern can be extended beyond Gmail into:

calendars
CRMs
helpdesk systems
internal dashboards
customer support tools
personal productivity assistants
business workflow automation

In other words, this project is a strong proof of capability for building AI agents that actually do useful work.

User Experience
Command Mode

Power users can still use slash commands.

Examples:

/gmail connect
/gmail inbox
/gmail search from:hr@company.com
/gmail read <message_id>
/gmail draft hr@company.com | Interview Availability | Write a short professional email...
/gmail send <draft_id>
Natural Conversation Mode

Users can also speak normally.

Examples:

Show my latest emails
Read the first one
Draft a reply saying I’m available after 2 PM
Create a label called internships
Archive those mails

This dual-mode design makes the system both flexible and user-friendly.

Key Modules
webhook_entry.py

Main Telegram webhook entry point.

Responsibilities:

receives Telegram updates
routes /gmail commands
routes natural messages to Gmail agent
falls back to general LLM reply for non-Gmail messages
gmail_agent.py

Conversational Gmail agent layer.

Responsibilities:

detect Gmail-related intent
manage conversational context
call tools
stage confirmations
return natural assistant replies
gmail_integration.py

Core Gmail OAuth and API integration.

Responsibilities:

OAuth flow
token storage
token loading
Gmail service creation
inbox summaries
draft handling helpers
gmail_search.py

Handles Gmail search queries.

gmail_inbox_ops.py

Handles inbox operations like:

read
archive
delete
mark read/unread
star/unstar
gmail_drafts.py

Handles:

update draft
delete draft
fetch draft
gmail_labels.py

Handles:

list labels
create label
delete label
apply/remove labels
gmail_threads.py

Handles thread fetching and summarization.

gmail_attachments.py

Handles attachment listing and downloading.

groq_handler.py

General LLM response generation and persona-driven chat logic.

personas.py

Defines assistant personas and prompt behavior.

Safety Design

This project is built with operational caution in mind.

Current Safety Principles
Prefer draft creation before sending
Use confirmation flow for sensitive actions
Handle revoked/expired Gmail sessions gracefully
Keep operational tools separate from plain chat replies
Why This Matters

AI assistants become useful only when they can take actions.
But once actions are enabled, safety becomes critical.

This project is structured around that reality.

Setup
1. Clone the repository
git clone https://github.com/sanusharma-ui/Telegram-chatbot
cd Telegram-chatbot
2. Create virtual environment
python -m venv .venv
3. Activate virtual environment

Windows
.venv\Scripts\activate

Linux / macOS
source .venv/bin/activate

4. Install dependencies
pip install -r requirements.txt
5. Create .env
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
WEBHOOK_SECRET_TOKEN=your_webhook_secret
GROQ_API_KEY=your_groq_api_key

GOOGLE_CLIENT_ID=your_google_client_id
GOOGLE_CLIENT_SECRET=your_google_client_secret
OAUTH_REDIRECT_URI=your_redirect_uri
TOKEN_ENC_KEY=your_encryption_key
REDIS_URL=your_optional_redis_url

6. Run the backend
uvicorn webhook_entry:app --host 0.0.0.0 --port 8000
Example Use Cases
Personal Assistant
check new emails
summarize inbox
draft professional replies
keep communication organized
Founder Workflow
screen investor emails
manage outreach drafts
organize labels by category
quickly review threads
Hiring / Internship Workflow
draft replies to recruiters
manage job-related labels
summarize HR email threads
archive irrelevant messages
Productivity Automation
use Gmail as an actionable workspace
reduce friction in email workflows
move from command-based bots to assistant-like systems
Project Highlights
Natural-language Gmail control inside Telegram
Function-style tool orchestration
OAuth-secured Gmail access
Draft, label, search, inbox, thread, and attachment support
Human-friendly conversational UX
Extendable architecture for future AI agents
Example Prompts
Meri latest mails dikhao
Inbox me kya aaya hai
Pehli wali kholo
HR ko reply draft karo ki Monday ko available hu
Create a label called internships
Archive those mails
Roadmap
stronger confirmation enforcement for send/delete actions
richer Hinglish intent coverage
reply-to-thread support
attachment upload to drafts
memory improvements for “send it”, “that one”, “reply to that”
calendar integration
multi-tool business assistant mode
better analytics and event logging
admin dashboard for monitoring tool actions
Ideal Extensions

This architecture can be expanded into:

Gmail + Calendar executive assistant
AI sales assistant
support operations bot
recruiting assistant
internal workflow agent
multi-app productivity agent
Developer Note

This project was built to explore a more serious direction for AI assistants:
not just answering questions, but handling useful workflows in a reliable way.

It reflects practical backend engineering around:

conversational systems
API integration
tool calling
OAuth
agent orchestration
user-facing automation
About the Builder

Sanu Sharma
AI & Python Developer focused on building practical intelligent systems, conversational agents, backend tools, and real-world automation products.

Portfolio: https://sanusharma.dev
GitHub: https://github.com/sanusharma-ui
LinkedIn: https://www.linkedin.com/in/sanu-sharma-256818341/

Contact

If you're looking for someone who can build:

AI agents
custom automation tools
intelligent Telegram bots
Gmail / API workflow assistants
backend-heavy AI systems

feel free to connect.

License

This project is available under the MIT License.

Final Note

This is more than a bot.

It is a practical demonstration of how conversational AI can be connected to real actions, real accounts, and real workflows in a way that feels useful, modern, and product-ready.
