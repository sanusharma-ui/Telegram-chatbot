# AI Gmail Assistant for Telegram

> A production-oriented Gmail assistant inside Telegram that can read emails, search inboxes, summarize threads, create drafts, manage labels, and handle real email workflows through natural conversation.

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

This project is a **natural-language Gmail assistant built inside Telegram**.

Instead of forcing users to memorize rigid slash commands for every action, the system lets them interact like they would with a real assistant.

For example, users can say:

- **"Show my latest emails"**
- **"Read the first one"**
- **"Draft a reply saying I’m available on Monday"**
- **"Create a label called internships"**
- **"Archive those mails"**

The assistant understands intent, selects the right Gmail tool, executes the workflow, and replies naturally.

That makes it much more practical than a traditional command bot and much closer to the kind of AI workflow assistant businesses actually need.

---

## Why This Project Matters

Most bots usually do only one of these things well:

1. **Chat naturally**
2. **Perform real actions**

Very few do both in a reliable, product-oriented way.

This project combines:

- **Conversational AI**
- **Real Gmail operations**
- **OAuth-based secure access**
- **Natural-language tool orchestration**
- **Draft-first workflows**
- **Safety-aware action handling**
- **Telegram-native interaction**

The result is not just a chatbot.  
It is a working example of how to build an AI assistant that can interact with real systems and complete useful tasks.

---

## Core Features

### 1. Natural-Language Gmail Actions

Users can speak normally instead of relying only on slash commands.

**Examples:**

- `Meri latest mails dikhao`
- `Inbox me kya aaya hai`
- `Pehli wali kholo`
- `HR ko reply draft karo ki Monday ko available hu`

---

### 2. Gmail Inbox Access

The assistant can:

- Fetch recent emails
- Read full message bodies
- Search inbox using Gmail search operators
- Summarize email threads

---

### 3. Drafting Workflow

The bot supports practical email composition flows:

- Create email drafts from plain instructions
- Update existing drafts
- Preview drafts before sending

This keeps the workflow safer and more professional than directly sending emails without review.

---

### 4. Gmail Management

Users can manage inbox actions such as:

- Mark read / unread
- Star emails
- Archive messages
- Delete messages
- Create labels
- Delete labels
- List attachments

---

### 5. OAuth Integration

Secure Gmail connection is handled through Google OAuth.

This includes:

- Gmail account connection flow
- Token storage and refresh handling
- Reconnect flow when sessions expire or are revoked

---

### 6. Conversational Agent Layer

A dedicated Gmail agent decides:

- Whether the user message is a normal chat request or a Gmail task
- Which tool should be called
- How to structure the response naturally
- When confirmation or safer handling is needed

---

### 7. Safety-Oriented Design

The system is designed with operational caution in mind.

It includes:

- Confirmation-oriented flow for sensitive actions
- Draft-first behavior for email composition
- Reconnect handling for expired or revoked Gmail sessions
- Safer function routing between chat and tool execution

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
        +-----------------------------+
        |                             |
        v                             v
 /gmail command flow         Natural-language Gmail agent
                                      |
                                      v
                           Tool selection via LLM
                                      |
                                      v
                           Gmail tool execution layer
                                      |
                                      v
                      Gmail API / OAuth / Drafts / Labels

