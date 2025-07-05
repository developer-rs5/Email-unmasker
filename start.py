# email_unmasker.py

import re
import smtplib
import dns.resolver
import time
import argparse
import os
from itertools import product
from concurrent.futures import ThreadPoolExecutor, as_completed
from flask import Flask, render_template_string, request, redirect, url_for
from rich.console import Console
from rich.live import Live
from rich.table import Table
from rich.panel import Panel
from rich.progress import Progress, BarColumn, TimeRemainingColumn, TextColumn
from rich.layout import Layout
from pyfiglet import Figlet

CHARSET = 'abcdefghijklmnopqrstuvwxyz0123456789'
console = Console()
app = Flask(__name__)
VALID_EMAILS_FILE = "results/valid-emails.txt"

rows = []  # For scrollable live view
MAX_VISIBLE = 20  # Show only last 20 emails in table

def animated_banner():
    fig = Figlet(font='slant')
    title = fig.renderText('EMAIL UNMASKER')
    console.print(f"[bold cyan]{title}[/bold cyan]")
    console.print("[bold yellow]Developed by: [green]developer.rs[/green][/bold yellow]\n")

def generate_emails(masked):
    prefix, domain = masked.split('@')
    positions = [i for i, c in enumerate(prefix) if c == '*']
    known = [c if c != '*' else None for c in prefix]

    total = len(CHARSET) ** len(positions)
    console.print(f"[cyan]Generating {total} combinations...[/cyan]")
    for combo in product(CHARSET, repeat=len(positions)):
        temp = known[:]
        for pos, char in zip(positions, combo):
            temp[pos] = char
        yield ''.join(temp) + '@' + domain

def is_valid_email(email):
    try:
        domain = email.split('@')[1]
        mx_record = dns.resolver.resolve(domain, 'MX')
        host = str(mx_record[0].exchange)

        server = smtplib.SMTP(host, 25, timeout=5)
        server.helo()
        server.mail('check@example.com')
        code, _ = server.rcpt(email)
        server.quit()
        return code == 250
    except Exception:
        return False

def run_cli(masked, threads):
    os.makedirs("results", exist_ok=True)
    emails = list(generate_emails(masked))
    total = len(emails)
    console.print(f"[blue]Total guesses: {total} | Threads: {threads}[/blue]")

    start_time = time.time()
    checked_count = 0
    valid_emails = set()
    seen_emails = set()

    progress = Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        "[progress.percentage]{task.percentage:>3.0f}%",
        TimeRemainingColumn(),
    )
    task = progress.add_task("Checking...", total=total)

    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("Email", style="cyan")
    table.add_column("Status")

    layout = Layout()
    layout.split(
        Layout(name="top", ratio=4),
        Layout(name="bottom", ratio=1)
    )
    layout["top"].update(table)
    layout["bottom"].update(progress)

    with Live(layout, refresh_per_second=10, console=console):
        with ThreadPoolExecutor(max_workers=threads) as executor:
            futures = {executor.submit(is_valid_email, email): email for email in emails}
            for future in as_completed(futures):
                email = futures[future]
                if email in seen_emails:
                    continue
                seen_emails.add(email)

                try:
                    valid = future.result()
                    status = "[green]✅ Valid[/green]" if valid else "[red]❌ Invalid[/red]"
                    rows.append((email, status))
                    if len(rows) > MAX_VISIBLE:
                        rows.pop(0)
                    table.rows.clear()
                    for em, stat in rows:
                        table.add_row(em, stat)
                    if valid:
                        valid_emails.add(email)
                except Exception:
                    rows.append((email, "[yellow]⚠️ Error[/yellow]"))

                checked_count += 1
                elapsed = time.time() - start_time
                if checked_count > 0:
                    avg_time = elapsed / checked_count
                    eta = int((total - checked_count) * avg_time)
                    progress.update(task, advance=1, description=f"ETA: {eta}s")

    if valid_emails:
        with open(VALID_EMAILS_FILE, "w") as f:
            for email in sorted(valid_emails):
                f.write(email + "\n")
        box = "\n".join(sorted(valid_emails))
        console.print(Panel(box, title="✅ Valid Emails Found", border_style="green"))
    else:
        console.print(Panel("No valid emails found.", title="❌ Result", border_style="red"))

@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        masked = request.form['masked']
        threads = int(request.form['threads'])
        run_cli(masked, threads)
        return redirect(url_for('results'))
    return render_template_string('''
        <h2>Email Unmasker Web</h2>
        <form method="post">
            Masked Email: <input name="masked" required><br>
            Threads: <input name="threads" type="number" value="50" required><br>
            <button type="submit">Start</button>
        </form>
    ''')

@app.route('/results')
def results():
    if os.path.exists(VALID_EMAILS_FILE):
        with open(VALID_EMAILS_FILE) as f:
            data = f.read()
    else:
        data = "No valid emails found."
    return f"<pre style='max-height: 500px; overflow-y: scroll;'>{data}</pre><br><a href='/'>Back</a>"

def cli_entry():
    parser = argparse.ArgumentParser(description='Email Unmasker by developer.rs')
    parser.add_argument('-e', '--email', help='Masked email (e.g. r****r@gmail.com)')
    parser.add_argument('-t', '--threads', help='Threads per second', type=int, default=50)
    parser.add_argument('--web', help='Launch web interface', action='store_true')
    args = parser.parse_args()

    if args.web:
        app.run(debug=True, port=5000)
    elif args.email:
        run_cli(args.email, args.threads)
    else:
        animated_banner()
        email = console.input("Enter masked email (e.g. r******s@gmail.com): ")
        threads = int(console.input("How many threads (requests per second)? (e.g. 50): "))
        run_cli(email.strip().lower(), threads)

if __name__ == "__main__":
    cli_entry()
