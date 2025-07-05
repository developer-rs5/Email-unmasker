
import re
import smtplib
import dns.resolver
import time
import os
import socket
import threading
from collections import deque
from itertools import product
from concurrent.futures import ThreadPoolExecutor, as_completed
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.progress import Progress, BarColumn, TimeRemainingColumn, TextColumn
from rich.text import Text
from pyfiglet import Figlet
import argparse
from flask import Flask, render_template_string, request, redirect, url_for
from flask_socketio import SocketIO

# Configuration
CHARSET = 'abcdefghijklmnopqrstuvwxyz0123456789'
MAX_THREADS = 500
UNVERIFIABLE_DOMAINS = ['gmail.com', 'googlemail.com', 'yahoo.com', 'outlook.com', 'hotmail.com', 'protonmail.com']
SMTP_TIMEOUT = 5
VALID_EMAILS_FILE = "results/valid-emails.txt"
MAX_DISPLAY_EMAILS = 500
SOCIAL_LINKS = {
    "Discord": "https://discord.zenuxs.xyz",
    "Instagram": "https://instagram.com/developer.rs",
    "GitHub": "https://github.com/developer-rs5"
}

console = Console()
app = Flask(__name__)
socketio = SocketIO(app)

# Shared state for web interface
results_state = {
    'emails': [],
    'valid_emails': [],
    'progress': 0,
    'total': 0,
    'running': False
}

def animated_banner():
    fig = Figlet(font='slant')
    title = fig.renderText('EMAIL UNMASKER')
    console.print(f"[bold cyan]{title}[/bold cyan]")
    console.print("[bold yellow]Developed by: [green]developer.rs[/green][/bold yellow]")
    
    # Display social links
    console.print("\n[bold]Connect with us:[/bold]")
    for platform, url in SOCIAL_LINKS.items():
        console.print(f"[blue]{platform}:[/blue] [link={url}]{url}[/link]")
    console.print()

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

import dns.resolver
import smtplib
import socket

def is_valid_email(email):
    try:
        domain = email.split('@')[1]

        # Quick pre-check to avoid slow SMTP on invalid domains
        try:
            dns.resolver.resolve(domain, 'A')
        except:
            return False

        # Only try the top-priority MX server
        mx_records = sorted(dns.resolver.resolve(domain, 'MX'), key=lambda x: x.preference)[:1]
        if not mx_records:
            return False

        host = str(mx_records[0].exchange).rstrip('.')

        with smtplib.SMTP(host, 25, timeout=1) as server:
            server.set_debuglevel(0)
            server.ehlo()
            server.mail('<>')
            code, _ = server.rcpt(email)
            return code in (250, 251)

    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer, IndexError, dns.resolver.Timeout,
            smtplib.SMTPException, socket.error, TimeoutError):
        return False


def update_web_interface(email, status, valid_count, progress, total):
    try:
        with app.test_request_context():
            socketio.emit('update', {
                'email': email,
                'status': status,
                'valid_count': valid_count,
                'progress': progress,
                'total': total
            })
    except Exception as e:
        print(f"Web interface update failed: {str(e)}")
         
def run_verification(masked, threads):
    global results_state
    results_state['running'] = True 
    results_state['emails'] = []
    results_state['valid_emails'] = []
    
    os.makedirs("results", exist_ok=True)
    emails = list(generate_emails(masked))
    total = len(emails)
    results_state['total'] = total
    
    start_time = time.time()
    checked_count = 0
    valid_emails = set()
    seen_emails = set()

    # Create progress bar
    progress = Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        "[progress.percentage]{task.percentage:>3.0f}%",
        TimeRemainingColumn(),
    )
    task = progress.add_task("Checking...", total=total)
    
    # Create results display using deque
    last_results = deque(maxlen=MAX_DISPLAY_EMAILS)
    results_display = Text("", no_wrap=True)
    results_panel = Panel(results_display, title="Results", border_style="blue")
    
    # Create main display
    main_layout = Panel(
        Panel(progress, title="Progress", border_style="green"),
        title="Email Unmasker", 
        border_style="bold magenta"
    )
    
    with Live(main_layout, refresh_per_second=10, console=console) as live:
        with ThreadPoolExecutor(max_workers=min(threads, MAX_THREADS)) as executor:
            futures = {executor.submit(is_valid_email, email): email for email in emails}
            
            for future in as_completed(futures):
                email = futures[future]
                if email in seen_emails: 
                    continue
                seen_emails.add(email)

                try:
                    valid = future.result()
                    status = "✅ Valid" if valid else "❌ Invalid"
                    color = "green" if valid else "red"
                    
                    # Update results display
                    last_results.appendleft(f"[{color}]{email} - {status}[/]")
                    results_display = Text("\n".join(last_results), no_wrap=True)
                    results_panel.renderable = results_display
                    
                    # Update web interface
                    if valid:
                        valid_emails.add(email)
                        results_state['valid_emails'].append(email)
                    
                    results_state['emails'].append({'email': email, 'status': status})
                    checked_count += 1
                    progress_percent = int((checked_count / total) * 100)
                    
                    update_web_interface(
                        email=email,
                        status=status,
                        valid_count=len(valid_emails),
                        progress=progress_percent,
                        total=total
                    )

                    # Update progress
                    progress.update(task, advance=1)
                    
                    # Refresh display
                    live.update(main_layout)

                except Exception as e:
                    last_results.appendleft(f"[yellow]{email} - ⚠️ Error ({str(e)})[/]")
                    results_display = Text("\n".join(last_results), no_wrap=True)
                    results_panel.renderable = results_display
                    results_state['emails'].append({'email': email, 'status': "⚠️ Error"})
                    progress_percent = int((checked_count / total) * 100)
                    update_web_interface(email, "⚠️ Error", len(valid_emails), progress_percent, total)
                    progress.update(task, advance=1)
                    live.update(main_layout)

    if valid_emails:
        with open(VALID_EMAILS_FILE, "w") as f:
            for email in sorted(valid_emails):
                f.write(email + "\n")
        box = "\n".join(sorted(valid_emails))
        console.print(Panel(box, title="✅ Valid Emails Found", border_style="green"))
    else:
        console.print(Panel("No valid emails found.", title="❌ Result", border_style="red"))
    
    results_state['running'] = False


