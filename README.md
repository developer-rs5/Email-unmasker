# 📧 Email Unmasker

🔍 A powerful ethical tool to unmask hidden emails like `r****r@gmail.com` using smart brute-force + SMTP verification.

> 🛠 Developed by: **developer.rs**

---

## ⚙️ Features

- 🔢 Smart guessing for masked emails with `*` (e.g. `rs****r@gmail.com`)
- 📬 Real-time SMTP email existence checking (no email is sent)
- 🖥️ Beautiful CLI with live progress, ETA, and result table
- 🌐 Web interface (Flask-based) for easy browser use
- 🧵 Adjustable threads/requests per second
- 💾 Saves valid emails to `results/valid-emails.txt`
- ✅ Works with CLI args or interactive input

---

## 📦 Installation

```bash
git clone https://github.com/your-username/email-unmasker.git
cd email-unmasker
pip install -r requirements.txt
