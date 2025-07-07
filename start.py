import re
import dns.resolver
import time
import os
import socket
import threading
import smtplib
from collections import deque
from itertools import product
from concurrent.futures import ThreadPoolExecutor, as_completed
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.progress import Progress, BarColumn, TimeRemainingColumn, TextColumn
from rich.text import Text
from pyfiglet import Figlet
import argparse
from flask import Flask, render_template_string, request, redirect, url_for
from flask_socketio import SocketIO, emit

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
app.config['SECRET_KEY'] = 'secret!'
socketio = SocketIO(app, async_mode='threading', logger=False, engineio_logger=False)

# Shared state for web interface
results_state = {
    'emails': [],
    'valid_emails': [],
    'progress': 0,
    'total': 0,
    'running': False,
    'valid_count': 0,
    'error': None
}
state_lock = threading.Lock()

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

resolver = dns.resolver.Resolver()
resolver.nameservers = ['8.8.8.8', '1.1.1.1', '8.8.4.4']  # Google & Cloudflare DNS
resolver.timeout = 2
resolver.lifetime = 2

def is_valid_email(email):
    # Enhanced email format validation
    if not re.fullmatch(r"(^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$)", email):
        return False

    try:
        domain = email.split('@')[1].lower()

        # Skip reserved/fake TLDs
        if domain.endswith(('.test', '.invalid', '.example', '.local', '.localhost')):
            return False

        # Resolve MX records using custom resolver
        mx_records = resolver.resolve(domain, 'MX')
        return bool(mx_records)

    except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN, dns.resolver.Timeout):
        return False
    except Exception as e:
        return False

def smtp_verify(email):
    """Perform SMTP verification for email addresses"""
    try:
        domain = email.split('@')[1]
        records = dns.resolver.resolve(domain, 'MX')
        mx_record = str(records[0].exchange).rstrip('.')
        
        # Connect to SMTP server
        server = smtplib.SMTP(timeout=SMTP_TIMEOUT)
        server.connect(mx_record, 25)
        server.ehlo_or_helo_if_needed()
        
        # Check recipient
        server.mail('verify@example.com')
        code, _ = server.rcpt(email)
        server.quit()
        
        return code == 250  # 250 indicates valid recipient
    except Exception as e:
        return False

def update_web_interface(email, status, valid_count, progress, total):
    try:
        socketio.emit('update', {
            'email': email,
            'status': status,
            'valid_count': valid_count,
            'progress': progress,
            'total': total
        }, namespace='/', broadcast=True)
    except Exception as e:
        pass
         
def run_verification(masked, threads):
    global results_state
    try:
        with state_lock:
            results_state['running'] = True 
            results_state['emails'] = []
            results_state['valid_emails'] = []
            results_state['valid_count'] = 0
            results_state['error'] = None
        
        os.makedirs("results", exist_ok=True)
        emails = list(generate_emails(masked))
        total = len(emails)
        
        with state_lock:
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
        
        # Create main display using Group to avoid Panel substitution issue
        main_layout = Panel(
            Group(
                Panel(progress, title="Progress", border_style="green"),
                Panel(results_panel, title="Results", border_style="blue"),
            ),
            title="Email Unmasker", 
            border_style="bold magenta"
        )
        
        with Live(main_layout, refresh_per_second=4, console=console) as live:
            with ThreadPoolExecutor(max_workers=min(threads, MAX_THREADS)) as executor:
                futures = {executor.submit(is_valid_email, email): email for email in emails}
                
                for future in as_completed(futures):
                    email = futures[future]
                    if email in seen_emails: 
                        continue
                    seen_emails.add(email)

                    try:
                        valid = future.result()
                        smtp_status = ""
                        
                        if valid:
                            domain = email.split('@')[1].lower()
                            if any(domain.endswith(d) for d in UNVERIFIABLE_DOMAINS):
                                # Skip SMTP for unverifiable domains
                                smtp_status = " (DNS)"
                            else:
                                # Perform SMTP verification
                                if smtp_verify(email):
                                    smtp_status = " (SMTP)"
                                else:
                                    valid = False
                                    smtp_status = " (SMTP Failed)"
                        
                        status = "✅ Valid" + smtp_status if valid else "❌ Invalid"
                        color = "green" if valid else "red"
                        
                        # Update results display
                        last_results.appendleft(f"[{color}]{email} - {status}[/]")
                        results_display = Text("\n".join(last_results), no_wrap=True)
                        results_panel.renderable = results_display
                        
                        # Update state
                        with state_lock:
                            if valid:
                                valid_emails.add(email)
                                results_state['valid_emails'].append(email)
                                results_state['valid_count'] = len(valid_emails)
                            
                            checked_count += 1
                            progress_percent = min(100, int((checked_count / total) * 100))
                            results_state['progress'] = progress_percent
                            results_state['emails'].append({'email': email, 'status': status})
                        
                        # Update web interface
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
                        with state_lock:
                            checked_count += 1
                            progress_percent = min(100, int((checked_count / total) * 100))
                            results_state['progress'] = progress_percent
                            results_state['emails'].append({'email': email, 'status': "⚠️ Error"})
                        update_web_interface(email, "⚠️ Error", len(valid_emails), progress_percent, total)
                        progress.update(task, advance=1)
                        live.update(main_layout)

        if valid_emails:
            with open(VALID_EMAILS_FILE, "w") as f:
                for email in sorted(valid_emails):
                    f.write(email + "\n")
            box = "\n".join(sorted(valid_emails))
            console.print(Panel(box, title="✅ Valid Emails Found", border_style="green"))
            console.print(f"[green]Saved to {VALID_EMAILS_FILE}[/green]")
        else:
            console.print(Panel("No valid emails found.", title="❌ Result", border_style="red"))
        
        console.print(f"[cyan]Total time: {time.time() - start_time:.2f} seconds[/cyan]")

    except Exception as e:
        console.print(f"[red]Error in verification process: {str(e)}[/red]")
        with state_lock:
            results_state['error'] = str(e)
    finally:
        with state_lock:
            results_state['running'] = False