@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        masked = request.form['masked']
        threads = int(request.form['threads'])
        
        # Start verification in a separate thread
        threading.Thread(
            target=run_verification,
            args=(masked, threads),
            daemon=True
        ).start()
        
        return redirect(url_for('live_results'))
    
    return render_template_string('''
        <html>
        <head>
            <title>Email Unmasker</title>
            <style>
                body { font-family: Arial, sans-serif; background: #1a1a1a; color: #f0f0f0; text-align: center; }
                input, button { padding: 10px; margin: 10px; font-size: 1em; }
                button { background-color: #28a745; color: white; border: none; cursor: pointer; }
                h2 { color: #00ffcc; }
                .social-links { margin-top: 20px; }
                .social-links a { margin: 0 10px; color: #00ccff; text-decoration: none; }
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
            
            <div class="social-links">
                <p>Connect with us:</p>
                <a href="https://discord.zenuxs.xyz" target="_blank">Discord</a>
                <a href="https://instagram.com/developer.rs" target="_blank">Instagram</a>
                <a href="https://github.com/developer-rs5" target="_blank">GitHub</a>
            </div>
            
            <p>Developed by <b>developer.rs</b> | CLI + Web | SMTP-based email validation</p>
        </body>
        </html>
    ''')

@app.route('/live-results')
def live_results():
    return render_template_string('''
        <html>
        <head>
            <title>Live Results</title>
            <style>
                body { font-family: monospace; background: #111; color: #eee; padding: 20px; }
                #results { height: 70vh; overflow-y: auto; border: 1px solid #444; padding: 10px; }
                .valid { color: #0f0; }
                .invalid { color: #f00; }
                .error { color: #ff0; }
                .progress-container { width: 100%; background-color: #333; margin: 10px 0; }
                .progress-bar { height: 20px; background-color: #28a745; width: 0%; }
                .stats { margin: 10px 0; }
            </style>
            <script src="https://cdn.socket.io/4.5.0/socket.io.min.js"></script>
            <script>
                const socket = io();
                
                socket.on('update', function(data) {
                    // Update results list
                    const resultsDiv = document.getElementById('results');
                    const entry = document.createElement('div');
                    entry.className = data.status.includes('Valid') ? 'valid' : 
                                      data.status.includes('Invalid') ? 'invalid' : 'error';
                    entry.textContent = `${data.email} - ${data.status}`;
                    resultsDiv.appendChild(entry);
                    resultsDiv.scrollTop = resultsDiv.scrollHeight;
                    
                    // Update progress
                    document.getElementById('progress-bar').style.width = `${data.progress}%`;
                    document.getElementById('progress-text').textContent = `${data.progress}%`;
                    
                    // Update stats
                    document.getElementById('valid-count').textContent = data.valid_count;
                    document.getElementById('checked-count').textContent = Math.round(data.total * (data.progress/100));
                    document.getElementById('total-count').textContent = data.total;
                });
            </script>
        </head>
        <body>
            <h2>Live Results</h2>
            
            <div class="stats">
                <div>Valid Emails: <span id="valid-count">0</span></div>
                <div>Progress: <span id="checked-count">0</span>/<span id="total-count">0</span></div>
            </div>
            
            <div class="progress-container">
                <div id="progress-bar" class="progress-bar"></div>
            </div>
            <div id="progress-text" style="text-align: center;">0%</div>
            
            <div id="results"></div>
            
            <a href="/" style="color: #00ccff;">← Back to Home</a>
        </body>
        </html>
    ''')

def cli_entry():
    parser = argparse.ArgumentParser(description='Email Unmasker by developer.rs')
    parser.add_argument('-e', '--email', help='Masked email (e.g. r****r@gmail.com)')
    parser.add_argument('-t', '--threads', help='Threads count (default: 20)', type=int, default=20)
    parser.add_argument('--web', help='Launch web interface', action='store_true')
    args = parser.parse_args()

    if args.web:
        socketio.run(app, host='0.0.0.0', port=5000, debug=False)
    elif args.email:
        if not re.match(r'^[a-z0-9.*]+@[a-z]+\.[a-z]+$', args.email):
            console.print("[red]❌ Invalid email format[/red]")
            return
        run_verification(args.email.strip().lower(), args.threads)
    else:
        animated_banner()
        while True:
            masked = console.input("Enter masked email (e.g. r******s@gmail.com): ").strip().lower()
            if re.match(r'^[a-z0-9.*]+@[a-z]+\.[a-z]+$', masked):
                break
            console.print("[red]❌ Invalid email format[/red]")
        
        while True:
            try:
                threads = int(console.input("How many threads (requests per second)? (e.g. 50): "))
                if threads >= 1:
                    break
                console.print("[red]❌ Thread count must be at least 1[/red]")
            except ValueError:
                console.print("[red]❌ Invalid number[/red]")
        
        run_verification(masked, threads)

if __name__ == "__main__":
    cli_entry()