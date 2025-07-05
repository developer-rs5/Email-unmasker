#!/usr/bin/env python3

import re
import smtplib
import dns.resolver
import time
import os
import threading
from itertools import product
from concurrent.futures import ThreadPoolExecutor, as_completed
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.progress import Progress, BarColumn, TimeRemainingColumn, TextColumn
from pyfiglet import Figlet
import argparse
from flask import Flask, render_template_string, request, redirect, url_for, jsonify

# Configuration
CHARSET = 'abcdefghijklmnopqrstuvwxyz0123456789'
MAX_THREADS = 50
SMTP_TIMEOUT = 8
VALID_EMAILS_FILE = "results/valid-emails.txt"
MAX_DISPLAY_EMAILS = 20
SOCIAL_LINKS = {
    "Discord": "https://discord.zenuxs.xyz",
    "Instagram": "https://instagram.com/developer.rs",
    "GitHub": "https://github.com/developer-rs5"
}

console = Console()
app = Flask(__name__)

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

def is_valid_email(email):
    try:
        domain = email.split('@')[1]
        mx_record = dns.resolver.resolve(domain, 'MX')
        host = str(mx_record[0].exchange)

        server = smtplib.SMTP(host, 25, timeout=SMTP_TIMEOUT)
        server.helo()
        server.mail('check@example.com')
        code, _ = server.rcpt(email)
        server.quit()
        return code == 250
    except Exception:
        return False

def run_verification(masked, threads):
    global results_state
    results_state = {
        'emails': [],
        'valid_emails': [],
        'progress': 0,
        'total': 0,
        'running': True
    }
    
    os.makedirs("results", exist_ok=True)
    emails = list(generate_emails(masked))
    total = len(emails)
    results_state['total'] = total
    
    start_time = time.time()
    checked_count = 0
    valid_emails = set()

    with ThreadPoolExecutor(max_workers=min(threads, MAX_THREADS)) as executor:
        futures = {executor.submit(is_valid_email, email): email for email in emails}
        
        for future in as_completed(futures):
            email = futures[future]
            try:
                valid = future.result()
                status = "✅ Valid" if valid else "❌ Invalid"
                
                if valid:
                    valid_emails.add(email)
                    results_state['valid_emails'].append(email)
                
                results_state['emails'].append({'email': email, 'status': status})
                checked_count += 1
                progress_percent = int((checked_count / total) * 100)
                results_state['progress'] = progress_percent

            except Exception:
                results_state['emails'].append({'email': email, 'status': "⚠️ Error"})

    if valid_emails:
        with open(VALID_EMAILS_FILE, "w") as f:
            for email in sorted(valid_emails):
                f.write(email + "\n")
        console.print(Panel("\n".join(sorted(valid_emails)), title="✅ Valid Emails Found", border_style="green"))
    else:
        console.print(Panel("No valid emails found.", title="❌ Result", border_style="red"))
    
    results_state['running'] = False

@app.route('/')
def index():
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
            <form method="post" action="/start">
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

@app.route('/start', methods=['POST'])
def start_verification():
    masked = request.form['masked']
    threads = int(request.form['threads'])
    
    threading.Thread(
        target=run_verification,
        args=(masked, threads),
        daemon=True
    ).start()
    
    return redirect(url_for('results'))

@app.route('/results')
def results():
    return render_template_string('''
        <html>
        <head>
            <title>Results</title>
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
            <script>
                function fetchUpdates() {
                    fetch('/get-updates')
                        .then(response => response.json())
                        .then(data => {
                            // Update progress
                            document.getElementById('progress-bar').style.width = `${data.progress}%`;
                            document.getElementById('progress-text').textContent = `${data.progress}%`;
                            
                            // Update stats
                            document.getElementById('valid-count').textContent = data.valid_count;
                            document.getElementById('checked-count').textContent = Math.round(data.total * (data.progress/100));
                            document.getElementById('total-count').textContent = data.total;
                            
                            // Update results
                            const resultsDiv = document.getElementById('results');
                            resultsDiv.innerHTML = '';
                            data.emails.forEach(item => {
                                const entry = document.createElement('div');
                                entry.className = item.status.includes('Valid') ? 'valid' : 
                                                  item.status.includes('Invalid') ? 'invalid' : 'error';
                                entry.textContent = `${item.email} - ${item.status}`;
                                resultsDiv.appendChild(entry);
                            });
                            resultsDiv.scrollTop = resultsDiv.scrollHeight;
                            
                            // Continue polling if still running
                            if (data.progress < 100) {
                                setTimeout(fetchUpdates, 1000);
                            }
                        });
                }
                // Start polling when page loads
                window.onload = fetchUpdates;
            </script>
        </head>
        <body>
            <h2>Results</h2>
            
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

@app.route('/get-updates')
def get_updates():
    return jsonify({
        'emails': results_state['emails'][-MAX_DISPLAY_EMAILS:],
        'valid_count': len(results_state['valid_emails']),
        'progress': results_state['progress'],
        'total': results_state['total']
    })

def cli_entry():
    parser = argparse.ArgumentParser(description='Email Unmasker by developer.rs')
    parser.add_argument('-e', '--email', help='Masked email (e.g. r****r@gmail.com)')
    parser.add_argument('-t', '--threads', help='Threads count (default: 20)', type=int, default=20)
    parser.add_argument('--web', help='Launch web interface', action='store_true')
    args = parser.parse_args()

    if args.web:
        app.run(host='0.0.0.0', port=5000, debug=False)
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