```
## Tech Stack

- **Python**
- **FastAPI**
- **Aiogram**
- **Groq API**
- **Google Gmail API**
- **Google OAuth**
- **JSON / file-based memory**
- **Optional Redis support**
- **Telegram Bot API**

---

## What Makes It Valuable for Clients

This is not just a toy chatbot.

It demonstrates the kind of architecture needed for real AI productivity products:

- **AI assistants with real-world actions**
- **External API orchestration**
- **Secure authentication handling**
- **Safe automation workflows**
- **Conversational UX over operational systems**
- **LLM + tools integration**
- **Production-focused backend design**

This same architecture can be extended beyond Gmail into:

- **Calendars**
- **CRMs**
- **Helpdesk systems**
- **Internal dashboards**
- **Customer support tools**
- **Personal productivity assistants**
- **Business workflow automation**

In other words, this project is a strong proof of capability for building AI agents that actually do useful work.

---

## User Experience

### Command Mode

Power users can still use slash commands when they want direct control.

#### Examples

```text
/gmail connect
/gmail inbox
/gmail search from:hr@company.com
/gmail read <message_id>
/gmail draft hr@company.com | Interview Availability | Write a short professional email...
/gmail send <draft_id>
```

### Natural Conversation Mode

Users can also interact normally.

#### Examples

```text
Show my latest emails
Read the first one
Draft a reply saying I’m available after 2 PM
Create a label called internships
Archive those mails
```

This dual-mode design makes the system both flexible and user-friendly.

---

## Key Modules

### `webhook_entry.py`

Main Telegram webhook entry point.

**Responsibilities:**

- Receives Telegram updates
- Routes `/gmail` commands
- Routes natural-language messages to the Gmail agent
- Falls back to general LLM replies for non-Gmail conversations

### `gmail_agent.py`

Conversational Gmail agent layer.

**Responsibilities:**

- Detect Gmail-related intent
- Manage conversational context
- Call tools
- Stage confirmations
- Return natural assistant-style replies

### `gmail_integration.py`

Core Gmail OAuth and API integration layer.

**Responsibilities:**

- OAuth flow
- Token storage
- Token loading
- Gmail service creation
- Inbox summaries
- Draft handling helpers

### `gmail_search.py`

Handles Gmail search queries.

### `gmail_inbox_ops.py`

Handles inbox operations such as:

- Read
- Archive
- Delete
- Mark read / unread
- Star / unstar

### `gmail_drafts.py`

Handles:

- Update draft
- Delete draft
- Fetch draft

### `gmail_labels.py`

Handles:

- List labels
- Create label
- Delete label
- Apply / remove labels

### `gmail_threads.py`

Handles thread fetching and summarization.

### `gmail_attachments.py`

Handles attachment listing and downloading.

### `groq_handler.py`

General LLM response generation and persona-driven chat logic.

### `personas.py`

Defines assistant personas and prompt behavior.

---

## Safety Design

This project is built with operational caution in mind.

### Current Safety Principles

- Prefer draft creation before sending
- Use confirmation flow for sensitive actions
- Handle revoked or expired Gmail sessions gracefully
- Keep operational tools separate from normal chat replies

### Why This Matters

AI assistants become truly useful when they can take actions.

But once actions are enabled, safety becomes critical.

This project is structured around that reality.

---

## Setup

### 1. Clone the repository

```bash
git clone https://github.com/sanusharma-ui/Telegram-chatbot
cd Telegram-chatbot
```

### 2. Create a virtual environment

```bash
python -m venv .venv
```

### 3. Activate the virtual environment

**Windows**

```bash
.venv\Scripts\activate
```

**Linux / macOS**

```bash
source .venv/bin/activate
```

### 4. Install dependencies

```bash
pip install -r requirements.txt
```

### 5. Create a `.env` file

```env
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
WEBHOOK_SECRET_TOKEN=your_webhook_secret
GROQ_API_KEY=your_groq_api_key

GOOGLE_CLIENT_ID=your_google_client_id
GOOGLE_CLIENT_SECRET=your_google_client_secret
OAUTH_REDIRECT_URI=your_redirect_uri
TOKEN_ENC_KEY=your_encryption_key
REDIS_URL=your_optional_redis_url
```

### 6. Run the backend

```bash
uvicorn webhook_entry:app --host 0.0.0.0 --port 8000
```

---

## Example Use Cases

### Personal Assistant

- Check new emails
- Summarize inbox
- Draft professional replies
- Keep communication organized

### Founder Workflow

- Screen investor emails
- Manage outreach drafts
- Organize labels by category
- Quickly review threads

### Hiring / Internship Workflow

- Draft replies to recruiters
- Manage job-related labels
- Summarize HR email threads
- Archive irrelevant messages

### Productivity Automation

- Use Gmail as an actionable workspace
- Reduce friction in email workflows
- Move from command-based bots to assistant-like systems

---

## Project Highlights

- Natural-language Gmail control inside Telegram
- Function-style tool orchestration
- OAuth-secured Gmail access
- Draft, label, search, inbox, thread, and attachment support
- Human-friendly conversational UX
- Extendable architecture for future AI agents

---

## Example Prompts

```text
Meri latest mails dikhao
Inbox me kya aaya hai
Pehli wali kholo
HR ko reply draft karo ki Monday ko available hu
Create a label called internships
Archive those mails
```

---

## Roadmap

- Stronger confirmation enforcement for send / delete actions
- Richer Hinglish intent coverage
- Reply-to-thread support
- Attachment upload to drafts
- Better memory for references like “send it”, “that one”, or “reply to that”
- Calendar integration
- Multi-tool business assistant mode
- Better analytics and event logging
- Admin dashboard for monitoring tool actions

---

## Ideal Extensions

This architecture can be expanded into:

- Gmail + Calendar executive assistant
- AI sales assistant
- Support operations bot
- Recruiting assistant
- Internal workflow agent
- Multi-app productivity agent

---

## Developer Note

This project was built to explore a more serious direction for AI assistants:

**Not just answering questions — but handling useful workflows in a reliable, production-minded way.**

It reflects practical backend engineering around:

- Conversational systems
- API integration
- Tool calling
- OAuth
- Agent orchestration
- User-facing automation

---

## About the Builder

**Sanu Sharma**  
AI & Python Developer focused on building practical intelligent systems, conversational agents, backend tools, and real-world automation products.

- **Portfolio:** https://sanusharma.dev
- **GitHub:** https://github.com/sanusharma-ui
- **LinkedIn:** https://www.linkedin.com/in/sanu-sharma-256818341/

---

## Contact

If you're looking for someone who can build:

- AI agents
- Custom automation tools
- Intelligent Telegram bots
- Gmail / API workflow assistants
- Backend-heavy AI systems

Feel free to connect.
