import re
import smtplib
import dns.resolver
import time
import argparse
import os
import socket
from itertools import product
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError
from flask import Flask, render_template_string, request, redirect, url_for
from rich.console import Console
from rich.live import Live
from rich.table import Table
from rich.panel import Panel
from rich.progress import Progress, BarColumn, TimeRemainingColumn, TextColumn
from rich.layout import Layout
from pyfiglet import Figlet

# Configuration
CHARSET = 'abcdefghijklmnopqrstuvwxyz0123456789'
MAX_THREADS = 50  # Reduced from 100 to be more conservative
SMTP_TIMEOUT = 8  # Increased timeout
DNS_TIMEOUT = 5
MAX_RETRIES = 2

console = Console()
app = Flask(__name__)
VALID_EMAILS_FILE = "results/valid-emails.txt"

rows = []
MAX_VISIBLE = 20

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

def check_smtp(host, email):
    try:
        with smtplib.SMTP(host, 25, timeout=SMTP_TIMEOUT) as server:
            server.set_debuglevel(0)
            
            # Try EHLO first, then HELO
            try:
                server.ehlo()
            except smtplib.SMTPHeloError:
                server.helo()
            
            # Verify sender
            try:
                server.mail('verify@example.com')
            except smtplib.SMTPResponseException:
                return False
            
            # Verify recipient with retries
            for _ in range(MAX_RETRIES):
                try:
                    code, _ = server.rcpt(email)
                    if code == 250:
                        return True
                    break
                except smtplib.SMTPServerDisconnected:
                    server.connect(host, 25)
                    server.ehlo_or_helo_if_needed()
                    continue
            
            return False
    except (socket.timeout, ConnectionRefusedError, smtplib.SMTPConnectError):
        return False
    except Exception as e:
        console.print(f"[yellow]SMTP Error for {host}: {str(e)}[/yellow]")
        return False

def is_valid_email(email):
    try:
        domain = email.split('@')[1]
        
        # DNS MX Record Check with timeout
        try:
            dns.resolver.default_resolver = dns.resolver.Resolver(configure=False)
            dns.resolver.default_resolver.nameservers = ['8.8.8.8', '1.1.1.1']  # Google and Cloudflare DNS
            dns.resolver.default_resolver.timeout = DNS_TIMEOUT
            dns.resolver.default_resolver.lifetime = DNS_TIMEOUT
            
            mx_records = dns.resolver.resolve(domain, 'MX')
            if not mx_records:
                return False
        except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN, 
               dns.resolver.NoNameservers, dns.resolver.Timeout) as e:
            return False

        # Try each MX record with timeout protection
        for mx in mx_records:
            host = str(mx.exchange).rstrip('.')
            if check_smtp(host, email):
                return True
        
        return False
    except Exception as e:
        console.print(f"[red]Unexpected error checking {email}: {str(e)}[/red]")
        return False

def run_cli(masked, threads):
    os.makedirs("results", exist_ok=True)
    emails = list(generate_emails(masked))
    total = len(emails)
    console.print(f"[blue]Total guesses: {total} | Threads: {min(threads, MAX_THREADS)}[/blue]")

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
    table.add_row("Initializing verification...", "⏳")

    layout = Layout()
    layout.split(
        Layout(name="top", ratio=4),
        Layout(name="bottom", ratio=1)
    )
    layout["top"].update(table)
    layout["bottom"].update(progress)

    with Live(layout, refresh_per_second=10, console=console):
        with ThreadPoolExecutor(max_workers=min(threads, MAX_THREADS)) as executor:
            futures = {executor.submit(is_valid_email, email): email for email in emails}
            
            for future in as_completed(futures, timeout=SMTP_TIMEOUT + 5):
                try:
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
                        rows.append((email, f"[yellow]⚠️ Error[/yellow]"))
                        console.print(f"[red]Error processing {email}: {str(e)}[/red]")

                    checked_count += 1
                    progress.update(task, advance=1, description=f"Checked {checked_count}/{total}")
                
                except TimeoutError:
                    console.print(f"[yellow]Timeout checking an email, continuing...[/yellow]")
                    continue

    if valid_emails:
        with open(VALID_EMAILS_FILE, "w") as f:
            for email in sorted(valid_emails):
                f.write(email + "\n")
        box = "\n".join(sorted(valid_emails))
        console.print(Panel(box, title="✅ Valid Emails Found", border_style="green"))
        console.print(f"[green]Results saved to {VALID_EMAILS_FILE}[/green]")
    else:
        console.print(Panel("No valid emails found.", title="❌ Result", border_style="red"))

# [Rest of the code remains the same...]

if __name__ == "__main__":
    cli_entry()