@app.route('/', methods=['GET', 'POST'])
def index():
    with state_lock:
        if results_state['running']:
            return render_template_string('''
                <html>
                <head>
                    <title>Email Unmasker</title>
                    <style>
                        body { font-family: Arial, sans-serif; background: #1a1a1a; color: #f0f0f0; text-align: center; padding: 50px; }
                        .message { background: #222; padding: 30px; border-radius: 10px; max-width: 600px; margin: 0 auto; }
                        a { color: #00ccff; text-decoration: none; }
                    </style>
                </head>
                <body>
                    <div class="message">
                        <h2>Verification in Progress</h2>
                        <p>Please wait while we verify email addresses...</p>
                        <p><a href="/live-results">View Live Results</a></p>
                        <p><a href="/">Back to Home</a></p>
                    </div>
                </body>
                </html>
            ''')
    
    if request.method == 'POST':
        masked = request.form['masked'].strip().lower()
        threads = int(request.form['threads'])
        
        # Validate input
        if not re.match(r'^[a-z0-9.*]+@[a-z]+\.[a-z]+$', masked):
            return render_template_string('''
                <html>
                <head>
                    <title>Error</title>
                    <style>body { font-family: Arial; background: #1a1a1a; color: #f0f0f0; text-align: center; padding: 50px; }</style>
                </head>
                <body>
                    <h2 style="color: #ff5555;">Invalid Email Format</h2>
                    <p>Please use format like: r****r@gmail.com</p>
                    <p><a href="/">Try Again</a></p>
                </body>
                </html>
            ''', 400)
        
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
                body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background: #121212; color: #e0e0e0; text-align: center; }
                .container { max-width: 800px; margin: 40px auto; padding: 30px; background: #1e1e1e; border-radius: 15px; box-shadow: 0 0 20px rgba(0,0,0,0.5); }
                h1 { color: #00ffcc; margin-bottom: 30px; font-size: 2.5rem; text-shadow: 0 0 10px rgba(0,255,204,0.3); }
                .card { background: #252525; border-radius: 10px; padding: 25px; margin-bottom: 25px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); }
                input, button { padding: 14px; margin: 15px; font-size: 1.1em; border-radius: 8px; transition: all 0.3s; }
                input { width: 80%; background: #333; color: white; border: 2px solid #444; }
                input:focus { border-color: #00ffcc; outline: none; box-shadow: 0 0 10px rgba(0,255,204,0.3); }
                button { background: linear-gradient(to right, #00cc99, #00ccff); color: white; border: none; cursor: pointer; font-weight: bold; width: 250px; }
                button:hover { transform: translateY(-3px); box-shadow: 0 7px 14px rgba(0,255,204,0.4); }
                .info { background: #1a2a2a; border-left: 4px solid #00ffcc; padding: 15px; text-align: left; margin: 20px 0; }
                .social-links { display: flex; justify-content: center; gap: 20px; margin: 30px 0; }
                .social-links a { display: inline-block; padding: 12px 25px; background: #2a2a2a; border-radius: 30px; transition: all 0.3s; }
                .social-links a:hover { background: #00cc99; transform: translateY(-3px); }
                footer { margin-top: 30px; color: #777; font-size: 0.9em; }
                .logo { font-size: 1.2em; color: #00ffcc; margin-bottom: 10px; }
                .input-group { margin: 25px 0; }
                label { display: block; margin-bottom: 10px; font-size: 1.1em; color: #00ffcc; }
            </style>
        </head>
        <body>
            <div class="container">
                <div class="logo">EMAIL UNMASKER</div>
                <h1>Find Hidden Email Addresses</h1>
                
                <div class="card">
                    <form method="post">
                        <div class="input-group">
                            <label for="masked">Masked Email Pattern</label>
                            <input type="text" id="masked" name="masked" placeholder="r****r@gmail.com" required>
                        </div>
                        
                        <div class="input-group">
                            <label for="threads">Verification Threads (1-500)</label>
                            <input type="number" id="threads" name="threads" value="50" min="1" max="500" required>
                        </div>
                        
                        <button type="submit">Start Verification</button>
                    </form>
                </div>
                
                <div class="info">
                    <p><strong>How to use:</strong> Replace unknown characters with asterisks (*)</p>
                    <p><strong>Example:</strong> j****n@example.com will generate all combinations like jason@example.com, jaden@example.com, etc.</p>
                    <p><strong>Note:</strong> Higher thread counts may cause performance issues. Recommended: 50-100 threads.</p>
                </div>
                
                <div class="social-links">
                    <a href="https://discord.zenuxs.xyz" target="_blank">Discord</a>
                    <a href="https://instagram.com/developer.rs" target="_blank">Instagram</a>
                    <a href="https://github.com/developer-rs5" target="_blank">GitHub</a>
                </div>
                
                <footer>
                    <p>Developed by <strong>developer.rs</strong> | DNS + SMTP Verification | CLI + Web Interface</p>
                </footer>
            </div>
        </body>
        </html>
    ''')

@app.route('/live-results')
def live_results():
    with state_lock:
        if not results_state['running']:
            if results_state.get('error'):
                return render_template_string('''
                    <div style="color: red; padding: 20px;">
                        <h2>Verification Failed</h2>
                        <p>{{ error }}</p>
                        <a href="/">Back to Home</a>
                    </div>
                ''', error=results_state['error'])
            return redirect(url_for('index'))
            
    return render_template_string('''
        <html>
        <head>
            <title>Live Results</title>
            <style>
                body { font-family: 'Consolas', monospace; background: #0d1117; color: #c9d1d9; padding: 20px; }
                .header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 25px; }
                .title { font-size: 1.8rem; color: #58a6ff; text-shadow: 0 0 10px rgba(88,166,255,0.3); }
                .stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 15px; margin-bottom: 25px; }
                .stat-card { background: #161b22; border-radius: 8px; padding: 15px; border: 1px solid #30363d; }
                .stat-value { font-size: 1.8rem; font-weight: bold; color: #3fb950; }
                .stat-label { color: #8b949e; margin-top: 5px; }
                .progress-container { background: #161b22; border-radius: 8px; padding: 20px; margin-bottom: 25px; border: 1px solid #30363d; }
                .progress-bar { height: 25px; background: #0d2b19; border-radius: 4px; overflow: hidden; margin-top: 15px; }
                .progress-fill { height: 100%; background: linear-gradient(to right, #238636, #3fb950); width: 0%; transition: width 0.5s; }
                .progress-text { text-align: center; margin-top: 10px; font-size: 1.2rem; color: #8b949e; }
                .results-container { background: #161b22; border-radius: 8px; padding: 20px; border: 1px solid #30363d; height: 60vh; overflow-y: auto; }
                .result-item { padding: 12px; border-bottom: 1px solid #21262d; font-family: 'Courier New', monospace; }
                .result-valid { color: #3fb950; }
                .result-invalid { color: #f85149; }
                .result-error { color: #d29922; }
                .back-btn { display: inline-block; padding: 10px 20px; background: #1f6feb; border-radius: 6px; color: white; text-decoration: none; margin-top: 25px; transition: all 0.3s; }
                .back-btn:hover { background: #2a7aef; transform: translateY(-3px); }
            </style>
            <script src="https://cdn.socket.io/4.5.0/socket.io.min.js"></script>
            <script>
                document.addEventListener('DOMContentLoaded', function() {
                    const socket = io();
                    
                    // Initialize with current state
                    fetch('/current-state')
                        .then(response => response.json())
                        .then(data => {
                            if (data.running) {
                                document.getElementById('valid-count').textContent = data.valid_count;
                                const checked = Math.round(data.total * (data.progress/100));
                                document.getElementById('checked-count').textContent = checked;
                                document.getElementById('total-count').textContent = data.total;
                                document.getElementById('progress-fill').style.width = `${data.progress}%`;
                                document.getElementById('progress-text').textContent = `${Math.round(data.progress)}% Complete`;
                                
                                // Display existing results
                                const resultsDiv = document.getElementById('results');
                                data.emails.forEach(item => {
                                    const entry = document.createElement('div');
                                    entry.className = 'result-item';
                                    
                                    if (item.status.includes('Valid')) {
                                        entry.classList.add('result-valid');
                                    } else if (item.status.includes('Invalid')) {
                                        entry.classList.add('result-invalid');
                                    } else {
                                        entry.classList.add('result-error');
                                    }
                                    
                                    entry.textContent = `${item.email} - ${item.status}`;
                                    resultsDiv.appendChild(entry);
                                });
                                resultsDiv.scrollTop = resultsDiv.scrollHeight;
                            }
                        });
                    
                    socket.on('update', function(data) {
                        // Update stats
                        document.getElementById('valid-count').textContent = data.valid_count;
                        const checked = Math.round(data.total * (data.progress/100));
                        document.getElementById('checked-count').textContent = checked;
                        document.getElementById('total-count').textContent = data.total;
                        
                        // Update progress bar
                        document.getElementById('progress-fill').style.width = `${data.progress}%`;
                        document.getElementById('progress-text').textContent = `${Math.round(data.progress)}% Complete`;
                        
                        // Add new result
                        const resultsDiv = document.getElementById('results');
                        const entry = document.createElement('div');
                        entry.className = 'result-item';
                        
                        if (data.status.includes('Valid')) {
                            entry.classList.add('result-valid');
                        } else if (data.status.includes('Invalid')) {
                            entry.classList.add('result-invalid');
                        } else {
                            entry.classList.add('result-error');
                        }
                        
                        entry.textContent = `${data.email} - ${data.status}`;
                        resultsDiv.appendChild(entry);
                        resultsDiv.scrollTop = resultsDiv.scrollHeight;
                    });
                });
            </script>
        </head>
        <body>
            <div class="header">
                <h1 class="title">Live Verification Results</h1>
                <a href="/" class="back-btn">Back to Home</a>
            </div>
            
            <div class="stats">
                <div class="stat-card">
                    <div class="stat-value" id="valid-count">0</div>
                    <div class="stat-label">Valid Emails</div>
                </div>
                <div class="stat-card">
                    <div class="stat-value" id="checked-count">0</div>
                    <div class="stat-label">Emails Checked</div>
                </div>
                <div class="stat-card">
                    <div class="stat-value" id="total-count">0</div>
                    <div class="stat-label">Total Emails</div>
                </div>
            </div>
            
            <div class="progress-container">
                <h3>Verification Progress</h3>
                <div class="progress-bar">
                    <div class="progress-fill" id="progress-fill"></div>
                </div>
                <div class="progress-text" id="progress-text">0% Complete</div>
            </div>
            
            <div class="results-container" id="results"></div>
        </body>
        </html>
    ''')

@app.route('/current-state')
def current_state():
    with state_lock:
        return {
            'running': results_state['running'],
            'emails': results_state['emails'],
            'valid_count': results_state['valid_count'],
            'progress': results_state['progress'],
            'total': results_state['total'],
            'error': results_state.get('error')
        }

def cli_entry():
    parser = argparse.ArgumentParser(description='Email Unmasker by developer.rs')
    parser.add_argument('-e', '--email', help='Masked email (e.g. r****r@gmail.com)')
    parser.add_argument('-t', '--threads', help='Threads count (default: 50)', type=int, default=50)
    parser.add_argument('--web', help='Launch web interface', action='store_true')
    args = parser.parse_args()

    if args.web:
        console.print("\n[bold green]Starting web server on http://localhost:5000[/bold green]")
        console.print("[bold yellow]Press Ctrl+C to exit[/bold yellow]\n")
        socketio.run(app, host='0.0.0.0', port=5000, debug=False, allow_unsafe_werkzeug=True)
    elif args.email:
        if not re.match(r'^[a-z0-9.*]+@[a-z]+\.[a-z]+$', args.email):
            console.print("[red]❌ Invalid email format. Use format like: r****r@gmail.com[/red]")
            return
        run_verification(args.email.strip().lower(), args.threads)
    else:
        animated_banner()
        while True:
            masked = console.input("[bold cyan]Enter masked email (e.g. r******s@gmail.com): [/bold]").strip().lower()
            if re.match(r'^[a-z0-9.*]+@[a-z]+\.[a-z]+$', masked):
                break
            console.print("[red]❌ Invalid email format. Use format like: r****r@gmail.com[/red]")
        
        while True:
            try:
                threads = int(console.input("[bold cyan]Threads (1-500): [/bold]"))
                if 1 <= threads <= 500:
                    break
                console.print("[red]❌ Thread count must be between 1-500[/red]")
            except ValueError:
                console.print("[red]❌ Invalid number[/red]")
        
        run_verification(masked, threads)

if __name__ == "__main__":
    cli_entry()