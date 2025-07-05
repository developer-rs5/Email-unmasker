import re
import smtplib
import dns.resolver
import time
import argparse
import os
import socket
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
        
        # DNS MX Record Check
        try:
            mx_records = dns.resolver.resolve(domain, 'MX')
            if not mx_records:
                return False
        except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN, 
                dns.resolver.NoNameservers, dns.resolver.Timeout):
            return False

        # Try each MX record
        for mx in mx_records:
            host = str(mx.exchange).rstrip('.')
            try:
                # SMTP Verification
                with smtplib.SMTP(host, 25, timeout=10) as server:
                    server.set_debuglevel(0)
                    
                    # Some servers require EHLO instead of HELO
                    try:
                        server.ehlo()
                    except smtplib.SMTPHeloError:
                        server.helo()
                    
                    # Verify sender
                    try:
                        server.mail('verify@example.com')
                    except smtplib.SMTPResponseException:
                        continue
                    
                    # Verify recipient
                    code, _ = server.rcpt(email)
                    if code == 250:
                        server.quit()
                        return True
                    
                    server.quit()
            except (smtplib.SMTPServerDisconnected, smtplib.SMTPConnectError,
                   smtplib.SMTPResponseException, socket.timeout,
                   ConnectionRefusedError, TimeoutError,
                   smtplib.SMTPNotSupportedError, smtplib.SMTPAuthenticationError):
                continue
        
        return False
    except Exception as e:
        console.print(f"[yellow]Error checking {email}: {str(e)}[/yellow]")
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
    table.add_row("Starting verification...", "⏳")

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
                        console.print(f"[green]Found valid email: {email}[/green]")
                except Exception as e:
                    rows.append((email, f"[yellow]⚠️ Error ({str(e)[:30]})[/yellow]"))
                    console.print(f"[red]Error processing {email}: {str(e)}[/red]")

                checked_count += 1
                elapsed = time.time() - start_time
                progress.update(task, advance=1, description=f"Checked {checked_count}/{total}")

    if valid_emails:
        with open(VALID_EMAILS_FILE, "w") as f:
            for email in sorted(valid_emails):
                f.write(email + "\n")
        box = "\n".join(sorted(valid_emails))
        console.print(Panel(box, title="✅ Valid Emails Found", border_style="green"))
        console.print(f"[green]Results saved to {VALID_EMAILS_FILE}[/green]")
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
        <html>
        <head>
            <title>Email Unmasker</title>
            <style>
                body { font-family: Arial, sans-serif; background: #1a1a1a; color: #f0f0f0; text-align: center; }
                input, button { padding: 10px; margin: 10px; font-size: 1em; }
                button { background-color: #28a745; color: white; border: none; cursor: pointer; }
                h2 { color: #00ffcc; }
            </style>
        </head>
        <body>
            <h2>🔍 Email Unmasker Web</h2>
            <form method="post">
                <label>Masked Email:</label><br>
                <input name="masked" placeholder="r****r@gmail.com" required><br>
                <label>Threads:</label><br>
                <input name="threads" type="number" value="20" min="1" max="100" required><br>
                <button type="submit">Start</button>
            </form>
            <p>Developed by <b>developer.rs</b> | CLI + Web | SMTP-based email validation</p>
        </body>
        </html>
    ''')

@app.route('/results')
def results():
    if os.path.exists(VALID_EMAILS_FILE):
        with open(VALID_EMAILS_FILE) as f:
            data = f.read()
    else:
        data = "No valid emails found."
    return render_template_string('''
        <html>
        <head>
            <title>Results</title>
            <style>
                body { background: #111; color: #eee; font-family: monospace; padding: 20px; }
                pre { max-height: 500px; overflow-y: scroll; background: #222; padding: 10px; border: 1px solid #444; }
                a { color: #00ffcc; }
            </style>
        </head>
        <body>
            <h2>✅ Valid Emails</h2>
            <pre>{{data}}</pre>
            <a href="/">← Back</a>
        </body>
        </html>
    ''', data=data)

def cli_entry():
    parser = argparse.ArgumentParser(description='Email Unmasker by developer.rs')
    parser.add_argument('-e', '--email', help='Masked email (e.g. r****r@gmail.com)')
    parser.add_argument('-t', '--threads', help='Threads count (default: 20)', type=int, default=20)
    parser.add_argument('--web', help='Launch web interface', action='store_true')
    args = parser.parse_args()

    if args.web:
        app.run(host='0.0.0.0', port=5000, debug=False)
    elif args.email:
        if not re.match(r'^[a-zA-Z0-9*]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', args.email):
            console.print("[red]Error: Invalid email format. Use pattern like r****r@gmail.com[/red]")
            return
        run_cli(args.email.strip().lower(), args.threads)
    else:
        animated_banner()
        while True:
            email = console.input("Enter masked email (e.g. r******s@gmail.com): ").strip().lower()
            if re.match(r'^[a-zA-Z0-9*]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', email):
                break
            console.print("[red]Invalid format. Please include @ and domain (e.g. r****r@gmail.com)[/red]")
        
        while True:
            try:
                threads = int(console.input("How many threads (1-100)? (default 20): ") or 20)
                if 1 <= threads <= 100:
                    break
                console.print("[red]Please enter a number between 1 and 100[/red]")
            except ValueError:
                console.print("[red]Please enter a valid number[/red]")
        
        run_cli(email, threads)

if __name__ == "__main__":
    cli_entry()